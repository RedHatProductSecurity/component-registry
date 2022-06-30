import pytest

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
