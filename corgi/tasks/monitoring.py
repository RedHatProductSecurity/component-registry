import logging
from datetime import timedelta

from celery.signals import beat_init
from celery_singleton import Singleton, clear_locks
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from django_celery_results.models import TaskResult

from config.celery import app
from config.utils import running_dev

from .common import get_last_success_for_task

logger = logging.getLogger(__name__)


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

    # Once a week on a Saturday fetch relations from all active CDN repos
    # Revisit if this is still necessary after CORGI-257 is complete
    upsert_cron_task("pulp", "setup_pulp_relations", minute=0, hour=4, day_of_week=6)
    upsert_cron_task("pulp", "fetch_unprocessed_cdn_relations", minute=0, hour=8, day_of_week=6)

    # Daily tasks, scheduled to a specific hour. For some reason, using hours=24 may not run the
    # task at all: https://github.com/celery/django-celery-beat/issues/221
    upsert_cron_task("errata_tool", "load_et_products", hour=0, minute=0)
    upsert_cron_task("prod_defs", "update_products", hour=1, minute=0)
    upsert_cron_task("pulp", "update_cdn_repo_channels", hour=2, minute=0)
    upsert_cron_task("rhel_compose", "save_composes", hour=3, minute=0)
    upsert_cron_task("rhel_compose", "get_builds", hour=4, minute=0)
    upsert_cron_task("brew", "load_brew_tags", hour=5, minute=0)
    upsert_cron_task("brew", "slow_fetch_unprocessed_brew_tag_relations", hour=6, minute=0)
    upsert_cron_task("monitoring", "email_failed_tasks", hour=10, minute=45)

    # Automatic task result expiration is currently disabled
    # We are required to keep UMB task results
    # Only run manually if DB is running out of space, or similar
    # upsert_cron_task("monitoring", "expire_task_results", hour=13, minute=0)


@app.task(base=Singleton, autoretry_for=(Exception,), retry_backoff=900, retry_jitter=False)
def email_failed_tasks():
    """Send email about failed Celery tasks within past 24 hours to Corgi developers who like spam
    If it failed to send, try again after 15 minutes, then 30 minutes, then give up"""
    # If a dev env runs more than 24 hours, and it's somehow able to send email, don't spam people.
    if running_dev():
        return

    failed_tasks_threshold = get_last_success_for_task("corgi.tasks.monitoring.email_failed_tasks")
    failed_tasks = (
        TaskResult.objects.filter(
            status__exact="FAILURE",
            date_done__gte=failed_tasks_threshold,
        )
        .order_by("task_name", "date_done")
        .values_list("task_name", "task_args", "task_kwargs", "result", "traceback")
    )

    subject = (
        f"Failed Corgi Celery tasks after "
        f"{failed_tasks_threshold.date()}: {failed_tasks.count()}"
    )

    failed_tasks = "\n".join(
        f"{task_name}: args={task_args}, kwargs={task_kwargs}\n"
        f"result={result}\n"
        f"{traceback}\n"
        for (task_name, task_args, task_kwargs, result, traceback) in failed_tasks.iterator(
            chunk_size=500
        )
    )

    EmailMessage(
        subject=subject,
        body=failed_tasks,
        to=settings.ADMINS,
        from_email=settings.SERVER_EMAIL,
    ).send()


@app.task
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
