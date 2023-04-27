import logging
from unittest.mock import patch

import pytest

from corgi.core.models import ProductComponentRelation, SoftwareBuild
from corgi.tasks.brew import slow_fetch_brew_build, slow_fetch_modular_build
from corgi.tasks.common import create_relations

from .factories import ProductStreamFactory, SoftwareBuildFactory

logger = logging.getLogger()
pytestmark = [
    pytest.mark.unit,
    pytest.mark.django_db(databases=("default",)),
]


@patch("corgi.tasks.brew.slow_fetch_brew_build.delay")
@patch("corgi.tasks.brew.slow_fetch_modular_build.delay")
def test_update_products_after_relation_creation(fetch_modular_build_task, fetch_brew_build_task):
    """This tests that we always call slow_fetch_*_build task after creating a relation which
    calls save_product_taxonomy on the SoftwareBuild and it's child components to update the
    product taxonomy based on the newly created relation"""
    stream = ProductStreamFactory()
    sb = SoftwareBuildFactory(build_type=SoftwareBuild.Type.BREW)

    create_relations(
        (sb.build_id,),
        SoftwareBuild.Type.BREW,
        "mock-brew-tag",
        stream.name,
        ProductComponentRelation.Type.BREW_TAG,
        slow_fetch_modular_build,
    )

    fetch_modular_build_task.assert_called_once()

    # For CENTOS builds we don't call fetch_modular_build, but slow_fetch_brew build directly
    # because the openstack-rdo product stream doesn't use modular builds and the collector models
    # still use build_id as a primary key, so they could overlap with Fedora builds
    sb = SoftwareBuildFactory(build_type=SoftwareBuild.Type.CENTOS)

    create_relations(
        (sb.build_id,),
        SoftwareBuild.Type.CENTOS,
        "cloud8s-openstack-xena-release",
        stream.name,
        ProductComponentRelation.Type.BREW_TAG,
        slow_fetch_brew_build,
    )

    fetch_brew_build_task.assert_called_once()
