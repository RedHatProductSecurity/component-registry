from collections import Counter, defaultdict
from datetime import timedelta

from celery import states as celery_states
from celery.signals import beat_init
from celery.utils.log import get_task_logger
from celery_singleton import Singleton, clear_locks
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from django_celery_results.models import TaskResult

from config.celery import app
from config.utils import running_dev
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS, get_last_success_for_task

logger = get_task_logger(__name__)


@beat_init.connect
def setup_periodic_tasks(sender, **kwargs):
    def upsert_cron_task(module, task, **kwargs):
        crontab, _ = CrontabSchedule.objects.get_or_create(timezone=timezone.utc, **kwargs)
        PeriodicTask.objects.get_or_create(
            name=task, task=f"corgi.tasks.{module}.{task}", defaults={"crontab": crontab}
        )

    def upsert_interval_task(module, task, hours=None, minutes=None):
        if hours:
            interval, _ = IntervalSchedule.objects.get_or_create(
                every=hours, period=IntervalSchedule.HOURS
            )
        elif minutes:
            interval, _ = IntervalSchedule.objects.get_or_create(
                every=minutes, period=IntervalSchedule.MINUTES
            )
        else:
            raise ValueError(f"No interval value was specified when setting up {task} task")

        PeriodicTask.objects.get_or_create(
            name=task, task=f"corgi.tasks.{module}.{task}", defaults={"interval": interval}
        )

    # Ensure celery_singleton is not still blocking new tasks if the pod did not shut down cleanly.
    clear_locks(app)

    # Wipe old schedules
    CrontabSchedule.objects.get_queryset().delete()
    IntervalSchedule.objects.get_queryset().delete()

    if settings.COMMUNITY_MODE_ENABLED:
        upsert_cron_task("prod_defs", "update_products", hour=0, minute=0)
        upsert_cron_task("brew", "load_stream_brew_tags", hour=1, minute=0)
        upsert_cron_task("brew", "fetch_unprocessed_brew_tag_relations", hour=2, minute=0)
        upsert_cron_task("yum", "load_yum_repositories", hour=3, minute=0)
        upsert_cron_task("yum", "fetch_unprocessed_yum_relations", hour=4, minute=0)
        upsert_cron_task("manifest", "update_manifests", hour=5, minute=0)
        upsert_cron_task("monitoring", "email_failed_tasks", hour=6, minute=30)
        upsert_cron_task("monitoring", "expire_task_results", hour=7, minute=30)
    else:
        # Once a week on a Saturday fetch relations from all active CDN repos
        upsert_cron_task("pulp", "setup_pulp_relations", minute=0, hour=4, day_of_week=6)
        upsert_cron_task("manifest", "slow_ensure_root_upstreams", minute=0, hour=8, day_of_week=6)

        # Daily tasks, scheduled to a specific hour. For some reason, using hours=24 may not run the
        # task at all: https://github.com/celery/django-celery-beat/issues/221
        upsert_cron_task("errata_tool", "load_et_products", hour=0, minute=0)
        upsert_cron_task("prod_defs", "update_products", hour=1, minute=0)
        upsert_cron_task("pulp", "update_cdn_repo_channels", hour=2, minute=0)
        upsert_cron_task("rhel_compose", "save_composes", hour=3, minute=0)
        upsert_cron_task("rhel_compose", "get_builds", hour=4, minute=0)
        upsert_cron_task("brew", "load_stream_brew_tags", hour=5, minute=0)
        upsert_cron_task("brew", "fetch_unprocessed_brew_tag_relations", hour=6, minute=0)
        upsert_cron_task("pulp", "fetch_unprocessed_cdn_relations", hour=7, minute=0)
        upsert_cron_task("yum", "load_yum_repositories", hour=8, minute=0)
        upsert_cron_task("yum", "fetch_unprocessed_yum_relations", hour=9, minute=0)
        upsert_cron_task("managed_services", "refresh_service_manifests", hour=10, minute=0)
        upsert_cron_task("manifest", "update_manifests", hour=11, minute=0)
        upsert_cron_task("monitoring", "email_failed_tasks", hour=12, minute=45)
        upsert_cron_task("monitoring", "expire_task_results", hour=13, minute=45)


@app.task(base=Singleton, autoretry_for=(Exception,), retry_backoff=900, retry_jitter=False)
def email_failed_tasks():
    """Send email about failed Celery tasks within past 24 hours to Corgi developers who like spam
    If it failed to send, try again after 15 minutes, then 30 minutes, then give up"""
    # If a dev env runs more than 24 hours, and it's somehow able to send email, don't spam people.
    if running_dev():
        return

    failed_tasks_threshold = get_last_success_for_task("corgi.tasks.monitoring.email_failed_tasks")
    failed_tasks_max_threshold = timezone.now() - timedelta(days=3)
    # Don't send emails about tasks that failed more than three days ago. Otherwise, we may end
    # up reporting way too many errors, exceeding the allowed message size set by our SMTP server.
    max_threshold = max(failed_tasks_threshold, failed_tasks_max_threshold)

    failed_tasks = (
        TaskResult.objects.filter(
            status__in=(celery_states.FAILURE, celery_states.RETRY),
            date_done__gte=max_threshold,
        )
        .order_by("task_name", "date_done")
        .using("read_only")
    )

    failed_tasks_count = failed_tasks.count()
    subject = (
        f"Failed Corgi Celery tasks after {failed_tasks_threshold.date()}: {failed_tasks_count}"
    )

    report_body = f"The following Celery tasks failed since {max_threshold}:\n\n"
    if failed_tasks_count == 0:
        report_body += "No failed tasks! Hooray!"

    else:
        # Group task errors (args, kwargs, and the traceback) by task_name
        errors_by_task = defaultdict(list)
        for task in failed_tasks.iterator(chunk_size=100):
            failed_task = (task.task_args, task.task_kwargs, task.traceback or task.result)
            errors_by_task[task.task_name].append(failed_task)

        for task_name, errors in errors_by_task.items():
            # Create a list of unique errors (each unique triple) and report the total and
            # per-error numbers in each section.
            unique_errors = Counter(errors)
            report_body += f"# {task_name}: {sum(unique_errors.values())} total errors\n\n"

            # Sort from highest error count per unique error to lowest.
            unique_errors = sorted(unique_errors.items(), key=lambda x: x[1], reverse=True)
            for (task_args, task_kwargs, task_traceback), error_count in unique_errors:
                report_body += (
                    f"## {task_name}: {error_count} error(s) when called with:\n\n"
                    f"args={task_args}\nkwargs={task_kwargs}\n{task_traceback}\n"
                )
            report_body += "---\n\n"

    EmailMessage(
        subject=subject,
        body=report_body,
        to=settings.ADMINS,
        from_email=settings.SERVER_EMAIL,
    ).send()


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def expire_task_results():
    """Delete task results older than 30 days.

    To prevent the task results table to grow to huge numbers, remove any results that are
    30 days or older. This job mimics the built-in celery.backend_cleanup job but works with
    our schedules and is a bit more transparent in what it actually does.
    """
    expired_on = timezone.now() - timedelta(days=30)
    removed_count, _ = TaskResult.objects.filter(date_done__lt=expired_on).delete()
    logger.info("Removed %s expired task results", removed_count)

    return f"Removed {removed_count} expired task results"
