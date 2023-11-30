import re
from collections import defaultdict
from typing import Any

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.db import transaction
from django.db.models import Q, QuerySet

from config.celery import app
from corgi.collectors.errata_tool import BREW_TAG_CANDIDATE_SUFFIX
from corgi.collectors.models import (
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
    CollectorErrataRelease,
)
from corgi.collectors.prod_defs import ProdDefs
from corgi.core.models import (
    Product,
    ProductComponentRelation,
    ProductNode,
    ProductStream,
    ProductVariant,
    ProductVersion,
    SoftwareBuild,
)
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS, slow_save_taxonomy

logger = get_task_logger(__name__)
# Find a substring that looks like a version (e.g. "3", "3.5", "3-5", "1.2.z") at the end of a
# searched string.
RE_VERSION_LIKE_STRING = re.compile(r"\d[\dz.-]*$|$")


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_remove_product_from_build(build_pk: str, product_model_name: str, product_pk: str) -> None:
    SoftwareBuild.objects.get(pk=build_pk).disassociate_with_product(product_model_name, product_pk)


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
            product.save_product_taxonomy()


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

    product_version.save_product_taxonomy()

    if cpes_matching_patterns:
        _match_and_save_stream_cpes(product_version)


def _parse_variants_from_brew_tags(brew_tags: list[str]) -> dict[str, str]:
    """We don't link core ProductVariant models to streams via brew_tags because it results in too
    many builds being linked to the stream. These variants should include all possible builds that
    could match the stream. We use them for finding which errata match this stream, so it's OK to
    find too many errata"""
    distinct_variants: dict[str, str] = {}
    # TODO add CollectorErrataProductVariant.repos to ProductVariant
    for brew_tag in brew_tags:
        trimmed_brew_tag, sans_container_released = brew_tag_to_et_prefixes(brew_tag)
        variant_data = CollectorErrataProductVersion.objects.filter(
            brew_tags__overlap=[trimmed_brew_tag, sans_container_released]
        ).values("variants__name", "variants__cpe")
        for variant in variant_data:
            distinct_variants[variant["variants__name"]] = variant["variants__cpe"]

    return distinct_variants


def _create_inferred_variants(variants_and_cpes: dict[str, str], product: Product, product_version: ProductVersion, product_stream: ProductStream, parent: ProductNode) -> None:
    for variant, cpe in variants_and_cpes.items():
        variant, created = ProductVariant.objects.update_or_create(
            name=variant,
            defaults={
                "cpe": cpe,
                "products": product,
                "productversions": product_version,
                "productstreams": product_stream
            }
        )
        if created:
            logger.info(f"Created ProductVariant {variant} as inferred variant of {product_stream.name} ProductStream")

        # TODO set type to INFERRED
        ProductNode.objects.get_or_create(
            object_id=variant.pk,
            parent=parent,
            defaults={"obj": variant}
        )




def _parse_releases_from_brew_tags(brew_tags) -> list[int]:
    """Match releases using stream brew tags"""
    release_ids: set[int] = set()
    for brew_tag in brew_tags:
        trimmed_brew_tag, sans_container_released = brew_tag_to_et_prefixes(brew_tag)
        releases = (
            CollectorErrataRelease.objects.filter(
                brew_tags__overlap=[trimmed_brew_tag, sans_container_released]
            )
            .values_list("et_id", flat=True)
            .distinct()
        )
        release_ids.update(releases)
    return list(release_ids)


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

    cpes_from_brew_tags = _parse_cpes_from_brew_tags(brew_tags_dict, name, product_version.name)
    brew_tag_names = brew_tags_dict.keys()
    # TODO stop setting variants_from_brew_tags on meta_attr and just get the data from the linked (INFERRED) Variants
    variants_and_cpes = _parse_variants_from_brew_tags(brew_tag_names)
    pd_product_stream["variants_from_brew_tags"] = list(variants_and_cpes.keys())
    pd_product_stream["releases_from_brew_tags"] = _parse_releases_from_brew_tags(brew_tag_names)

    logger.debug("Creating or updating Product Stream: name=%s", name)

    product_stream, stream_created = ProductStream.objects.update_or_create(
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
            "cpes_from_brew_tags": cpes_from_brew_tags,
        },
    )
    product_stream_node, _ = ProductNode.objects.get_or_create(
        object_id=product_stream.pk,
        defaults={
            "parent": product_version_node,
            "obj": product_stream,
        },
    )

    _create_inferred_variants(variants_and_cpes, product, product_version, product_stream, product_stream_node)

    parse_errata_info(errata_info, product, product_stream, product_stream_node, product_version)
    if not stream_created:
        _clean_orphaned_relations_and_builds(
            set(brew_tag_names),
            name,
            str(product_stream.pk),
            ProductComponentRelation.Type.BREW_TAG,
        )
        _clean_orphaned_relations_and_builds(
            yum_repos, name, str(product_stream.pk), ProductComponentRelation.Type.YUM_REPO
        )
    product_stream.save_product_taxonomy()


def _clean_orphaned_relations_and_builds(
    new_external_system_ids: set[str],
    name: str,
    product_stream_pk: str,
    relation_type: ProductComponentRelation.Type,
) -> None:
    relations_to_remove = (
        ProductComponentRelation.objects.filter(type=relation_type, product_ref=name)
        .exclude(external_system_id__in=new_external_system_ids)
        .iterator()
    )
    related_product_refs: set[str] = {name}
    related_product_refs.update(
        ProductStream.objects.get(pk=product_stream_pk).productvariants.values_list(
            "name", flat=True
        )
    )
    for relation_to_remove in relations_to_remove:
        # Make sure there is no other relation linking this build to the stream or it's child
        # variants before removing
        existing_stream_relation = (
            ProductComponentRelation.objects.filter(
                build_id=relation_to_remove.build_id, product_ref__in=related_product_refs
            )
            .exclude(pk=relation_to_remove.pk)
            .exists()
        )
        # We want to remove relations even if they don't have a software_build foreign key populated
        # However we only need to clear the product_ref from the build if we've linked a build
        if relation_to_remove.software_build_id and not existing_stream_relation:
            slow_remove_product_from_build.delay(
                str(relation_to_remove.software_build_id),
                "ProductStream",
                product_stream_pk,
            )
        relation_to_remove.delete()


def _parse_cpes_from_brew_tags(
    brew_tags: dict[str, bool],
    stream_name: str,
    version_name: str,
) -> list[str]:
    """Match streams using brew_tags to Errata Tool Product Versions and their Variants
    Return a list of CPEs for the matched Variants"""
    if len(brew_tags) > 0:
        logger.debug(f"Found brew tags {brew_tags} in product stream: {stream_name}")
        cpes: set[str] = set()
        for brew_tag in brew_tags.keys():
            trimmed_brew_tag, sans_container_released = brew_tag_to_et_prefixes(brew_tag)
            brew_tag_cpes = CollectorErrataProductVersion.objects.filter(
                brew_tags__overlap=[trimmed_brew_tag, sans_container_released],
                variants__cpe__isnull=False,
            ).values_list("variants__cpe", flat=True)
            cpes.update(brew_tag_cpes)
        return list(cpes)
    else:
        return []


def brew_tag_to_et_prefixes(brew_tag: str) -> tuple[str, str]:
    """Match brew tags in prod_defs with those from ET by reducing them to common prefixes"""
    trimmed_brew_tag = brew_tag.removesuffix(BREW_TAG_CANDIDATE_SUFFIX)
    trimmed_brew_tag = trimmed_brew_tag.removesuffix("-released")
    # This is a special case for 'rhaos-4-*` brew tags which don't have the -container
    # suffix in ET, but do have that suffix in product_definitions
    sans_container_released = brew_tag.removesuffix("-container-released")
    return trimmed_brew_tag, sans_container_released


def parse_errata_info(
    errata_info: list[dict],
    product: Product,
    product_stream: ProductStream,
    product_stream_node: ProductNode,
    product_version: ProductVersion,
):
    """Parse and create ProductVariants from errata_info in product-definitions.json"""
    # Reset the stream's et_product_versions_set so we keep it up to date with what's in prod_defs.
    et_product_versions_set = set()
    for et_product in errata_info:
        et_product_name = et_product.pop("product_name")
        et_product_versions = et_product.pop("product_versions")

        for et_product_version in et_product_versions:
            et_pv_name = et_product_version["name"]
            et_product_versions_set.add(et_pv_name)

            # This is a workaround for CORGI-811 which attaches variants from errata_info only to
            # active streams
            # TODO remove this
            if not product_stream.active:
                continue

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
                # TODO if the ProductNode exists and is of the inferred type, change it to a direct
                # TODO move parent out of defaults and into kw args to allow linking this variant to multiple streams
                # If multiple active streams share the same errata_info,
                # use update to relink the node to a different parent product_stream_node
                # the last matching stream always wins / gets the link
                node, node_created = ProductNode.objects.get_or_create(
                    object_id=product_variant.pk,
                    defaults={
                        "parent": product_stream_node,
                        "obj": product_variant,
                    },
                )
                # TODO, stop updating the parent as stream -> variant is now many-to-many
                if node.parent != product_stream_node:
                    node.parent = product_stream_node
                    node.save()
                    # save the existing builds to adjust the product_stream
                    # we don't remove the old product_stream from the builds because are related via
                    # the same variant. This a workaround for CORGI-811 stream to variant should be
                    # many-to-many relationship
                    builds_to_update = (
                        ProductComponentRelation.objects.filter(
                            type__in=ProductComponentRelation.VARIANT_TYPES, product_ref=variant
                        )
                        .values_list("build_id", "build_type")
                        .distinct()
                    )
                    for build_id, build_type in builds_to_update:
                        slow_save_taxonomy.delay(build_id, build_type)
                product_variant.save_product_taxonomy()
    # persist et_product_versions plucked from errata_info
    product_stream.et_product_versions = sorted(et_product_versions_set)
    product_stream.save()
