from datetime import timedelta
from ssl import SSLError

from django.db.utils import InterfaceError
from django.utils import timezone
from django_celery_results.models import TaskResult
from psycopg2.errors import InterfaceError as Psycopg2InterfaceError
from requests.exceptions import RequestException

from corgi.core.models import ProductComponentRelation

BACKOFF_KWARGS = {"max_tries": 5, "jitter": None}

# InterfaceError is "connection already closed" or some other connection-level error
# This can be safely retried. OperationalError is potentially a connection-level error
# But can also be due to database operation failures that should NOT be retried
RETRYABLE_ERRORS = (
    InterfaceError,
    Psycopg2InterfaceError,
    RequestException,
    SSLError,
    TimeoutError,
)

RETRY_KWARGS = {
    "retry_backoff": 300,
    "retry_jitter": False,
}


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


def get_last_success_for_task(task_name):
    """Return the timestamp of the last successful task so we can fetch updates since that time.

    For extra measure, the last success timestamp is offset by 30 minutes to overlap. If no record
    of a job that succeeded exists in our results DB, return a refresh timestamp of 3 days ago.
    If that still misses stuff, it indicates a longer outage and updates should be scheduled
    manually.
    """
    # Specifically query for jobs without any task kwargs so prevent refreshing only after a
    # successful manually triggered task for a particular resource.
    last_success = (
        TaskResult.objects.filter(task_name=task_name, task_kwargs='"{}"', status="SUCCESS")
        .order_by("-date_done")
        .values_list("date_done", flat=True)
        .first()
    )
    return (
        last_success - timedelta(minutes=30) if last_success else timezone.now() - timedelta(days=3)
    )


def _create_relations(build_ids, external_system_id, product_ref, relation_type) -> int:
    no_of_relations = 0
    for build_id in build_ids:
        _, created = ProductComponentRelation.objects.get_or_create(
            external_system_id=external_system_id,
            product_ref=product_ref,
            build_id=build_id,
            defaults={"type": relation_type},
        )
        if created:
            no_of_relations += 1
    return no_of_relations
