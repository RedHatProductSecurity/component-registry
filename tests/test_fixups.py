import pytest

from corgi.core.fixups import cpe_lookup

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


def test_cpe_lookup():
    assert cpe_lookup("rhui-4") == ["cpe:/a:redhat:rhui:4::el8"]
    assert cpe_lookup("non-existant-product-stream") == []
