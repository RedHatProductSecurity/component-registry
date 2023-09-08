# avoid MonkeyPatchWarning error/warnings
from gevent import monkey

from config.utils import running_dev

monkey.patch_all(thread=False, select=False)
workers = 4  # this can probably be increased
worker_class = "gevent"
reuse_port = True

bind = "0.0.0.0:8008"
proc_name = "corgi"

errorlog = "-"
loglevel = "info"
accesslog = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Ensure wsgi.url_scheme is set to HTTPS, by trusting the X_FORWARDED_PROTO header set by the proxy
forwarded_allow_ips = "*"

timeout = 300

if not running_dev():
    # Saves memory in the worker process, but breaks --reload
    preload_app = True
    # avoid restarting gunicorn and leave it to pod restarts to handle memory leaks
    max_requests = 0
    graceful_timeout = 800  # if a restart must happen then let it be graceful
    keepalive = 60  # specifically this should be a value larger then nginx setting
    # ref: https://github.com/benoitc/gunicorn/issues/1978
    max_requests_jitter = 0
else:
    # Support hot-reloading of Gunicorn / Django when files change
    reload = True
