import logging

from celery import Celery  # type: ignore[attr-defined]
from celery.app import trace
from celery.app.log import TaskFormatter
from celery.signals import after_setup_logger, after_setup_task_logger, worker_ready
from celery_singleton import clear_locks

from config.settings.base import LOG_DATE_FORMAT, LOG_FORMAT_END, LOG_FORMAT_START

logger = logging.getLogger(__name__)

app = Celery("corgi")
app.config_from_object("django.conf:settings", namespace="CELERY")
# Below will look for a module named "tasks" in all INSTALLED_APPS
# corgi.tasks.tasks has helper code to import all tasks
# from sibling modules like corgi.tasks.brew and corgi.tasks.monitoring
# So all tasks in any corgi.tasks submodule are automatically discovered
# And no config changes are needed when a new submodule is added
app.autodiscover_tasks()


CELERY_TASK_LOG_FORMAT = (
    f"{LOG_FORMAT_START}, process_name=%(processName)s, task_name=%(task_name)s, "
    f"task_id=%(task_id)s, {LOG_FORMAT_END}"
)


@worker_ready.connect
def unlock_all(**kwargs):
    clear_locks(app)


@after_setup_logger.connect
def after_setup_logger_handler(logger, *args, **kwargs):
    """Override the celery logger to include splunk friendly timestamp"""
    formatter = TaskFormatter(f"{LOG_FORMAT_START}, {LOG_FORMAT_END}")
    formatter.datefmt = LOG_DATE_FORMAT
    for handler in logger.handlers:
        handler.setFormatter(formatter)


@after_setup_task_logger.connect
def after_setup_task_logger_handler(logger, *args, **kwargs):
    """Override the celery task logging to include key/value pairs"""
    formatter = TaskFormatter(f"{CELERY_TASK_LOG_FORMAT}")
    formatter.datefmt = LOG_DATE_FORMAT
    for handler in logger.handlers:
        handler.setFormatter(formatter)


log_common = 'task_name=%(name)s, task_id=%(id)s, task_args="%(args)s", task_kwargs="%(kwargs)s"'

# Closes the message log attribute early and adds key value pairs
# Avoids a trailing " by adding an extra 'x' key
trace.LOG_SUCCESS = f"""\
" status=SUCCESS, result=%(return_value)s, {log_common} task_runtime=%(runtime)ss x="\
"""
