from django.conf import settings
from django.db import connection
from rest_framework.pagination import LimitOffsetPagination


class FasterPageNumberPagination(LimitOffsetPagination):
    def get_count(self, queryset):
        """more efficient REST API count"""
        if not (queryset):
            return 0
        if not (settings.OPTIMISE_REST_API_COUNT) or "WHERE" in str(queryset.query):
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
