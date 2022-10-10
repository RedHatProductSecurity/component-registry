import logging
from datetime import timedelta

from celery_singleton import Singleton
from django.utils import timezone

from config.celery import app
from corgi.collectors.pulp import Pulp
from corgi.core.models import (
    Channel,
    ProductComponentRelation,
    ProductVariant,
    SoftwareBuild,
)
from corgi.tasks.brew import fetch_modular_build
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS, _create_relations
from corgi.tasks.errata_tool import update_variant_repos

logger = logging.getLogger(__name__)


@app.task(base=Singleton, autorety_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def fetch_unprocessed_cdn_relations(force_process=False, created_in_last_week=True):
    relations_query = ProductComponentRelation.objects.filter(
        type=ProductComponentRelation.Type.CDN_REPO
    )
    if created_in_last_week:
        # Account for any timedelay by processing 8 days instead of 7
        created_during = timezone.now() - timedelta(days=8)
        relations_query = relations_query.filter(created_at__gte=created_during)
    # batch process to avoid exhausting the memory limit for the pod
    relation_count = relations_query.count()
    logger.info("Found %s unprocessed relations", relation_count)
    offset = 0
    limit = 10000
    while offset < relation_count:
        logger.info("Processing CDN relations with offset %s and limit %s", offset, limit)
        for build_id in relations_query.values_list("build_id", flat=True).distinct()[
            offset : offset + limit
        ]:
            if not SoftwareBuild.objects.filter(build_id=int(build_id)).exists():
                logger.info("Processing CDN relation build with id: %s", build_id)
                fetch_modular_build.delay(build_id, force_process=force_process)
        offset = offset + limit


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def setup_pulp_relations() -> None:
    logger.info("Setting up CDN repo relations for all Channels")
    for channel in Channel.objects.filter(type=Channel.Type.CDN_REPO):
        for pv_ofuri in channel.product_variants:
            pv = ProductVariant.objects.get(ofuri=pv_ofuri)
            slow_setup_pulp_rpm_relations.delay(channel.name, pv.name)
            slow_setup_pulp_module_relations.delay(channel.name, pv.name)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_setup_pulp_rpm_relations(channel, variant):
    srpm_build_ids = Pulp().get_rpm_data(channel)
    no_of_relations = _create_relations(
        srpm_build_ids, channel, variant, ProductComponentRelation.Type.CDN_REPO
    )
    if no_of_relations > 0:
        logger.info("Created %s new relations for SRPMs in %s", no_of_relations, channel)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_setup_pulp_module_relations(channel, variant):
    module_build_ids = Pulp().get_module_data(channel)
    no_of_relations = _create_relations(
        module_build_ids, channel, variant, ProductComponentRelation.Type.CDN_REPO
    )
    if no_of_relations > 0:
        logger.info("Created %s new relations for rhel_modules in %s", no_of_relations, channel)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def update_cdn_repo_channels() -> int:
    logger.info("Getting active repositories from Pulp")
    no_of_created_repos = Pulp().get_active_repositories()
    logger.info("Created %s new active CDN repositories", no_of_created_repos)
    update_variant_repos.delay()
    return no_of_created_repos
