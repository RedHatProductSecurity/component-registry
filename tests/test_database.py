import pytest
from django.db import connection

pytestmark = pytest.mark.unit


@pytest.mark.django_db
def test_stored_proc_exists():
    """Check if stored procs exist."""

    with connection.cursor() as cursor:
        cursor.execute("select 'get_latest_component'::regproc;")
        row = cursor.fetchone()

    assert row == ("get_latest_component",)

    with connection.cursor() as cursor:
        cursor.execute("select 'rpmvercmp'::regproc;")
        row = cursor.fetchone()

    assert row == ("rpmvercmp",)


@pytest.mark.django_db
def test_rpmvercmp_stored_proc():
    """Basic check if rpmvercmp works  (Note- behaviour is tested in test_model.py)."""

    with connection.cursor() as cursor:
        cursor.execute("select * from rpmvercmp('1.2.3','1.2.5');")
        row = cursor.fetchone()

    assert row == (1,)

    with connection.cursor() as cursor:
        cursor.execute("select * from rpmvercmp('1.3.3','1.4.5');")
        row = cursor.fetchone()

    assert row == (-1,)

    with connection.cursor() as cursor:
        cursor.execute("select * from rpmvercmp('1.8.3','1.3.5');")
        row = cursor.fetchone()

    assert row == (1,)


@pytest.mark.django_db
def test_get_latest_component_stored_proc():
    """Basic check if get_latest_component works (Note- behaviour is tested in test_model.py)."""

    with connection.cursor() as cursor:
        cursor.execute("select * from get_latest_component('test','REDHAT','ansible-runner');")
        row = cursor.fetchone()

    assert row == (None,)
