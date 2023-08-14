from celery import Celery  # type: ignore[attr-defined]
from celery.app import trace

app = Celery("corgi")
app.config_from_object("django.conf:settings", namespace="CELERY")
# Below will look for a module named "tasks" in all INSTALLED_APPS
# corgi.tasks.tasks has helper code to import all tasks
# from sibling modules like corgi.tasks.brew and corgi.tasks.monitoring
# So all tasks in any corgi.tasks submodule are automatically discovered
# And no config changes are needed when a new submodule is added
app.autodiscover_tasks()

log_common = 'task_name=%(name)s, task_id=%(id)s, task_args="%(args)s", task_kwargs="%(kwargs)s"'

# Closes the message log attribute early and adds key value pairs
trace.LOG_SUCCESS = (
    f'", status=SUCCESS, result=%(return_value)s, {log_common}, task_runtime=%(runtime)ss'
)
