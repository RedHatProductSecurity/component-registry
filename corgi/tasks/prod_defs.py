import logging
import re

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

logger = logging.getLogger(__name__)
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
    TODO: investigate whether we need to update_or_create ProductNodes
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
                version="",
                description=description,
                defaults={
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
                        version=version,
                        description="",
                        defaults={
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

                    if len(brew_tags) > 0:
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
                                        version="",
                                        description="",
                                        defaults={
                                            "meta_attr": {
                                                "et_product": variant_product_version.product.name,
                                                "et_product_version": variant_product_version.name,
                                            }
                                        },
                                    )
                                    ProductNode.objects.get_or_create(
                                        object_id=product_variant.pk,
                                        defaults={
                                            "parent": product_stream_node,
                                            "obj": product_variant,
                                        },
                                    )
                    for et_product in errata_info:
                        et_product_name = et_product.pop("product_name")
                        et_product_versions = et_product.pop("product_versions")

                        for et_product_version in et_product_versions:
                            et_pv_name = et_product_version["name"]
                            product_stream.et_product_versions.append(et_pv_name)

                            for variant in et_product_version["variants"]:
                                logger.debug(
                                    "Creating or updating Product Variant: name=%s", variant
                                )
                                product_variant, _ = ProductVariant.objects.update_or_create(
                                    name=variant,
                                    version="",
                                    description="",
                                    defaults={
                                        "meta_attr": {
                                            "et_product": et_product_name,
                                            "et_product_version": et_pv_name,
                                        }
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
                        product_stream.save()

    with transaction.atomic():
        # Note - Once product taxonomy has been fully loaded we can materialise
        # relationships in product entities.
        for product_variant in ProductVariant.objects.get_queryset():
            product_variant.save_product_taxonomy()
        for product_stream in ProductStream.objects.get_queryset():
            product_stream.save_product_taxonomy()
        for product_version in ProductVersion.objects.get_queryset():
            product_version.save_product_taxonomy()
        for product in Product.objects.get_queryset():
            product.save_product_taxonomy()
