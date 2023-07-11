import re
from typing import Any, Optional

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.db import transaction

from config.celery import app
from corgi.collectors.models import (
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)
from corgi.collectors.prod_defs import ProdDefs
from corgi.core.models import (
    Component,
    Product,
    ProductNode,
    ProductStream,
    ProductVariant,
    ProductVersion,
    SoftwareBuild,
)
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = get_task_logger(__name__)
# Find a substring that looks like a version (e.g. "3", "3.5", "3-5", "1.2.z") at the end of a
# searched string.
RE_VERSION_LIKE_STRING = re.compile(r"\d[\dz.-]*$|$")


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_update_builds_for_variant(
    variant_name: str,
    updated_stream: tuple[str, str],
    updated_version: Optional[tuple[str, str]] = None,
    updated_product: Optional[tuple[str, str]] = None,
):
    """Ensures new stream parent of the passed in variant is reflected in all the variant's builds
    and child components"""
    logger.info(
        f"Updating components related to {variant_name} with new stream details: {updated_stream}"
    )
    updated_stream_objs = (
        ProductStream.objects.get(name=updated_stream[0]),
        ProductStream.objects.get(name=updated_stream[1]),
    )
    updated_version_objs = None
    updated_product_objs = None
    if updated_version:
        logger.info(f"Also updating product_version with {updated_version}")
        updated_version_objs = (
            ProductVersion.objects.get(name=updated_version[0]),
            ProductVersion.objects.get(name=updated_version[1]),
        )
    if updated_product:
        logger.info(f"Also updating product {updated_product}")
        updated_product_objs = (
            Product.objects.get(name=updated_product[0]),
            Product.objects.get(name=updated_product[1]),
        )
    for software_build in (
        SoftwareBuild.objects.filter(relations__product_ref=variant_name).distinct().iterator()
    ):
        component = software_build.components.get()
        _update_product_streams(
            component, updated_stream_objs, updated_version_objs, updated_product_objs
        )
        for cnode in component.cnodes.get_queryset().iterator():
            for d in cnode.get_descendants().iterator():
                _update_product_streams(
                    d.obj, updated_stream_objs, updated_version_objs, updated_product_objs
                )


def _update_product_streams(
    component: Component,
    updated_stream: tuple[ProductStream, ProductStream],
    updated_version: Optional[tuple[ProductVersion, ProductVersion]] = None,
    updated_product: Optional[tuple[Product, Product]] = None,
):
    component.productstreams.add(updated_stream[0])
    component.productstreams.remove(updated_stream[1])
    if updated_version:
        component.productversions.add(updated_version[0])
        component.productversions.remove(updated_version[1])
    if updated_product:
        component.products.add(updated_product[0])
        component.products.remove(updated_product[1])


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def update_products() -> None:
    """Fetch product definitions and update the entire product data and tree model.

    Data from prod-defs is stored into these models:
    "ps_product" -> Product
    "ps_module -> ProductVersion
    "ps_update_stream -> ProductStream
    Errata Tool Variants configured for a specific "ps_update_stream" -> ProductVariant

    Each of these models is then set up as a ProductNode with its child nodes in the same order
    as noted above.

    TODO: investigate whether we need to delete removed definitions from prod-defs.
    """
    products = ProdDefs.load_product_definitions()
    with transaction.atomic():
        for pd_product in products:
            pd_product_versions = pd_product.pop("product_versions", [])

            name = pd_product.pop("id")
            description = pd_product.pop("name")

            logger.debug("Creating or updating Product: name=%s, description=%s", name, description)
            product, _ = Product.objects.update_or_create(
                name=name,
                defaults={
                    "version": "",
                    "description": description,
                    "lifecycle_url": pd_product.pop("lifecycle_url", ""),
                    "meta_attr": pd_product,
                },
            )
            product_node, _ = ProductNode.objects.get_or_create(
                object_id=product.pk, defaults={"parent": None, "obj": product}
            )

            for pd_product_version in pd_product_versions:
                parse_product_version(pd_product_version, product, product_node)


def parse_product_version(
    pd_product_version: dict[str, Any], product: Product, product_node: ProductNode
):
    """Parse the product versions from ps_modules in product-definitions.json"""
    pd_product_streams = pd_product_version.pop("product_streams", [])
    name = pd_product_version.pop("id")
    if match_version := RE_VERSION_LIKE_STRING.search(name):
        version = match_version.group()
    description = pd_product_version.pop("public_description", [])
    logger.debug(
        "Creating or updating Product Version: name=%s, description=%s",
        name,
        description,
    )
    product_version, _ = ProductVersion.objects.update_or_create(
        name=name,
        defaults={
            "version": version,
            "description": description,
            "products": product,
            "meta_attr": pd_product_version,
        },
    )
    product_version_node, _ = ProductNode.objects.get_or_create(
        object_id=product_version.pk,
        defaults={
            "parent": product_node,
            "obj": product_version,
        },
    )
    for pd_product_stream in pd_product_streams:
        parse_product_stream(
            pd_product_stream, product, product_version, product_version_node, version
        )


def parse_product_stream(
    pd_product_stream: dict[str, Any],
    product: Product,
    product_version: ProductVersion,
    product_version_node: ProductNode,
    version: str,
):
    """Parse the product streams from ps_update_streams in product-definitions.json"""
    active = pd_product_stream.pop("active")
    errata_info = pd_product_stream.pop("errata_info", [])
    brew_tags = pd_product_stream.pop("brew_tags", [])
    yum_repos = pd_product_stream.pop("yum_repositories", [])
    composes = pd_product_stream.pop("composes", [])
    brew_tags_dict = {brew_tag["tag"]: brew_tag["inherit"] for brew_tag in brew_tags}
    composes_dict = {}
    for compose in composes:
        composes_dict[compose["url"]] = compose["variants"]
    name = pd_product_stream.pop("id")
    if match_version := RE_VERSION_LIKE_STRING.search(name):
        version = match_version.group()
    logger.debug("Creating or updating Product Stream: name=%s", name)
    product_stream, _ = ProductStream.objects.update_or_create(
        name=name,
        defaults={
            "version": version,
            "description": "",
            "products": product,
            "productversions": product_version,
            "active": active,
            "brew_tags": brew_tags_dict,
            "meta_attr": pd_product_stream,
            "yum_repositories": yum_repos,
            "composes": composes_dict,
        },
    )
    product_stream_node, _ = ProductNode.objects.get_or_create(
        object_id=product_stream.pk,
        defaults={
            "parent": product_version_node,
            "obj": product_stream,
        },
    )
    parse_variants_from_brew_tags(
        brew_tags_dict, name, product, product_stream, product_stream_node, product_version
    )
    parse_errata_info(errata_info, product, product_stream, product_stream_node, product_version)


def parse_variants_from_brew_tags(
    brew_tags: dict[str, bool],
    stream_name: str,
    product: Product,
    product_stream: ProductStream,
    product_stream_node: ProductNode,
    product_version: ProductVersion,
):
    """Match streams using brew_tags to Errata Tool Product Versions and their Variants"""
    # quay-3 Errata Tool product version Quay-3-RHEL-8 list too many brew tags
    # Linking quay streams to the 8Base-Quay-3 variant here via brew tags leads
    # to builds from later streams being included in earlier ones,
    # see PROJQUAY-5312.
    # The rhn_satellite_6 streams have brew tags, but those brew tags are associated
    # with the RHEL-7-SATELLITE-6.10 ET Product Version. We skip them
    # here to ensure the rhn_satelite_6.10 variants only get linked with that stream
    # I haven't filed a product bug to get them fixed since 6.7 - 6.9 are no longer
    # active. See CORGI-546 for more details.
    # Also skip brew_tag matching for dts ps_module which share Variants/Brew Tags with rhscl
    # See CORGI-726
    if (
        len(brew_tags) > 0
        and product_version.name != "quay-3"
        and not product_version.name.startswith("dts-")
        and stream_name not in ("rhn_satellite_6.7", "rhn_satellite_6.8", "rhn_satellite_6.9")
    ):
        logger.debug(
            "Found brew tags (%s) in product stream: %s",
            brew_tags,
            product_stream.name,
        )
        for brew_tag in brew_tags.keys():
            # Also match brew tags in prod_defs with those from ET
            trimmed_brew_tag = brew_tag.removesuffix("-released")
            et_pvs = CollectorErrataProductVersion.objects.filter(
                brew_tags__contains=[trimmed_brew_tag]
            )

            for et_pv in et_pvs:
                logger.debug(
                    "Found Product Version (%s) in ET matching brew tag %s",
                    et_pv.name,
                    brew_tag,
                )
                for et_variant in et_pv.variants.all():
                    logger.info(
                        "Assigning Variant %s to product stream %s",
                        et_variant.name,
                        product_stream.name,
                    )
                    variant_product_version: CollectorErrataProductVersion = (
                        et_variant.product_version
                    )
                    # We don't use update_or_create here in order to get the existing product detail
                    # of the variant. We then clear the existing product details from all associated
                    # builds and there components before adding the new product details.
                    variant_created = False
                    try:
                        product_variant = ProductVariant.objects.get(name=et_variant.name)
                        product_variant.cpe = et_variant.cpe
                        product_variant.meta_attr = {
                            "et_product": variant_product_version.product.name,
                            "et_product_version": variant_product_version.name,
                        }
                        product_variant.save()
                    except ProductVariant.DoesNotExist:
                        product_variant = ProductVariant.objects.create(
                            name=et_variant.name,
                            cpe=et_variant.cpe,
                            products=product,
                            productversions=product_version,
                            productstreams=product_stream,
                            meta_attr={
                                "et_product": variant_product_version.product.name,
                                "et_product_version": variant_product_version.name,
                            },
                        )
                        variant_created = True

                    _, node_created = ProductNode.objects.update_or_create(
                        object_id=product_variant.pk,
                        defaults={
                            "parent": product_stream_node,
                            "obj": product_variant,
                        },
                    )
                    if node_created and variant_created:
                        # This is a new Variant, so no need to update anything
                        continue

                    # If there was an existing product variant, and it was updated
                    # to now be associated with a different stream we need to
                    # update all the builds to reflect the new product relationships
                    # Capture all the old stream details before updating the foreign keys
                    existing_product_name = product_variant.products.name
                    existing_version_name = product_variant.productversions.name
                    existing_stream_name = product_variant.productstreams.name
                    product_variant.products = product
                    product_variant.productversions = product_version
                    product_variant.productstreams = product_stream
                    product_variant.save()

                    changed_products = None
                    changed_versions = None
                    if existing_product_name != product.name:
                        changed_products = (
                            product.name,
                            existing_product_name,
                        )
                    if existing_version_name != product_version.name:
                        changed_versions = (
                            product_version.name,
                            existing_version_name,
                        )
                    slow_update_builds_for_variant.apply_async(
                        args=(
                            # Variant name is always present
                            product_variant.name,
                            # New and old stream names are always present
                            (
                                product_stream.name,
                                existing_stream_name,
                            ),
                            # New and old version names might not be present
                            changed_versions,
                            # New and old product names might not be present
                            changed_products,
                        ),
                        kwargs={"countdown": 300},
                    )


def parse_errata_info(
    errata_info: list[dict],
    product: Product,
    product_stream: ProductStream,
    product_stream_node: ProductNode,
    product_version: ProductVersion,
):
    """Parse and create ProductVariants from errata_info in product-definitions.json"""
    et_product_versions_set = set(product_stream.et_product_versions)
    for et_product in errata_info:
        et_product_name = et_product.pop("product_name")
        et_product_versions = et_product.pop("product_versions")

        for et_product_version in et_product_versions:
            et_pv_name = et_product_version["name"]
            et_product_versions_set.add(et_pv_name)

            for variant in et_product_version["variants"]:
                logger.debug("Creating or updating Product Variant: name=%s", variant)
                et_variant_cpe = (
                    CollectorErrataProductVariant.objects.filter(name=variant)
                    .values_list("cpe", flat=True)
                    .first()
                )

                product_variant, _ = ProductVariant.objects.update_or_create(
                    name=variant,
                    defaults={
                        "version": "",
                        "description": "",
                        "cpe": et_variant_cpe if et_variant_cpe else "",
                        "products": product,
                        "productversions": product_version,
                        "productstreams": product_stream,
                        "meta_attr": {
                            "et_product": et_product_name,
                            "et_product_version": et_pv_name,
                        },
                    },
                )
                ProductNode.objects.get_or_create(
                    object_id=product_variant.pk,
                    defaults={
                        "parent": product_stream_node,
                        "obj": product_variant,
                    },
                )
    # persist et_product_versions plucked from errata_info
    product_stream.et_product_versions = sorted(et_product_versions_set)
    product_stream.save()
