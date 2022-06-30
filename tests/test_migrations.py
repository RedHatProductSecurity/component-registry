import pytest

pytestmark = pytest.mark.unit


@pytest.mark.django_db
def test_dummy():
    """Run a dummy test that always succeeds but requires a database connection.

    The point of this test is for pytest to create a test database, and run all migrations (see
    "corgi-migrations" Tox env in tox.ini) to see if any of them fail to apply.
    """
    assert True
