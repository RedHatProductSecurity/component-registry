import os
from datetime import timedelta
from json import JSONDecodeError
from ssl import SSLError

from django.db.utils import InterfaceError
from django.utils import timezone
from django_celery_results.models import TaskResult
from psycopg2.errors import InterfaceError as Psycopg2InterfaceError
from requests.exceptions import RequestException

BACKOFF_KWARGS = {"max_tries": 5, "jitter": None}

# InterfaceError is "connection already closed" or some other connection-level error
# This can be safely retried. OperationalError is potentially a connection-level error
# But can also be due to database operation failures that should NOT be retried
RETRYABLE_ERRORS = (
    InterfaceError,
    JSONDecodeError,
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
    if getattr(e, "response", None):
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


def running_local():
    return os.getenv("DJANGO_SETTINGS_MODULE") == "config.settings.dev"
