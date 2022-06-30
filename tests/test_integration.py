import pytest
import requests

pytestmark = pytest.mark.integration


class TestIntegration(object):
    """Integration tests

    The Python requests library is used to independently test the API.
    """

    def test_healthy(self, test_scheme_host):
        """Access healthy API using requests."""
        response = requests.get(
            f"{test_scheme_host}/api/healthy", headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        assert response.status_code == 200

    def test_status(self, test_scheme_host):
        """Access status API using requests."""
        response = requests.get(
            f"{test_scheme_host}/api/v1/status", headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        assert response.status_code == 200

    def test_products(
        self,
        test_scheme_host,
    ):
        """Access products API using requests."""
        response = requests.get(
            f"{test_scheme_host}/api/v1/products",
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        assert response.status_code == 200

    def test_components(
        self,
        test_scheme_host,
    ):
        """Access components API using requests."""
        response = requests.get(
            f"{test_scheme_host}/api/v1/components",
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        assert response.status_code == 200
