import re

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.db import transaction

from config.celery import app
from corgi.collectors.models import CollectorErrataProductVersion
from corgi.collectors.prod_defs import ProdDefs
from corgi.core.models import (
    Product,
    ProductNode,
    ProductStream,
    ProductVariant,
    ProductVersion,
)
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = get_task_logger(__name__)
# Find a substring that looks like a version (e.g. "3", "3.5", "3-5", "1.2.z") at the end of a
# searched string.
RE_VERSION_LIKE_STRING = re.compile(r"\d[\dz.-]*$|$")


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
                    active = pd_product_stream.pop("active")
                    errata_info = pd_product_stream.pop("errata_info", [])
                    brew_tags = pd_product_stream.pop("brew_tags", [])
                    yum_repos = pd_product_stream.pop("yum_repositories", [])
                    composes = pd_product_stream.pop("composes", [])

                    brew_tags_dict = {}
                    for brew_tag in brew_tags:
                        brew_tags_dict[brew_tag["tag"]] = brew_tag["inherit"]

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
                    # quay-3 Errata Tool product version Quay-3-RHEL-8 list too many brew tags
                    # Linking quay streams to the 8Base-Quay-3 variant here via brew tags leads
                    # to builds from later streams being included in earlier ones,
                    # see PROJQUAY-5312.
                    # The rhn_satellite_6 streams have brew tags, but those brew tags are associated
                    # with the RHEL-7-SATELLITE-6.10 ET Product Version. We skip them
                    # here to ensure the rhn_satelite_6.10 variants only get linked with that stream
                    # I haven't filed a product bug to get them fixed since 6.7 - 6.9 are no longer
                    # active. See CORGI-546 for more details.
                    if (
                        len(brew_tags) > 0
                        and product_version.name != "quay-3"
                        and name
                        not in ["rhn_satellite_6.7", "rhn_satellite_6.8", "rhn_satellite_6.9"]
                    ):
                        #    continue
                        logger.debug(
                            "Found brew tags (%s) in product stream: %s",
                            brew_tags,
                            product_stream.name,
                        )
                        for brew_tag in brew_tags:
                            # Also match brew tags in prod_defs with those from ET
                            trimmed_brew_tag = brew_tag["tag"].removesuffix("-released")
                            et_pvs = CollectorErrataProductVersion.objects.filter(
                                brew_tags__contains=[trimmed_brew_tag]
                            )

                            for et_pv in et_pvs:
                                logger.debug(
                                    "Found Product Version (%s) in ET matching brew tag %s",
                                    et_pv.name,
                                    brew_tag,
                                )
                                for et_variant in et_pv.variants.get_queryset():
                                    logger.info(
                                        "Assigning Variant %s to product stream %s",
                                        et_variant.name,
                                        product_stream.name,
                                    )
                                    variant_product_version: CollectorErrataProductVersion = (
                                        et_variant.product_version
                                    )
                                    product_variant, _ = ProductVariant.objects.update_or_create(
                                        name=et_variant.name,
                                        defaults={
                                            "version": "",
                                            "description": "",
                                            "products": product,
                                            "productversions": product_version,
                                            "productstreams": product_stream,
                                            "meta_attr": {
                                                "et_product": variant_product_version.product.name,
                                                "et_product_version": variant_product_version.name,
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
                                    product_variant.save_product_taxonomy()
                    et_product_versions_set = set(product_stream.et_product_versions)
                    for et_product in errata_info:
                        et_product_name = et_product.pop("product_name")
                        et_product_versions = et_product.pop("product_versions")

                        for et_product_version in et_product_versions:
                            et_pv_name = et_product_version["name"]
                            et_product_versions_set.add(et_pv_name)

                            for variant in et_product_version["variants"]:
                                logger.debug(
                                    "Creating or updating Product Variant: name=%s", variant
                                )
                                product_variant, _ = ProductVariant.objects.update_or_create(
                                    name=variant,
                                    defaults={
                                        "version": "",
                                        "description": "",
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
                                product_variant.save_product_taxonomy()
                    # persist et_product_versions plucked from errata_info
                    product_stream.et_product_versions = sorted(et_product_versions_set)
                    product_stream.save()

                    # Save taxonomies for newly-created model at end of each loop iteration
                    # All child models + nodes have already been created and had taxonomies saved
                    # This should be a no-op since we link models directly upon creation above
                    # Keeping it here just to be safe, but we could remove in future
                    product_stream.save_product_taxonomy()
                product_version.save_product_taxonomy()
            product.save_product_taxonomy()
