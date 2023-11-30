import importlib

import pytest
from django.contrib.postgres.operations import BtreeGinExtension, TrigramExtension
from django.db import connection
from rest_framework.test import APIClient

from corgi.api.constants import CORGI_API_VERSION
from corgi.core.constants import GET_LATEST_COMPONENT_STOREDPROC_SQL
from corgi.core.models import ProductNode
from tests.factories import (
    ProductFactory,
    ProductStreamFactory,
    ProductVariantFactory,
    ProductVersionFactory, ProductStreamNodeFactory, ProductVariantNodeFactory,
)


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def api_version():
    return CORGI_API_VERSION


@pytest.fixture
def api_path(api_version):
    return f"/api/{api_version}"


@pytest.fixture(scope="session")
def stored_proc(django_db_setup, django_db_blocker):
    """setup stored procedure"""
    # depends on corgi/core/migration/0092_install_stored_proc.py data migration
    stored_proc = importlib.import_module("corgi.core.migrations.0092_install_stored_proc")
    with django_db_blocker.unblock():
        with connection.cursor() as c:
            c.execute(stored_proc.RPMVERCMP_STOREDPROC_SQL)
            c.execute(stored_proc.RPMVERCMP_EPOCH_STOREDPROC_SQL),
            c.execute(GET_LATEST_COMPONENT_STOREDPROC_SQL)


def setup_product(
    version_name: str = "",
    stream_name: str = "",
    variant_node_type=ProductNode.ProductNodeType.DIRECT,
):
    product = ProductFactory()
    if version_name:
        version = ProductVersionFactory(name=version_name, products=product)
    else:
        version = ProductVersionFactory(products=product)
    if stream_name:
        stream = ProductStreamFactory(
            name=stream_name, products=product, productversions=version, active=True
        )
    else:
        psnode = ProductStreamNodeFactory()
    stream = psnode.obj

    pvariant = ProductVariantFactory(name="1")
    pvariant_node = ProductVariantNodeFactory(
        obj=pvariant, parent=psnode, node_type=variant_node_type
    )
    variant = pvariant_node.obj

    assert variant in stream.productvariants.get_queryset()
    return stream, variant


def filter_response(response):
    response["headers"].pop("Set-Cookie", None)
    response["headers"].pop("x-ausername", None)
    return response


@pytest.fixture(autouse=True)
def setup_gin_extension(request):
    """Setup pg gin and trigram extensions. Note: not required for tests to pass."""
    BtreeGinExtension(),
    TrigramExtension(),
