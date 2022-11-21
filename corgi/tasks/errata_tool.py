from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.db import transaction

from config.celery import app
from corgi.collectors.errata_tool import ErrataTool
from corgi.collectors.models import CollectorRPMRepository
from corgi.core.models import (
    Channel,
    ProductComponentRelation,
    ProductVariant,
    SoftwareBuild,
)
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = get_task_logger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def load_et_products() -> None:
    et = ErrataTool()
    et.load_et_products()
    et.save_variant_cdn_repo_mapping()


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def save_errata_product_taxonomy(erratum_id: int):
    logger.info(f"save_errata_product_taxonomy called for {erratum_id}")
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
        logger.info(f"Calling save_errata_product_taxonomy for {erratum_id}")
        save_errata_product_taxonomy.delay(erratum_id)

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
            app.send_task("corgi.tasks.brew.slow_fetch_brew_build", args=(build_id, False))
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
    logger.info("Getting variant to CDN repo mapping from Errata Tool")
    variant_to_repo_map: dict = ErrataTool().get_variant_cdn_repo_mapping()
    with transaction.atomic():
        for name, et_variant_data in variant_to_repo_map.items():
            try:
                pv = ProductVariant.objects.get(name=name)
            except ProductVariant.DoesNotExist:
                logger.warning("Product Variant %s from ET not found in models", name)
                continue

            pv_node = pv.pnodes.first()

            for repo in et_variant_data["repos"]:
                # Filter out inactive repos in pulp and get content_set
                try:
                    rpm_repo = CollectorRPMRepository.objects.get(name=repo)
                except CollectorRPMRepository.DoesNotExist:
                    logger.debug("Not creating Channel for inactive repo %s", repo)
                    continue
                repo, created = Channel.objects.update_or_create(
                    name=repo,
                    defaults={
                        "type": Channel.Type.CDN_REPO,
                        "relative_url": rpm_repo.relative_url,
                        "meta_attr": {"content_set": rpm_repo.content_set},
                    },
                )
                if created:
                    logger.info("Created new channel %s for variant %s", repo, pv.name)
                # TODO: investigate whether we need to delete CDN repos that were removed from a
                #  Variant between two different runs of this task.
                #
                # Create a Product Node for each CDN repo linked to a Variant. This means that one
                # CDN repo (since we run update_or_create above) can be linked to
                # multiple product nodes, each linked to a different Variant.
                repo.pnodes.get_or_create(object_id=repo.pk, parent=pv_node, defaults={"obj": repo})
                # Saving the Channel's taxonomy automatically links it to all other models
                # Those other models don't need to have their taxonomies saved separately
                repo.save_product_taxonomy()
