"""
corgi api constants
"""

from django.conf import settings

from config import utils

# REST API version
CORGI_API_VERSION: str = "v1"

# Generic URL prefix
if not utils.running_dev():
    CORGI_API_URL = f"https://{settings.CORGI_DOMAIN}/api/{CORGI_API_VERSION}"
    CORGI_STATIC_URL = f"https://{settings.CORGI_DOMAIN}{settings.STATIC_URL}"
else:
    CORGI_API_URL = f"http://localhost:8008/api/{CORGI_API_VERSION}"
    CORGI_STATIC_URL = f"http://localhost:8008{settings.STATIC_URL}"
