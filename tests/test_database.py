import pytest
from django.db import connection

pytestmark = pytest.mark.unit


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_stored_proc_exists(stored_proc):
    """Check if stored procs exist."""

    with connection.cursor() as cursor:
        cursor.execute("select 'get_latest_component'::regproc;")
        row = cursor.fetchone()

    assert row == ("get_latest_component",)

    with connection.cursor() as cursor:
        cursor.execute("select 'rpmvercmp'::regproc;")
        row = cursor.fetchone()

    assert row == ("rpmvercmp",)


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_rpmvercmp_stored_proc(stored_proc):
    """Basic check if rpmvercmp works.
    (Note- behaviour is tested in test_model.py and test_api.py)

    Parameters:
        a 	1st version string
        b 	2nd version string
    Returns:
        +1 if a is "newer", 0 if equal, -1 if b is "newer"

    """

    with connection.cursor() as cursor:
        cursor.execute("select * from rpmvercmp('1.2.3','1.2.5');")
        row = cursor.fetchone()
    assert row == (-1,)

    with connection.cursor() as cursor:
        cursor.execute("select * from rpmvercmp('1.3.3','1.4.5');")
        row = cursor.fetchone()
    assert row == (-1,)

    with connection.cursor() as cursor:
        cursor.execute("select * from rpmvercmp('1.8.3','1.3.5');")
        row = cursor.fetchone()
    assert row == (1,)

    with connection.cursor() as cursor:
        cursor.execute("select * from rpmvercmp('1.1.1.1.1.1.1.1','1.3.5');")
        row = cursor.fetchone()
    assert row == (-1,)

    with connection.cursor() as cursor:
        cursor.execute("select * from rpmvercmp('7.29.0','8.3.0');")
        row = cursor.fetchone()
    assert row == (-1,)


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_rpmvercmp_epoch_stored_proc(stored_proc):
    """Basic check if rpmvercmp_epoch works.
    (Note- behaviour is tested in test_model.py and test_api.py)

    Parameters:
        component1_epoch
        component1_version
        component1_release
        component2_epoch
        component2_version
        component2_release
    Returns:
        +1 if a is "newer", 0 if equal, -1 if b is "newer"

    """

    with connection.cursor() as cursor:
        cursor.execute("select * from rpmvercmp_epoch(2,'6.4.0','13.el7',3,'7.91','10.el9');")
        row = cursor.fetchone()
    assert row == (-1,)

    with connection.cursor() as cursor:
        cursor.execute("select * from rpmvercmp_epoch(3,'6.4.0','13.el7',2,'7.91','19.el9');")
        row = cursor.fetchone()
    assert row == (1,)


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_get_latest_component_stored_proc(stored_proc):
    """Basic check if get_latest_component works.
    (Note- behaviour is tested in test_model.py and test_api.py)

    Parameters:
        product_model_type: Product|ProductVersion|ProductStream|ProductVariant
        product ofuri: str
        component_namespace: REDHAT|UPSTREAM
        component_name: str
        active_products: bool
    Returns:
        uuid of latest component
    """

    with connection.cursor() as cursor:
        cursor.execute(
            "select * from get_latest_component('ProductStream',ARRAY['o:redhat:openshift-enterprise:3.11.z'],'RPM','REDHAT','ansible-runner','src',True);"  # noqa: E501
        )
        row = cursor.fetchone()

    assert row == (None,)
