import re
from collections import defaultdict
from typing import Any

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.db import transaction
from django.db.models import Q, QuerySet

from config.celery import app
from corgi.collectors.models import (
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)
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
                parse_product_version(pd_product_version, product, product_node)


def _find_by_cpe(cpe_patterns: list[str]) -> list[str]:
    """Given a list of CPE patterns find all the CPEs from ET Variants which match it"""
    if not cpe_patterns:
        return []

    regex_query = Q()
    for pattern in cpe_patterns:
        regex = pattern.replace(".", "\\.").replace("*", ".*")
        regex_query |= Q(cpe__regex=regex)

    return list(
        CollectorErrataProductVariant.objects.filter(regex_query).values_list("cpe", flat=True)
    )


def _match_stream_version_to_cpe_version(
    version_streams: QuerySet[ProductStream],
    cpe_version: str,
    cpe: str,
    el_by_cpe: defaultdict[str, list],
    cpes_by_stream: defaultdict[str, list],
):
    """Iterate product stream versions, looking for matches to the cpe_version passed in.
    If a match is found, store it in cpes_by_streams dict along with any el suffixes."""
    cpe_version_with_z = f"{cpe_version}.z"
    for version_stream_name in version_streams.filter(
        Q(version=cpe_version) | Q(version=cpe_version_with_z)
    ).values_list("name", flat=True):
        if cpe in el_by_cpe:
            for el in el_by_cpe[cpe]:
                cpes_by_stream[version_stream_name].append(cpe + "::" + el)
        else:
            cpes_by_stream[version_stream_name].append(cpe)


def _split_cpe_version(cpe_part: str, versions_by_cpe: defaultdict[str, set]):
    version_split = cpe_part.rsplit(":", 1)
    if len(version_split) != 2:
        logger.warning(f"Didn't find ':' in cpe_part: {cpe_part}")
        return
    cpe_name = version_split[0]
    cpe_version = version_split[1]
    versions_by_cpe[cpe_name].add(cpe_version)


def _split_cpe(cpe: str, versions_by_cpe: defaultdict[str, set], el_by_cpe: defaultdict[str, list]):
    """Splits a cpe such as 'cpe:/a:redhat:openshift_ironic:4.13:el9' into
    'cpe:/a:redhat:openshift_ironic', '4.13', and 'el9'
    It builds 2 dictionaries, one with a set of versions grouped by cpe_name and another
    with el suffixes grouped by 'cpe_name + version'.
    We do this so that we can iterate versions looking for matches without el suffixes. Once
    we find a match, we add back the suffixes"""
    variant_split = cpe.rsplit("::")
    if len(variant_split) == 2:
        el_part = variant_split[1]
        cpe_part = variant_split[0]
        el_by_cpe[cpe_part].append(el_part)
        _split_cpe_version(cpe_part, versions_by_cpe)
    elif len(variant_split) == 1:
        _split_cpe_version(cpe, versions_by_cpe)
    else:
        raise ValueError(f"More than one el qualifier '::' in {cpe}")


def _match_and_save_stream_cpes(product_version: ProductVersion) -> None:
    """Given a Product Version with Product Stream foreign keys and cpes_matching_patterns
    This will match those cpes to the stream children using the stream versions, and save the cpes
    to the cpes_matching_patterns attribute of the streams."""
    versions_by_cpe: defaultdict[str, set] = defaultdict(set)
    el_suffix_by_cpe: defaultdict[str, list] = defaultdict(list)
    for cpe in product_version.cpes_matching_patterns:
        _split_cpe(cpe, versions_by_cpe, el_suffix_by_cpe)

    cpes_by_stream: defaultdict[str, list] = defaultdict(list)
    version_streams = product_version.productstreams.get_queryset()
    for cpe_name, versions in versions_by_cpe.items():
        for cpe_version in versions:
            cpe = cpe_name + ":" + cpe_version
            _match_stream_version_to_cpe_version(
                version_streams, cpe_version, cpe, el_suffix_by_cpe, cpes_by_stream
            )

    for stream, cpes in cpes_by_stream.items():
        ProductStream.objects.filter(name=stream).update(cpes_matching_patterns=cpes)


def parse_product_version(
    pd_product_version: dict[str, Any], product: Product, product_node: ProductNode
):
    """Parse the product versions from ps_modules in product-definitions.json"""
    pd_product_streams = pd_product_version.pop("product_streams", [])

    name = pd_product_version.pop("id")
    version = ""
    if match_version := RE_VERSION_LIKE_STRING.search(name):
        version = match_version.group()

    description = pd_product_version.pop("public_description", [])
    cpe_patterns = pd_product_version.pop("cpe", [])
    cpes_matching_patterns = _find_by_cpe(cpe_patterns)
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
            "cpe_patterns": cpe_patterns,
            "cpes_matching_patterns": cpes_matching_patterns,
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
    if cpes_matching_patterns:
        _match_and_save_stream_cpes(product_version)


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
    brew_tags_dict = {brew_tag["tag"]: brew_tag["inherit"] for brew_tag in brew_tags}

    yum_repos = pd_product_stream.pop("yum_repositories", [])
    composes = pd_product_stream.pop("composes", [])
    composes_dict = {compose["url"]: compose["variants"] for compose in composes}
    exclude_components = pd_product_stream.pop("exclude_components", [])
    name = pd_product_stream.pop("id")

    # If no match is found in the stream's name, use version from the parent ProductVersion's name
    # If no match was found in the parent PV's name, use the empty string (should never happen)
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
            "exclude_components": exclude_components,
            # reset by _match_and_save_stream_cpes
            "cpes_matching_patterns": [],
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
            # Brew tags in ET
            trimmed_brew_tag = brew_tag.removesuffix("-candidate")
            trimmed_brew_tag = trimmed_brew_tag.removesuffix("-released")
            # This is a special case for 'rhaos-4-*` brew tags which don't have the -container
            # suffix in ET, but do have that suffix in product_definitions
            sans_container_released = brew_tag.removesuffix("-container-released")
            et_pvs = CollectorErrataProductVersion.objects.filter(
                brew_tags__overlap=[trimmed_brew_tag, sans_container_released]
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
                            "cpe": et_variant.cpe,
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
