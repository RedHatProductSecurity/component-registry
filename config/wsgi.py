import os

from gevent.monkey import patch_ssl

if os.getenv("RUNNING_GUNICORN"):
    # Monekypatch the SSL module before preloading the app to avoid warnings / odd behavior
    # Other modules that can be monkeypatched are not imported by the WSGI application
    # patching only what's required is safer, since gunicorn hasn't forked workers yet
    patch_ssl()


from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
