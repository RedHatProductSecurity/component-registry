import pytest

from corgi.tasks.tagging import (
    apply_managed_services_no_manifest_tags,
    apply_middleware_stream_no_manifest_tags,
    apply_rhel_8_9_z_stream_no_manifest_tags,
)
from tests.conftest import setup_product

pytestmark = pytest.mark.unit


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_rhel_8_9_no_manifest_tagging():
    stream, _ = setup_product(version_name="rhel-8", stream_name="rhel-8.1.0.z")
    apply_rhel_8_9_z_stream_no_manifest_tags("no_manifest", True)
    stream.refresh_from_db()
    assert "no_manifest" in stream.tags.values_list("name", flat=True)


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_middleware_no_manifest_tag():
    stream, _ = setup_product()
    product = stream.products
    product.meta_attr["business_unit"] = "Core Middleware"
    product.save()
    apply_middleware_stream_no_manifest_tags("no_manifest", True)
    stream.refresh_from_db()
    assert "no_manifest" in stream.tags.values_list("name", flat=True)


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_managed_service_no_manifest_tag():
    stream, _ = setup_product()
    stream.meta_attr["managed_service_components"] = {"name": "test"}
    stream.save()
    apply_managed_services_no_manifest_tags("no_manifest", True)
    stream.refresh_from_db
    assert "no_manifest" in stream.tags.values_list("name", flat=True)
