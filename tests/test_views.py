import logging

import pytest
from rest_framework.viewsets import GenericViewSet

from corgi.api import views

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.unit


def test_health_safe_method(client):
    response = client.get("/api/healthy")
    assert response.status_code == 200


def test_health_unsafe_method(client):
    response = client.post("/api/healthy")
    assert response.status_code == 405  # Method not allowed


def test_not_found(client):
    response = client.get("/does/not/exist")
    assert response.status_code == 404


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_viewset_ordering(api_path, stored_proc):
    """Test that all ViewSets define an ordering to prevent DRF pagination bugs"""
    for name, cls in views.__dict__.items():
        if not isinstance(cls, type) or not issubclass(cls, GenericViewSet):
            # Name defined in the module isn't a class, or isn't a ViewSet
            continue

        if name in (
            "GenericViewSet",
            "ReadOnlyModelViewSet",
            "StatusViewSet",
            "ProductDataViewSet",
        ):
            # Skip imported names and special cases which have no queryset
            continue

        sql = cls.queryset.explain()
        if name in (
            "AppStreamLifeCycleViewSet",
            "ChannelViewSet",
            "ComponentViewSet",
            "SoftwareBuildViewSet",
        ) or issubclass(cls, views.ProductDataViewSet):
            # These viewsets are ordered based on all fields in a unique constraint
            # or a single field which sets unique=True and has a Django-managed index
            # and the query shouldn't contain some SQL to sort the objects
            # since rows are returned sorted in unique index order by default

            # But the query planner may choose to sort based on unique / indexed fields
            # instead of using the index for those fields directly
            # https://stackoverflow.com/questions/5203755/
            # why-does-postgresql-perform-sequential-scan-on-indexed-column

            # So make sure that we get unique / sorted output either way
            # and don't accept / fail the test if we receive completely unsorted output
            assert "Index Scan " in sql or "Sort " in sql

        else:
            # These viewsets are ordered based on some other fields
            # and the query should contain some SQL to sort the objects
            assert "Sort " in sql
            assert "Index Scan " not in sql
