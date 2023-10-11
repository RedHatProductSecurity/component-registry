import importlib

import pytest
from django.db import connection
from rest_framework.test import APIClient

from corgi.api.constants import CORGI_API_VERSION
from corgi.core.models import ProductNode
from tests.factories import (
    ProductFactory,
    ProductStreamFactory,
    ProductVariantFactory,
    ProductVersionFactory,
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
    updated_stored_proc = importlib.import_module(
        "corgi.core.migrations.0094_fix_stored_proc_inactive_filter"
    )
    with django_db_blocker.unblock():
        with connection.cursor() as c:
            c.execute("DROP FUNCTION if exists rpmvercmp;"),
            c.execute(stored_proc.RPMVERCMP_STOREDPROC_SQL)
            c.execute("DROP FUNCTION if exists rpmvercmp_epoch;"),
            c.execute(stored_proc.RPMVERCMP_EPOCH_STOREDPROC_SQL),
            c.execute("DROP FUNCTION if exists get_latest_component;"),
            c.execute(updated_stored_proc.GET_LATEST_COMPONENT_STOREDPROC_SQL)


def setup_product(version_name: str = "", stream_name: str = ""):
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
        stream = ProductStreamFactory(products=product, productversions=version, active=True)
    variant = ProductVariantFactory(
        name="1", products=product, productversions=version, productstreams=stream
    )
    pnode = ProductNode.objects.create(parent=None, obj=product)
    pvnode = ProductNode.objects.create(parent=pnode, obj=version)
    psnode = ProductNode.objects.create(parent=pvnode, obj=stream)
    ProductNode.objects.create(parent=psnode, obj=variant)
    # This generates and saves the ProductModel properties of stream
    # AKA we link the ProductModel instances to each other
    stream.save_product_taxonomy()
    assert variant in stream.productvariants.get_queryset()
    return stream, variant


def filter_response(response):
    response["headers"].pop("Set-Cookie", None)
    response["headers"].pop("x-ausername", None)
    return response
