"""
corgi api constants
"""

from datetime import timedelta, timezone

# REST API version
CORGI_API_VERSION: str = "v1"

# include meta_attr column on all queries (useful for debugging)
CORGI_VIEW_META_ATTR = False

TZ_OFFSET = 0  # GMT
TZINFO = timezone(timedelta(hours=TZ_OFFSET))
