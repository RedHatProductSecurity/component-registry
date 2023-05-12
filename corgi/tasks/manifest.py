from pathlib import Path

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.core.management import call_command
from django.db.models import Count

from config.celery import app
from corgi.core.models import ProductStream
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = get_task_logger(__name__)


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
)
def update_manifests(fixup=True):
    # Note - temporarily setting fixup=True
    # Ensure the OUTPUT_FILES_DIR exists on first run
    Path(settings.OUTPUT_FILES_DIR).mkdir(exist_ok=True)
    for ps in ProductStream.objects.annotate(num_components=Count("components")).filter(
        num_components__gt=0
    ):
        cpu_update_ps_manifest.delay(ps.name, fixup=fixup)


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
)
def cpu_update_ps_manifest(product_stream: str, fixup=True):
    logger.info("Updating manifest for %s", product_stream)
    ps = ProductStream.objects.get(name=product_stream)
    # TODO figure out a way skip updating files where the content doesnt need updating
    # for example we could check for the timestamp of the latest build, and only update if we
    # have a newer build than the last run.
    # That will allow clients to continue to be served the same content from the browser cache
    # over the span of multiple days, until the product stream receives a new build
    if ps.components.root_components().released_components().latest_components().exists():
        logger.info(f"Generating manifest for {product_stream}")
        with open(f"{settings.OUTPUT_FILES_DIR}/{product_stream}-{ps.pk}.json", "w") as fh:
            fh.write(ps.manifest)
    else:
        logger.info(
            f"Didn't find any released components for {product_stream}, "
            f"skipping manifest generation"
        )


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def collect_static():
    call_command("collectstatic", verbosity=1, interactive=False)
