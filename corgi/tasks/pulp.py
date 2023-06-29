from celery.utils.log import get_task_logger
from celery_singleton import Singleton

from config.celery import app
from corgi.collectors.pulp import Pulp
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS
from corgi.tasks.errata_tool import update_variant_repos

logger = get_task_logger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def update_cdn_repo_channels() -> int:
    logger.info("Getting active repositories from Pulp")
    no_of_created_repos = Pulp().get_active_repositories()
    logger.info("Created %s new active CDN repositories", no_of_created_repos)
    update_variant_repos.delay()
    return no_of_created_repos
