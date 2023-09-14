from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet

from config.celery import app
from corgi.collectors.errata_tool import ErrataTool
from corgi.collectors.models import CollectorRPMRepository
from corgi.core.models import (
    Channel,
    ProductComponentRelation,
    ProductNode,
    ProductVariant,
    SoftwareBuild,
)
from corgi.tasks.common import BUILD_TYPE, RETRY_KWARGS, RETRYABLE_ERRORS

logger = get_task_logger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def load_et_products() -> None:
    ErrataTool().load_et_products()
    save_variant_cdn_repo_mapping.delay()


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def save_variant_cdn_repo_mapping() -> None:
    ErrataTool().save_variant_cdn_repo_mapping()


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_save_errata_product_taxonomy(erratum_id: int) -> None:
    logger.info(f"slow_save_errata_product_taxonomy called for {erratum_id}")
    relation_builds = _get_relation_builds(erratum_id)
    for build_id, build_type, _ in relation_builds:
        logger.info("Saving product taxonomy for build (%s, %s)", build_id, build_type)
        # once all build's components are ingested we must save product taxonomy
        app.send_task("corgi.tasks.brew.slow_save_taxonomy", args=(build_id, build_type))


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_load_errata(erratum_name: str, force_process: bool = False) -> None:
    et = ErrataTool()
    erratum_id, shipped_live = et.get_errata_key_details(erratum_name)
    if not shipped_live:
        raise ValueError(f"Called slow_load_errata with non-shipped errata {erratum_name}")
    relation_builds = _get_relation_builds(erratum_id)

    build_types = set()
    build_ids = set()
    no_of_processed_builds = 0
    for build_id, build_type, software_build in relation_builds:
        build_ids.add(build_id)
        build_types.add(build_type)
        if software_build:
            no_of_processed_builds += 1

    # Errata are not used in community products, and we are not yet attaching other builds types
    # like HACBS to errata. If that changes, we should review this task for correctness
    if len(build_types) > 1:
        raise ValueError("Multiple build types found for errata %s", erratum_name)
    elif len(build_types) == 0:
        build_type = BUILD_TYPE
    else:
        build_type = build_types.pop()

    # If we have no relations at all, or we want to update them
    if len(build_ids) == 0 or force_process:
        # Save PCR
        logger.info("Saving product components for errata %s", erratum_id)
        variant_to_component_map = et.get_erratum_components(str(erratum_id))
        for variant_id, build_objects in variant_to_component_map.items():
            for build_obj in build_objects:
                for build_id, errata_components in build_obj.items():
                    # Add to relations list as we go, so we can fetch them below
                    build_ids.add(build_id)
                    ProductComponentRelation.objects.update_or_create(
                        external_system_id=erratum_id,
                        product_ref=variant_id,
                        build_id=build_id,
                        build_type=build_type,
                        defaults={
                            "type": ProductComponentRelation.Type.ERRATA,
                            "meta_attr": {"components": errata_components},
                        },
                    )

    # If the number of relations was more than 0 check if we've processed all the builds
    # in the errata
    elif len(build_ids) == no_of_processed_builds:
        logger.info(f"Calling slow_save_errata_product_taxonomy for {erratum_id}")
        slow_save_errata_product_taxonomy.delay(erratum_id)

    # Check if we are only part way through loading the errata
    if no_of_processed_builds < len(build_ids) or force_process:
        # Calculate and print the percentage of builds for the errata
        if len(build_ids) > 0:
            percentage_complete = int(no_of_processed_builds / len(build_ids) * 100)
            logger.info("Processed %i%% of builds in %s", percentage_complete, erratum_name)
        for build_id in build_ids:
            # We set save_product argument to False because it reads from the
            # ProductComponentRelations table which this function writes to. We've seen contention
            # on this database table causes by recursive looping of this task, and the
            # slow_fetch_modular_build task, eg CORGI-21. We call save_product_taxonomy from
            # this task only after all the builds in the errata have been loaded instead.
            logger.info("Calling slow_fetch_modular_build for %s", build_id)
            app.send_task(
                "corgi.tasks.brew.slow_fetch_modular_build",
                args=(build_id,),
                # Do not pass force_process through to child tasks
                # Or Celery will get stuck in an infinite loop
                # processing the same Brew builds / errata repeatedly
                kwargs={"save_product": False, "force_process": False},
            )

    if force_process:
        slow_save_errata_product_taxonomy.delay(erratum_id)

    logger.info("Finished processing %s", erratum_id)


def _get_relation_builds(erratum_id: int) -> QuerySet:
    # Get all the PCR with errata_id
    return ProductComponentRelation.objects.filter(
        type=ProductComponentRelation.Type.ERRATA, external_system_id=erratum_id
    ).values_list("build_id", "build_type", "software_build")


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def update_variant_repos() -> None:
    """Update each existing Product Variant's set of CDN repositories.

    CDN repos are saved as Channels and are linked to Variants as their children. Multiple Variants
    can link to the same CDN repo.
    """
    logger.info("Getting variant to CDN repo mapping from Errata Tool")
    variant_to_repo_map = ErrataTool.get_variant_cdn_repo_mapping()
    with transaction.atomic():
        for name, et_variant_data in variant_to_repo_map.items():
            try:
                pv = ProductVariant.objects.get(name=name)
            except ProductVariant.DoesNotExist:
                logger.warning("Product Variant %s from ET not found in models", name)
                continue

            pv_node = pv.pnodes.get()

            for repo_name in et_variant_data:
                # Filter out inactive repos in pulp and get content_set
                try:
                    rpm_repo = CollectorRPMRepository.objects.get(name=repo_name)
                except CollectorRPMRepository.DoesNotExist:
                    logger.debug("Not creating Channel for inactive repo %s", repo_name)
                    continue
                repo_obj, created = Channel.objects.update_or_create(
                    name=repo_name,
                    defaults={
                        "type": Channel.Type.CDN_REPO,
                        "relative_url": rpm_repo.relative_url,
                        "meta_attr": {"content_set": rpm_repo.content_set},
                    },
                )
                if created:
                    logger.info("Created new channel %s for variant %s", repo_obj, pv.name)
                # TODO: investigate whether we need to delete CDN repos that were removed from a
                #  Variant between two different runs of this task.
                #
                # Create a Product Node for each CDN repo linked to a Variant. This means that one
                # CDN repo (since we run update_or_create above) can be linked to
                # multiple product nodes, each linked to a different Variant.
                ProductNode.objects.get_or_create(
                    object_id=repo_obj.pk, parent=pv_node, defaults={"obj": repo_obj}
                )
                # Saving the Channel's taxonomy automatically links it to all other models
                # Those other models don't need to have their taxonomies saved separately
                repo_obj.save_product_taxonomy()


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_handle_shipped_errata(erratum_id: int, erratum_status: str) -> None:
    """Given a numeric ID for some SHIPPED_LIVE erratum,
    refresh each related build's list of tags, errata_tags, and released_errata_tags
    then force saving the erratum taxonomy in case it changed between ingestion and release"""
    logger.info(f"Refreshing tags and taxonomy for all builds on erratum {erratum_id}")
    if erratum_status != "SHIPPED_LIVE":
        # The UMB listener has a selector to limit the messages received
        # Only messages about SHIPPED_LIVE errata should be received and processed
        raise ValueError(f"Invalid status {erratum_status} for erratum {erratum_id}")
    builds_list = ErrataTool().get(f"api/v1/erratum/{erratum_id}/builds_list")

    # There should only be one erratum / list of builds in the response
    # But the JSON data is wrapped in some outer keys we want to ignore
    for erratum in builds_list.values():
        for brew_build in erratum["builds"]:
            for nested_build in brew_build.values():
                build_id = nested_build["id"]
                if not SoftwareBuild.objects.filter(
                    build_type=SoftwareBuild.Type.BREW, build_id=str(build_id)
                ).exists():
                    # Loading a new build for the first time will set the tags correctly
                    logger.warning(f"Brew build with matching ID not ingested yet: {build_id}")
                    logger.info(f"Calling slow_fetch_brew_build for {build_id}")
                    app.send_task(
                        "corgi.tasks.brew.slow_fetch_brew_build",
                        args=(str(build_id), SoftwareBuild.Type.BREW),
                    )
                else:
                    logger.info(f"Calling slow_refresh_brew_build_tags for {build_id}")
                    app.send_task("corgi.tasks.brew.slow_refresh_brew_build_tags", args=(build_id,))

    logger.info(f"Calling slow_load_errata for {erratum_id}")
    slow_load_errata.delay(str(erratum_id), force_process=True)
    logger.info(f"Finished refreshing tags and taxonomy for all builds on erratum {erratum_id}")
