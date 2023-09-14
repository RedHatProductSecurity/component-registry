import logging

import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import GenericViewSet

from corgi.api import views
from tests.factories import ProductFactory

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


@pytest.mark.django_db(databases=("read_only",))
def test_viewset_ordering(api_path):
    """Test that all ViewSets define an ordering to prevent DRF pagination bugs"""
    for name, cls in views.__dict__.items():
        if not isinstance(cls, type) or not issubclass(cls, GenericViewSet):
            # Name defined in the module isn't a class, or isn't a ViewSet
            continue

        if name in (
            "GenericViewSet",
            "ReadOnlyModelViewSet",
            "StatusViewSet",
            "TagViewMixin",
            "ProductDataViewSet",
            "SoftwareBuildViewSet",
            "ComponentViewSet",
            "AppStreamLifeCycleViewSet",
            "ChannelViewSet",
        ):
            # Skip imported names and special cases which have no queryset
            # last four pass or fail depending on whether query planner
            # uses an index scan or a sorted table scan
            continue

        viewset = cls()
        if name == "AppStreamLifeCycleViewSet":
            # This viewset is ordered based on all fields in a unique constraint
            # and the query shouldn't contain some SQL to sort the objects
            # since rows are returned sorted in unique index order by default
            assert "Sort " not in viewset.get_queryset().explain()

        elif name == "ComponentViewSet":
            # Special case that needs to be initialized with a request object
            # Otherwise get_queryset() fails due to "ComponentViewSet has no attribute 'request'"
            viewset = cls(action_map={"get": "list"})
            product = ProductFactory(name="rhel")

            # Can also use Django's HTTPRequest or Django's RequestFactory
            # Didn't test DRF's HTTPRequest
            request = APIRequestFactory().get(f"{api_path}/components?ofuri={product.ofuri}")
            # Instead of converting above to a DRF Request,
            # you can also use viewset.initialize_request(request) with any of above types
            # It requires type X and mypy passes, but Pycharm hints complain request isn't type Y
            request = Request(request)
            viewset.setup(request)

            assert "Sort " in viewset.get_queryset().explain()

        else:
            # These viewsets are ordered based on some other fields
            # and the query should contain some SQL to sort the objects
            assert "Sort " in viewset.get_queryset().explain()
