import logging

from celery_singleton import Singleton
from django.conf import settings

from config.celery import app
from corgi.collectors.pulp import Pulp
from corgi.core.models import Channel, ProductComponentRelation, ProductVariant
from corgi.tasks.brew import fetch_unprocessed_relations
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS, _create_relations
from corgi.tasks.errata_tool import update_variant_repos

logger = logging.getLogger(__name__)


@app.task(
    base=Singleton,
    autorety_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def slow_fetch_unprocessed_cdn_relations(force_process=False, created_since=8) -> int:
    return fetch_unprocessed_relations(
        ProductComponentRelation.Type.CDN_REPO,
        force_process=force_process,
        created_since=created_since,
    )


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
