from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
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
def update_manifests():
    for ps in ProductStream.objects.annotate(num_components=Count("components")).filter(
        num_components__gt=0
    ):
        cpu_update_ps_manifest.delay(ps.name)


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
)
def cpu_update_ps_manifest(product_stream: str):
    logger.info("Updating manifest for %s", product_stream)
    ps = ProductStream.objects.get(name=product_stream)
    # TODO figure out a way to skip updating files where the content doesnt need updating
    # for example we could render the manifest in a temp file, remove the created_at line and diff
    # the contents only if there is a difference we can overwrite the file.
    # This would could save a lot of resource downstream, because clients could just check if the
    # file has been modified before obtaining the updated copy.
    # collectstatic does not modify a file in staticfiles directory if it
    # hasn't been updated in outputfiles.
    if ps.components.manifest_components(quick=True, ofuri=ps.ofuri).exists():
        logger.info(f"Generating manifest for {product_stream}")
        with open(f"{settings.STATIC_ROOT}/{product_stream}-{ps.pk}.json", "w") as fh:
            fh.write(ps.manifest)
    else:
        logger.info(
            f"Didn't find any released components for {product_stream}, "
            f"skipping manifest generation"
        )
