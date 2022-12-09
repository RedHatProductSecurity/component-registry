from distutils.util import strtobool

from .base import *  # noqa: F401, F403

DEBUG = True

SECRET_KEY = "helloworld"  # pragma: allowlist secret

SESSION_COOKIE_SECURE = False

# When testing email functionality locally, you can start a debugging SMTP server that
# prints out the received emails with:
# python -m smtpd -n -c DebuggingServer localhost:1025
# with podman-compose add a service such as:
#  sdengine-mail:
#    container_name: sdengine-mail
#    image: sdengine
#    command: python3 -m smtpd -n -c DebuggingServer localhost:1025
#    ports:
#      - "1025:1025"

ALLOWED_HOSTS = ["*"]

UMB_BREW_MONITOR_ENABLED = strtobool("false")

COMMUNITY_MODE_ENABLED = strtobool("true")

# Need to figure out LOOKASIDE_CACHE_URL for community as least
SCA_ENABLED = strtobool("false")
