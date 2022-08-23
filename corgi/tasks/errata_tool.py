import logging

from celery_singleton import Singleton
from django.db import transaction

from config.celery import app
from corgi.collectors.errata_tool import ErrataTool
from corgi.core.models import (
    Channel,
    ProductComponentRelation,
    ProductVariant,
    SoftwareBuild,
)
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def load_et_products() -> None:
    ErrataTool().load_et_products()


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_save_errata_product_taxonomy(erratum_id: int):
    logger.info(f"slow_save_errata_product_taxonomy called for {erratum_id}")
    relation_build_ids = _get_relation_build_ids(erratum_id)
    for b in relation_build_ids:
        logger.info("Saving product taxonomy for build %s", b)
        # once all build's components are ingested we must save product taxonomy
        sb = SoftwareBuild.objects.get(build_id=b)
        sb.save_product_taxonomy()


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_load_errata(erratum_name):
    et = ErrataTool()
    if not erratum_name.isdigit():
        erratum_id = et.normalize_erratum_id(erratum_name)
        if not erratum_id:
            logger.warning("Couldn't normalize erratum: %s", erratum_name)
            return
    else:
        erratum_id = int(erratum_name)
    relation_build_ids = _get_relation_build_ids(erratum_id)

    # Check is we have software builds for all of them.
    # Most of the time will not have all the builds
    # so we don't load all the software builds at this point
    no_of_processed_builds = SoftwareBuild.objects.filter(build_id__in=relation_build_ids).count()

    if len(relation_build_ids) == 0:
        # Save PCR
        logger.info("Saving product components for errata %s", erratum_id)
        variant_to_component_map = et.get_erratum_components(str(erratum_id))
        for variant_id, build_objects in variant_to_component_map.items():
            for build_obj in build_objects:
                for build_id, errata_components in build_obj.items():
                    # Add to relations list as we go, so we can fetch them below
                    relation_build_ids.add(int(build_id))
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
        logger.info(f"Calling slow_save_errata_product_taxonomy for {erratum_id}")
        slow_save_errata_product_taxonomy.delay(erratum_id)

    # Check if we are only part way through loading the errata
    if no_of_processed_builds < len(relation_build_ids):
        # Calculate and print the percentage of builds for the errata
        if len(relation_build_ids) > 0:
            percentage_complete = int(no_of_processed_builds / len(relation_build_ids) * 100)
            logger.info("Processed %i%% of builds in %s", percentage_complete, erratum_name)
        for build_id in relation_build_ids:
            # We set save_product argument to False because it reads from the
            # ProductComponentRelations table which this function writes to. We've seen contention
            # on this database table causes by recursive looping of this task, and the
            # slow_fetch_brew_build task, eg CORGI-21. We call save_product_taxonomy from
            # this task only after all the builds in the errata have been loaded instead.
            logger.info("Calling slow_fetch_brew_build for %s", build_id)
            app.send_task("corgi.tasks.brew.slow_fetch_brew_build", args=[build_id, False])
    else:
        logger.info("Finished processing %s", erratum_id)


def _get_relation_build_ids(erratum_id: int) -> set[int]:
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
    return set(int(b_id) for b_id in relation_build_ids if b_id is not None)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def update_variant_repos() -> None:
    """Update each existing Product Variant's set of CDN repositories.

    CDN repos are saved as Channels and are linked to Variants as their children. Multiple Variants
    can link to the same CDN repo.
    """
    variant_to_repo_map: dict = ErrataTool().variant_cdn_repo_mapping()
    with transaction.atomic():
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
                # CDN repo (since we run get_or_create above) can be linked to
                # multiple product nodes, each linked to a different Variant.
                repo.pnodes.get_or_create(parent=pv_node)

            # Update list of channels for this Variant so that we don't have to call
            # the more expensive save_product_taxonomy() method just to update channels.
            pv.channels = pv_channels
            pv.save()
