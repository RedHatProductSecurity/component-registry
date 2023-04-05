import socket

from .base import *  # noqa: F401, F403

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")  # noqa: F405

ALLOWED_HOSTS = [
    # Allow local host's IP address and hostname for health probes
    socket.gethostname(),
    socket.gethostbyname(socket.gethostname()),
    CORGI_DOMAIN_BASE,  # noqa: F405
]

CSP_UPGRADE_INSECURE_REQUESTS = True

# We trust OpenShift's HAProxy to strip the X-Forwarded-Proto header and to set it to "https" if
# the request came over HTTPS from the client to HAProxy.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
