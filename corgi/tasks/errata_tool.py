import logging

from celery import chain
from django.db import transaction
from django.db.models import QuerySet

from config.celery import app
from corgi.collectors.brew import Brew
from corgi.collectors.errata_tool import ErrataTool
from corgi.core.models import (
    Channel,
    ProductComponentRelation,
    ProductNode,
    ProductStream,
    ProductVariant,
    SoftwareBuild,
)
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)


@app.task
def load_et_products() -> None:
    ErrataTool().load_et_products()


def link_stream_using_brew_tag(brew_tag: str, stream_name: str, inherit: bool = False) -> None:
    build_ids = Brew().get_builds_with_tag(brew_tag, inherit)
    product_stream = ProductStream.objects.get(name=stream_name)
    logger.info("Link stream to variant called with build_ids %s", build_ids)
    variants = save_product_components_for_builds(build_ids)
    for variant in variants:
        # Link the variant with the product stream
        product_variant, _ = ProductVariant.objects.get_or_create(name=variant)
        product_stream_node = ProductNode.objects.get(object_id=product_stream.pk)
        product_variant.pnodes.get_or_create(parent=product_stream_node)
        product_variant.save_product_taxonomy()
        product_variant.save()
    product_stream.save_product_taxonomy()
    product_stream.save()


def save_product_components_for_builds(build_ids: list[int]) -> QuerySet:
    # TODO fix this so that we don't try to call load_errata with a list
    result = chain(get_errata_for_builds.s(build_ids), load_errata.s())()
    result.get()

    # filtering by build_ids ensures we only get variants for the build_ids passed in
    return (
        ProductComponentRelation.objects.filter(build_id__in=build_ids)
        .filter(type=ProductComponentRelation.Type.ERRATA)
        .values_list("product_ref", flat=True)
        .order_by("product_ref")
    )


@app.task
def get_errata_for_builds(build_ids: list[int]) -> list[str]:
    et = ErrataTool()
    errata_ids = set()
    for build_id in build_ids:
        logger.info("Fetching errata build info for build_id: %s", build_id)
        build_info = et.get(f"api/v1/build/{build_id}")
        if "all_errata" in build_info:
            errata_ids.update([str(e["id"]) for e in build_info["all_errata"]])
        else:
            logger.debug("Didn't find any errata for build id: %s", build_id)
    return list(errata_ids)


@app.task(
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
)
def load_errata(erratum_name):
    et = ErrataTool()
    if not erratum_name.isdigit():
        erratum_id = et.normalize_erratum_id(erratum_name)
        if not erratum_id:
            logger.warning("Couldn't normalize erratum: %s", erratum_name)
            return
    else:
        erratum_id = int(erratum_name)
    # Get all the PCR with errata_id
    relation_build_ids = (
        ProductComponentRelation.objects.filter(
            type=ProductComponentRelation.Type.ERRATA, external_system_id=erratum_id
        )
        .values_list("build_id", flat=True)
        .distinct()
    )
    # Convert them to ints for use in queries and tasks
    # We don't store them as int because another build system not use ints
    relation_int_build_ids = set([int(b_id) for b_id in relation_build_ids])

    # Check is we have software builds for all of them.
    # Most of the time will not have all the builds
    # so we don't load all the software builds at this point
    no_of_processed_builds = SoftwareBuild.objects.filter(
        build_id__in=relation_int_build_ids
    ).count()

    if len(relation_int_build_ids) == 0:
        # Save PCR
        logger.info("Saving product components for errata %s", erratum_id)
        variant_to_component_map = et.get_erratum_components(str(erratum_id))
        for variant_id, build_objects in variant_to_component_map.items():
            for build_obj in build_objects:
                for build_id, errata_components in build_obj.items():
                    # Add to relations list as we go, so we can fetch them below
                    relation_int_build_ids.add(int(build_id))
                    ProductComponentRelation.objects.get_or_create(
                        external_system_id=erratum_id,
                        product_ref=variant_id,
                        build_id=build_id,
                        defaults={
                            "type": ProductComponentRelation.Type.ERRATA,
                            "meta_attr": {"components": errata_components},
                        },
                    )
    # If the number of relations was more than 0 check if we've processed all the builds
    # in the errata
    elif len(relation_build_ids) == no_of_processed_builds:
        logger.info("Saving product taxonomy for builds")
        for b in relation_int_build_ids:
            logger.info("Saving product taxonomy for build %s", b)
            # once all build's components are ingested we must update component taxonomy
            sb = SoftwareBuild.objects.get(build_id=b)
            sb.save_product_taxonomy()

    # Check if we are part way through loading the errata
    if no_of_processed_builds < len(relation_int_build_ids):
        # Calculate and print the percentage of builds for the errata
        if len(relation_int_build_ids) > 0:
            percentage_complete = int(no_of_processed_builds / len(relation_int_build_ids) * 100)
            logger.info("Processed %i%% of builds in %s", percentage_complete, erratum_name)
        for build_id in relation_int_build_ids:
            logger.info("Calling slow_fetch_brew_build for %s", build_id)
            app.send_task("corgi.tasks.brew.slow_fetch_brew_build", args=[build_id])
    else:
        logger.info("Finished processing %s", erratum_id)


@app.task
@transaction.atomic
def update_variant_repos() -> None:
    """Update each existing Product Variant's set of CDN repositories.

    CDN repos are saved as Channels and are linked to Variants as their children. Multiple Variants
    can link to the same CDN repo.
    """
    variant_to_repo_map: dict = ErrataTool().variant_cdn_repo_mapping()
    for pv in ProductVariant.objects.all():
        if pv.name not in variant_to_repo_map:
            logger.error("Product Variant '%s' does not exist in Errata Tool", pv.name)
            continue

        pv_node = pv.pnodes.first()
        et_variant_data = variant_to_repo_map[pv.name]

        pv_channels = []
        for repo in et_variant_data["repos"]:
            repo, _ = Channel.objects.get_or_create(
                name=repo, defaults={"type": Channel.Type.CDN_REPO}
            )
            pv_channels.append(repo.name)
            # TODO: investigate whether we need to delete CDN repos that were removed from a
            #  Variant between two different runs of this task.
            #
            # Create a Product Node for each CDN repo linked to a Variant. This means that one
            # CDN repo (since we run get_or_create above) can be linked to multiple product nodes,
            # each linked to a different Variant.
            repo.pnodes.get_or_create(parent=pv_node)

        # Update list of channels for this Variant so that we don't have to call the more expensive
        # save_product_taxonomy() method just to update channels.
        pv.channels = pv_channels
        pv.save()
