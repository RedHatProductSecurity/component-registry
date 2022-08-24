from functools import wraps

import redis
from django.apps import apps
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_safe

from config.celery import app as celery_app

CELERY_WORKERS: list = []
CELERY_WORKERS_LAST_REFRESH = timezone.now().timestamp()
CELERY_WORKERS_REFRESH_PERIOD = 900  # Once every 15 minutes refresh list of workers


def inspect_celery():
    """Return the Celery inspect object."""
    return celery_app.control.inspect(CELERY_WORKERS)


def check_app_status(func):
    """Check if Redis is up; if it is, check if Celery workers are up."""

    @wraps(func)
    def wrapped(*args, **kwargs):
        r = redis.Redis.from_url(settings.CELERY_BROKER_URL)
        try:
            r.ping()
        except redis.ConnectionError:
            return render(
                args[0],  # Response object
                "app_status.html",
                {"msg": "Redis message broker is not running..."},
            )

        global CELERY_WORKERS
        global CELERY_WORKERS_LAST_REFRESH

        # If we are due to refresh - wipe the list of workers to force full refresh
        if timezone.now().timestamp() - CELERY_WORKERS_LAST_REFRESH > CELERY_WORKERS_REFRESH_PERIOD:
            CELERY_WORKERS = []
            CELERY_WORKERS_LAST_REFRESH = timezone.now().timestamp()

        i = inspect_celery()
        # This is much faster if CELERY_WORKERS is set to current set of worker names
        ping_replies = i.ping()
        if (
            not ping_replies
            or len(ping_replies) < len(CELERY_WORKERS)
            or not all([r["ok"] == "pong" for r in ping_replies.values()])
        ):
            # Reset worker list - maybe they were redeployed?
            CELERY_WORKERS = []
            CELERY_WORKERS_LAST_REFRESH = timezone.now().timestamp()
            return render(
                args[0],  # Response object
                "app_status.html",
                {"msg": "Celery workers are not running..."},
            )

        # Update list of current workers
        CELERY_WORKERS = list(ping_replies.keys())
        return func(*args, **kwargs)

    return wrapped


@require_safe
def data_list(request: HttpRequest) -> HttpResponse:
    """Renders a count of all cached resources in the DB."""
    models = sorted(
        apps.get_app_config("core").get_models(), key=lambda model: model._meta.verbose_name
    )
    return render(
        request,
        "data.html",
        {
            "counts": [
                (model._meta.verbose_name_plural, model.objects.count()) for model in models
            ],
            "nbar": "data",  # Navbar identifier
        },
    )


@check_app_status
@require_safe
def tasks_list(request):
    inspect = inspect_celery()
    tasks = []
    for task in inspect.registered("__doc__")[CELERY_WORKERS[0]]:
        task_name, _, desc = task.partition(" ")
        desc = (
            desc.replace("[__doc__=", "").rstrip("]").replace("\n", "").strip() if desc else "N/A"
        )
        tasks.append({"name": task_name, "description": desc})

    return render(
        request,
        "task_list.html",
        {
            "tasks": sorted(tasks, key=lambda x: x["name"]),
            "running": False,
            "nbar": "scheduleable_tasks",
        },
    )


@check_app_status
@require_safe
def running_tasks(request):
    def add_tasks(ret, task_dict, status, scheduled=False):
        for worker in task_dict:
            for task in task_dict[worker]:
                if scheduled:
                    # There's another level of nesting for scheduled tasks.
                    task = task["request"]

                task["status"] = status
                if task.get("time_start"):
                    task["time_start"] = timezone.datetime.utcfromtimestamp(task["time_start"])
                ret.append(task)
        # Return None to implicitly indicate ret (list of tasks) was mutated

    inspect = inspect_celery()
    tasks = []  # A list of task dicts
    add_tasks(tasks, inspect.active(), "running")
    add_tasks(tasks, inspect.reserved(), "pending")
    add_tasks(tasks, inspect.scheduled(), "scheduled", scheduled=True)
    # Sort the list of tasks so they don't jump around each time the display view is refreshed
    # Based on status (to match existing behavior) and name
    # Then start time for running tasks with same name
    # And args / kwargs for pending tasks with same name but no start time
    unstarted_task_datetime = timezone.datetime.utcfromtimestamp(0)
    tasks = sorted(
        tasks,
        key=lambda x: (
            x["status"],
            x["name"],
            x.get("time_start", unstarted_task_datetime),
            x["args"],
            x["kwargs"],
        ),
    )
    r = redis.Redis.from_url(settings.CELERY_BROKER_URL)
    return render(
        request,
        "task_list.html",
        {
            "fast_queue_len": r.llen("fast"),
            "slow_queue_len": r.llen("slow"),
            "tasks": tasks,
            "running": True,
            "nbar": "running_tasks",
        },
    )


@require_safe
def home(request: HttpRequest) -> HttpResponse:
    """Serve home page"""
    return render(request, "index.html")
