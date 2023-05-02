import pytest
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


def setup_product():
    product = ProductFactory()
    version = ProductVersionFactory(products=product)
    stream = ProductStreamFactory(products=product, productversions=version)
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
