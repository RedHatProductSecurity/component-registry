from collections import defaultdict

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db import transaction

from config.celery import app
from corgi.collectors.errata_tool import ErrataTool
from corgi.collectors.models import (
    CollectorErrataProductVariant,
    CollectorRPMRepository,
)
from corgi.core.models import (
    Channel,
    ProductComponentRelation,
    ProductNode,
    ProductStream,
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


def associate_variant_with_build_stream(
    build_id: str, build_type: str, variant_names: list[str]
) -> bool:
    try:
        software_build = SoftwareBuild.objects.get(build_id=build_id, build_type=build_type)
    except SoftwareBuild.DoesNotExist:
        logger.warning(f"SoftwareBuild with id {build_id} of type {build_type} does not exist.")
        return False

    # This function is design to discover new variant to stream mappings
    # If a variant already exists, we assume it is already correct mapped to a stream and give up
    # This avoids the slow build_streams query below when it's not necessary
    if ProductVariant.objects.filter(name__in=variant_names).exists():
        logger.debug(f"Not associating Product Variants {variant_names}")
        return False

    build_streams = ProductStream.objects.filter(components__software_build=software_build)
    any_created = False

    build_streams_count = len(build_streams)
    # Only associate this variant to a single stream to keep one-to-many cardinality between
    # ProductStream and ProductVariant
    if build_streams_count != 1:
        return any_created

    stream = build_streams[0]
    for variant_name in variant_names:
        # The CollectorErrataProductVariant should always exist, let the DoesNotExist error
        # propagate if it doesn't
        try:
            errata_variant = CollectorErrataProductVariant.objects.get(name=variant_name)
            cpe = errata_variant.cpe
        except CollectorErrataProductVariant.DoesNotExist:
            logger.warning(f"CollectorErrataProductVariant with name {variant_name} does not exist")
            cpe = ""
        # We don't use update_or_create here because we checked earlier in this function
        # ProductVariant objects with this name do not already exists.
        ProductVariant.objects.create(
            name=variant_name,
            cpe=cpe,
            products=stream.products,
            productversions=stream.productversions,
            productstreams=stream,
            meta_attr={
                "build_id": build_id,
                "build_type": build_type,
            },
        )
        any_created = True
        # CORGI-703 - Also add channels for each new variant here
    return any_created


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_save_errata_product_taxonomy(
    build_variants: dict[str, list[str]], build_type: str
) -> bool:
    logger.info(f"slow_save_errata_product_taxonomy called with build_variants {build_variants}")

    variants_created = False
    for build_id, variant_ids in build_variants.items():
        # Only associate stream with variants where an existing relation not of type ERRATA exists
        if (
            ProductComponentRelation.objects.filter(
                build_id=build_id,
                build_type=build_type,
            )
            .exclude(type=ProductComponentRelation.Type.ERRATA)
            .exists()
        ):
            if associate_variant_with_build_stream(build_id, build_type, variant_ids):
                variants_created = True
        logger.info("Saving product taxonomy for build (%s, %s)", build_id, build_type)
        # once all build's components are ingested we must save product taxonomy
        sb = SoftwareBuild.objects.get(build_id=build_id, build_type=build_type)
        sb.save_product_taxonomy()
    return variants_created


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_load_errata(erratum_name: str, force_process: bool = False) -> None:
    et = ErrataTool()
    if not erratum_name.isdigit():
        erratum_id = et.normalize_erratum_id(erratum_name)
    else:
        erratum_id = int(erratum_name)
    build_variants, build_type = _get_relation_builds(erratum_id)

    # Check is we have software builds for all of them.
    no_of_processed_builds = SoftwareBuild.objects.filter(
        build_id__in=build_variants.keys(), build_type=build_type
    ).count()

    # If we have no relations at all, or we want to update them
    if len(build_variants) == 0 or force_process:
        # Save Errata relations
        logger.info("Saving relations for errata %s", erratum_id)
        variant_to_component_map = et.get_erratum_components(str(erratum_id))
        for variant_id, build_objects in variant_to_component_map.items():
            for build_obj in build_objects:
                for build_id, errata_components in build_obj.items():
                    # Add to relations list as we go, so we can fetch them below
                    build_variants[build_id] = variant_id
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

    # If we have SoftwareBuilds for all the builds in the errata, save the build product taxonomy
    elif len(build_variants) == no_of_processed_builds:
        logger.info(f"Calling slow_save_errata_product_taxonomy for {erratum_id}")
        slow_save_errata_product_taxonomy.delay(build_variants, build_type)

    # Check if we are only part way through loading the errata
    if no_of_processed_builds < len(build_variants) or force_process:
        # Calculate and print the percentage of builds for the errata
        if len(build_variants) > 0:
            percentage_complete = int(no_of_processed_builds / len(build_variants) * 100)
            logger.info("Processed %i%% of builds in %s", percentage_complete, erratum_name)
        for build_id in build_variants.keys():
            # We set save_product argument to False because it reads from the
            # ProductComponentRelations table which this function writes to. We've seen contention
            # on this database table causes by recursive looping of this task, and the
            # slow_fetch_brew_build task, eg CORGI-21. We call save_product_taxonomy from
            # this task only after all the builds in the errata have been loaded instead.
            logger.info("Calling slow_fetch_brew_build for %s", build_id)
            app.send_task(
                "corgi.tasks.brew.slow_fetch_brew_build",
                args=(build_id, build_type),
                # Do not pass force_process through to child tasks
                # Or Celery will get stuck in an infinite loop
                # processing the same Brew builds / errata repeatedly
                kwargs={"save_product": False, "force_process": False},
            )

    if force_process:
        slow_save_errata_product_taxonomy.delay(build_variants, build_type)

    logger.info("Finished processing %s", erratum_id)


def _get_relation_builds(erratum_id: int) -> tuple[dict[str, list[str]], str]:
    # Get all the PCR with errata_id
    relation_builds = ProductComponentRelation.objects.filter(
        type=ProductComponentRelation.Type.ERRATA, external_system_id=erratum_id
    ).values_list("build_id", "build_type", "product_ref")
    build_variants: dict[str, list[str]] = defaultdict(list)
    build_types = set()
    # Check erratum builds all have the same type
    for build_id, build_type, variant_id in relation_builds:
        build_variants[build_id].append(variant_id)
        build_types.add(build_type)

    # Errata are not used in community products, and we are not yet attaching other builds types
    # like HACBS to errata. If that changes, we should review slow_load_errata for correctness
    if len(build_types) > 1:
        raise ValueError("Multiple build types found for errata %s", erratum_id)
    elif len(build_types) == 0:
        # if the queryset above had no results
        build_type = BUILD_TYPE
    else:
        build_type = build_types.pop()
    return build_variants, build_type


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

            pv_node = pv.pnodes.get()

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
                ProductNode.objects.get_or_create(
                    object_id=repo.pk, parent=pv_node, defaults={"obj": repo}
                )
                # Saving the Channel's taxonomy automatically links it to all other models
                # Those other models don't need to have their taxonomies saved separately
                repo.save_product_taxonomy()


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
