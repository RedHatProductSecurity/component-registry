import pytest

from corgi.core.mixins import TimeStampedModel

from .factories import ContainerImageComponentFactory, ProductStreamFactory

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


def test_timestamped_mixin():
    c2 = ContainerImageComponentFactory()
    assert isinstance(c2, TimeStampedModel)
    ps1 = ProductStreamFactory()
    assert isinstance(ps1, TimeStampedModel)
