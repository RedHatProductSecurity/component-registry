from celery.utils.log import get_task_logger
from celery_singleton import Singleton

from config.celery import app
from corgi.collectors.models import CollectorSpdxLicense
from corgi.collectors.spdx import Spdx
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = get_task_logger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def update_spdx_license_list() -> str:
    return Spdx.get_spdx_license_list()


def valid_spdx_license_identifier(identifier: str) -> bool:
    sans_or_later_identifier = identifier.removesuffix("+")
    return CollectorSpdxLicense.objects.filter(identifier=sans_or_later_identifier).exists()
