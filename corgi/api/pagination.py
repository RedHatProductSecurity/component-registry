import logging

from django.db import connection
from rest_framework.pagination import LimitOffsetPagination

logger = logging.getLogger(__name__)


class FasterPageNumberPagination(LimitOffsetPagination):
    def get_count(self, queryset):
        """more efficient REST API count"""
        if "WHERE" in str(queryset.query):
            # if queryset conditions has filters then we revert to queryset count
            # using primary key which ensures we hit an index
            return queryset.only("pk").count()
        # otherwise estimate REST API count using postgres reltuples which is much
        # faster
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT reltuples FROM pg_class WHERE relname = %s", [queryset.model._meta.db_table]
            )
            return int(cursor.fetchone()[0])
