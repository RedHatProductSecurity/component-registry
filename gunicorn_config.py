from config.utils import running_dev

workers = 4
worker_class = "gevent"
# worker_connections = 10
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
