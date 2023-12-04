import subprocess
from datetime import datetime, timedelta
from typing import Optional

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db.utils import InterfaceError as DjangoInterfaceError
from django.utils import timezone
from django_celery_results.models import TaskResult
from psycopg2.errors import InterfaceError as Psycopg2InterfaceError
from redis.exceptions import ConnectionError as RedisConnectionError
from requests.exceptions import RequestException

from config.celery import app
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    SoftwareBuild,
)

logger = get_task_logger(__name__)

BACKOFF_KWARGS = {"max_tries": 5, "jitter": None}

# DjangoInterfaceError should just be a wrapper around Psycopg2InterfaceError
# But check for and retry both just in case
# InterfaceError is "connection already closed" or some other connection-level error
# This can be safely retried. OperationalError is potentially a connection-level error
# But can also be due to database operation failures that should NOT be retried
RETRYABLE_ERRORS = (
    DjangoInterfaceError,
    Psycopg2InterfaceError,
    RedisConnectionError,
    RequestException,
)

RETRY_KWARGS = {
    "retry_backoff": 300,
    "retry_jitter": False,
}

BUILD_TYPE = SoftwareBuild.Type.KOJI if settings.COMMUNITY_MODE_ENABLED else SoftwareBuild.Type.BREW


def fatal_code(e):
    """Do not retry on 4xx responses."""
    # Handle requests.exceptions.RequestException
    # 408 is "Request Timeout" that Brew sometimes returns, which can be retried safely
    # Note http.client.RemoteDisconnected errors have a response attr
    # but it's set to None / doesn't have a status code
    # so hasattr doesn't work, and getattr without "is not None" doesn't work either
    # because response objects are True for 200ish codes, False for 400ish codes
    if getattr(e, "response", None) is not None:
        return 400 <= e.response.status_code < 500 and e.response.status_code != 408


def get_last_success_for_task(task_name: str) -> datetime:
    """Return the timestamp of the last successful task so we can fetch updates since that time.

    For extra measure, the last success timestamp is offset by 30 minutes to overlap. If no record
    of a job that succeeded exists in our results DB, return a refresh timestamp of 3 days ago.
    If that still misses stuff, it indicates a longer outage and updates should be scheduled
    manually.
    """
    last_success = (
        TaskResult.objects.filter(task_name=task_name, status="SUCCESS")
        .order_by("-date_created")
        .values_list("date_created", flat=True)
        .using("read_only")
        .first()
    )
    return (
        last_success - timedelta(minutes=30) if last_success else timezone.now() - timedelta(days=3)
    )


def create_relations(
    build_ids: tuple,
    build_type: SoftwareBuild.Type,
    external_system_id: str,
    product_ref: str,
    relation_type: ProductComponentRelation.Type,
    refresh_task: Optional[app.task],
) -> int:
    no_of_relations = 0
    for build_id in build_ids:
        _, created = ProductComponentRelation.objects.get_or_create(
            external_system_id=external_system_id,
            product_ref=product_ref,
            build_id=build_id,
            build_type=build_type,
            defaults={"type": relation_type},
        )
        if created:
            # When creating relations via fetch_brew_build we call save_product_taxonomy right after
            # we call this function, so no need to refresh the build.
            if refresh_task:
                # Similar to fetch_unprocessed_relations
                # This skips use of the Collector models for builds in the CENTOS koji instance
                # It was done to avoid updating the collector models not to use build_id as
                # a primary key. It's possible because the only product stream (openstack-rdo)
                # stored in CENTOS koji doesn't use modules
                refresh_task_kwargs = {"build_id": build_id}
                if build_type == SoftwareBuild.Type.CENTOS:
                    refresh_task_kwargs["build_type"] = build_type
                # Daily tasks to create relations should finish ASAP
                refresh_task.apply_async(kwargs=refresh_task_kwargs, priority=0)
            no_of_relations += 1
    return no_of_relations


def run_external(
    command: list[str], *args, **kwargs
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Simple wrapper function to securely run an external command using the subprocess module
    Raises a CalledProcessError by default if the command returned a non-zero exit code"""
    logger.info(f"Running external command: {command}")
    result = subprocess.run(command, *args, **kwargs, capture_output=True, check=True, text=True)
    logger.info(f"Command completed with result: {result}")
    if result.stderr:
        # Treat warnings like errors even if the command had a successful exit code
        raise ValueError(f"Command {command} failed: {result.stderr}")
    output = result.stdout.splitlines()

    return result, output


def set_license_declared_safely(obj: Component, license_declared_raw: str) -> None:
    """Save a declared license onto a Component, without erasing any existing value"""
    if license_declared_raw:
        # Any non-empty license here should be reported
        # We only rely on OpenLCS if we don't know the license_declared
        # But we can't set license_declared in update_or_create
        # If the license in the metadata is an empty string and we are reprocessing,
        # we might erase the license that OpenLCS provided
        # They cannot erase any licenses we set ourselves (when the field is not empty)
        # API endpoint blocks this (400 Bad Request)
        # Use .update() to avoid possible race conditions
        # We only update if the current license in the DB doesn't match the new value
        Component.objects.filter(pk=obj.pk).exclude(
            license_declared_raw=license_declared_raw
        ).update(license_declared_raw=license_declared_raw)
    # else the value from Brew is an empty string
    # so don't overwrite any value OpenLCS may have submitted


def save_node(
    node_type: str, parent: Optional[ComponentNode], related_component: Component
) -> tuple[ComponentNode, bool]:
    """Helper function that wraps ComponentNode creation"""
    node, created = ComponentNode.objects.get_or_create(
        type=node_type,
        parent=parent,
        purl=related_component.purl,
        defaults={"obj": related_component},
    )
    return node, created


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def slow_save_taxonomy(build_id: str, build_type: str) -> None:
    """Helper function to call save_product_taxonomy()
    and save_component_taxonomy() in a separate task
    to reduce the risk of timeouts in slow_fetch_brew_build"""
    # This task has implicit priority 0 so data is linked and becomes searchable ASAP
    logger.info(f"Saving product taxonomy for {build_type} build {build_id}")
    build = SoftwareBuild.objects.get(build_id=build_id, build_type=build_type)
    build.save_product_taxonomy()

    # Below only saves the component taxonomy for the root component
    # There should only be one root component / node per build
    # but there are some historical data issues
    # TODO: Save the taxonomy for all components in the tree, CORGI-739
    #  We tried to do this in CORGI-730 but it caused SoftTimeLimitExceeded errors
    #  We may need to switch from GenericForeignKey to regular ForeignKey first
    logger.info(f"Saving component taxonomy for {build_type} build {build_id}")
    for root_component in build.components.get_queryset():
        root_component.save_component_taxonomy()
    logger.info(f"Finished saving taxonomies for {build_type} build {build_id}")
