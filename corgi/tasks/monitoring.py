import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django_celery_results.models import TaskResult

from config.celery import app

from .common import get_last_success_for_task, running_local

logger = logging.getLogger(__name__)


@app.task(autoretry_for=(Exception,), retry_backoff=900, retry_jitter=False)
def email_failed_tasks():
    """Send email about failed Celery tasks within past 24 hours to Corgi developers who like spam
    If it failed to send, try again after 15 minutes, then 30 minutes, then give up"""
    # If a dev env runs more than 24 hours, and it's somehow able to send email, don't spam people.
    if running_local():
        return

    failed_tasks_threshold = get_last_success_for_task("corgi.tasks.monitoring.email_failed_tasks")
    failed_tasks = TaskResult.objects.filter(
        status__exact="FAILURE",
        date_done__gte=failed_tasks_threshold,
    ).order_by("task_name", "date_done")

    subject = (
        f"Failed Corgi Celery tasks after "
        f"{failed_tasks_threshold.date()}: {failed_tasks.count()}"
    )

    failed_tasks = "\n".join(
        f"{task.task_name}: args={task.task_args}, kwargs={task.task_kwargs}\n"
        f"result={task.result}\n"
        f"{task.traceback}\n"
        for task in failed_tasks
    )

    email = EmailMessage(
        subject=subject,
        body=failed_tasks,
        to=settings.FAILED_CELERY_TASK_SUBSCRIBERS,
        from_email=settings.SERVER_EMAIL,
    )
    email.send()
