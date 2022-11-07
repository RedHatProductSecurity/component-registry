import logging
from datetime import timedelta
from urllib.parse import urlparse

from celery_singleton import Singleton
from django.conf import settings
from django.utils import timezone

from config.celery import app
from corgi.collectors.yum import Yum
from corgi.core.models import Channel, ProductComponentRelation, ProductStream
from corgi.tasks.common import (
    RETRY_KWARGS,
    RETRYABLE_ERRORS,
    _create_relations,
    get_last_success_for_task,
)
from corgi.tasks.pulp import fetch_unprocessed_relations

logger = logging.getLogger(__name__)


@app.task(
    base=Singleton,
    autorety_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def fetch_unprocessed_yum_relations(force_process: bool = False, created_since: int = 0) -> int:
    if created_since:
        created_dt = timezone.now() - timedelta(days=created_since)
    else:
        created_dt = get_last_success_for_task("corgi.tasks.yum.fetch_unprocessed_yum_relations")
    return fetch_unprocessed_relations(
        ProductComponentRelation.Type.YUM_REPO,
        force_process=force_process,
        created_since=created_dt,
    )


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def load_yum_repositories() -> None:
    """Use dnf repoquery commands to inspect and load all content in all Yum repos"""
    logger.info("Loading all Yum repository data for all ProductStreams")
    for stream, repos in ProductStream.objects.exclude(yum_repositories=[]).values_list(
        "name", "yum_repositories"
    ):
        for repo in repos:  # type: ignore
            # mypy thinks yum_repositories can be None, even though it defaults to []
            slow_load_yum_repositories_for_stream.delay(stream, repo)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_load_yum_repositories_for_stream(stream: str, repo: str) -> None:
    """Use dnf repoquery commands to inspect and load all content in a particular Yum repo"""
    logger.info(f"Loading Yum repository {repo} for ProductStream {stream}")

    # Some "Yum repository" URLs in prod-defs are actually Pulp repo URLs
    # Check to see if we already made a Channel for them, by looking up the repo's relative URL
    # If not, create the repo using the full path in prod-defs as the name
    logger.info(f"Checking if Channel with matching URL already exists for {repo}")
    relative_url = urlparse(repo).path.lstrip("/")
    channel, created = Channel.objects.get_or_create(
        relative_url=relative_url,
        defaults={
            "name": repo,
            "type": Channel.Type.CDN_REPO,
        },
    )

    if created:
        logger.info(f"Created new Channel for {repo}")
    else:
        logger.info(f"Found existing Channel for {repo} with name {channel.name}")

    logger.info(f"Querying Yum repository {repo} for SRPMs")
    yum = Yum()
    srpm_build_ids = yum.get_srpms_from_yum_repos((repo,))
    # Save the Brew build IDs for all SRPMs in the repo into the ProductComponentRelation table
    # The daily "fetch_unprocessed_yum_relations" Celery task will look up these IDs
    # then create Components and SoftwareBuilds for each (including both RPMs and modules)
    srpm_relations = _create_relations(
        srpm_build_ids, channel.name, stream, ProductComponentRelation.Type.YUM_REPO
    )
    if srpm_relations > 0:
        logger.info(f"Created {srpm_relations} new relations for SRPMs in {channel.name}")

    logger.info(f"Querying Yum repository {repo} for modules")
    module_build_ids = yum.get_modules_from_yum_repos((repo,))
    module_relations = _create_relations(
        module_build_ids, channel.name, stream, ProductComponentRelation.Type.YUM_REPO
    )
    if module_relations > 0:
        logger.info(f"Created {module_relations} new relations for modules in {channel.name}")
