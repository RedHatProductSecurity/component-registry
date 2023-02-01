from datetime import datetime
from urllib.parse import quote

import pytest
from django.conf import settings

from corgi.collectors.appstream_lifecycle import AppStreamLifeCycleCollector
from corgi.core.models import Component, ComponentNode

from .factories import (
    ChannelFactory,
    ComponentFactory,
    ComponentTagFactory,
    LifeCycleFactory,
    ProductFactory,
    ProductStreamFactory,
    ProductVariantFactory,
    ProductVersionFactory,
    SoftwareBuildFactory,
    SrpmComponentFactory,
)

pytestmark = pytest.mark.unit


def extract_tag_tuples(tags: list[dict]) -> set[tuple]:
    return {(t["name"], t["value"]) for t in tags}


# Different DB names which really point to the same DB run in different transactions by default
# so writes to "default" don't appear in "read_only" without workarounds
# https://code.djangoproject.com/ticket/23718#comment:6
@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_software_build_details(client, api_path):
    build = SoftwareBuildFactory(tag__name="t0", tag__value="v0")

    response = client.get(f"{api_path}/builds/{build.build_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["build_id"] == build.build_id
    assert data["name"] == build.name
    assert extract_tag_tuples(data["tags"]) == {("t0", "v0")}

    response = client.get(f"{api_path}/builds?tags=t0:v0")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
@pytest.mark.parametrize(
    "model, endpoint_name",
    [
        (ProductFactory, "products"),
        (ProductVersionFactory, "product_versions"),
        (ProductStreamFactory, "product_streams"),
        (ProductVariantFactory, "product_variants"),
    ],
)
def test_product_data_detail(model, endpoint_name, client, api_path):
    p1 = model(name="RHEL", tag__name="t0", tag__value="v0")

    response = client.get(f"{api_path}/{endpoint_name}")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/{endpoint_name}/{p1.uuid}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "RHEL"
    assert extract_tag_tuples(data["tags"]) == {("t0", "v0")}

    response = client.get(f"{api_path}/{endpoint_name}?tags=t0:v0")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_channel_detail(client, api_path):
    p1 = ChannelFactory(name="Repo1")

    response = client.get(f"{api_path}/channels")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/channels/{p1.uuid}")
    assert response.status_code == 200
    assert response.json()["name"] == "Repo1"


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_component_include_exclude_fields(client, api_path):
    SrpmComponentFactory(name="curl")

    response = client.get(
        f"{api_path}/components?include_fields=software_build.source,product_streams"
    )
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    srpm = response["results"][0]

    # Only software_build dict and its child source key are present
    assert srpm.get("product_streams") == []
    assert srpm.get("software_build") is not None
    assert srpm["software_build"].get("source") is not None

    # Other keys on Component and subkeys on SoftwareBuild are missing
    assert srpm.get("products") is None
    assert srpm["software_build"].get("build_id") is None

    response = client.get(
        f"{api_path}/components?exclude_fields=software_build.source,product_streams"
    )
    assert response.status_code == 200
    srpm = response.json()["results"][0]

    # Only product_streams key and SoftwareBuild's child source key are missing
    assert srpm.get("product_streams") is None
    assert srpm.get("software_build") is not None
    assert srpm["software_build"].get("source") is None

    # Other keys on Component and subkeys on SoftwareBuild are present
    assert srpm.get("products") == []
    assert srpm["software_build"].get("build_id") is not None

    # When both are given, include_fields takes precedence
    response = client.get(
        f"{api_path}/components?include_fields=software_build"
        "&exclude_fields=software_build.source,product_streams"
    )
    assert response.status_code == 200
    srpm = response.json()["results"][0]

    # Only keys in software_build dict are present
    assert srpm.get("software_build") is not None
    assert srpm["software_build"].get("build_id") is not None

    # Other keys on Component and SoftwareBuild's child source key are missing
    assert srpm.get("products") is None
    assert srpm.get("product_streams") is None
    assert srpm["software_build"].get("source") is None

    # When both are given and we exclude an entire object, some subkeys can still be present
    response = client.get(
        f"{api_path}/components?include_fields=software_build.source"
        "&exclude_fields=software_build,product_streams"
    )
    assert response.status_code == 200
    srpm = response.json()["results"][0]

    # Only SoftwareBuild's child source key is present
    assert srpm.get("software_build") is not None
    assert srpm["software_build"].get("source") is not None

    # Other keys on Component and SoftwareBuild are missing
    assert srpm.get("products") is None
    assert srpm.get("product_streams") is None
    assert srpm["software_build"].get("build_id") is None


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_component_detail(client, api_path):
    c1 = ComponentFactory(name="curl")

    response = client.get(f"{api_path}/components")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    assert response.json()["name"] == "curl"


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_component_detail_olcs_put(client, api_path):
    """Test that OpenLCS can upload scan results for a component"""
    c1 = ComponentFactory()
    component_path = f"{api_path}/components/{c1.uuid}"
    openlcs_data = {
        "copyright_text": "Copyright Test",
        "license_concluded": "BSD or MIT",
        "openlcs_scan_url": "a link",
        "openlcs_scan_version": "a version",
    }

    response = client.get(component_path)
    assert response.status_code == 200
    response = response.json()
    for key in openlcs_data:
        # Values are unset by default
        assert response[key] == ""

    # Subtly different from requests.put(), so json= kwarg doesn't work
    # Below should use follow=True, but it's currently broken
    # https://github.com/encode/django-rest-framework/discussions/8695
    response = client.put(f"{component_path}/olcs_test", data=openlcs_data, format="json")
    assert response.status_code == 302
    assert response.headers["Location"] == component_path

    response = client.get(component_path)
    assert response.status_code == 200
    response = response.json()
    for key, value in openlcs_data.items():
        if key == "license_concluded":
            # Uppercased to be SPDX-compliant
            value = value.upper()
        # Values now match what was submitted
        assert response[key] == value

    # Return a 400 if none of the above keys are getting set. May change in future
    response = client.put(f"{component_path}/olcs_test", data={}, format="json")
    assert response.status_code == 400

    # Return a 404 if that component can't be found
    response = client.put(
        f"{api_path}/components/00000000-0000-0000-0000-000000000000/olcs_test",
        data=openlcs_data,
        format="json",
    )
    assert response.status_code == 404


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_component_detail_dev(client, api_path):
    upstream = ComponentFactory(
        name="upstream",
        type=Component.Type.GENERIC,
        namespace=Component.Namespace.UPSTREAM,
        related_url="https://example.org/related",
    )
    upstream_node, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=upstream.purl,
        defaults={"obj": upstream},
    )
    dev_comp = ComponentFactory(
        name="dev", type=Component.Type.NPM, namespace=Component.Namespace.REDHAT
    )
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES_DEV,
        parent=upstream_node,
        purl=dev_comp.purl,
        defaults={"obj": dev_comp},
    )

    upstream.save_component_taxonomy()
    upstream.save()
    assert dev_comp.purl in upstream.provides.values_list("purl", flat=True)

    response = client.get(f"{api_path}/components?namespace=UPSTREAM")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    # TODO - bug in pytest loses request.META['HTTP_HOST'] fixing as part of another MR
    # assert dev_comp.purl in response.json()["results"][0]["provides"][0]["purl"]

    response = client.get(f"{api_path}/components/{upstream.uuid}")
    assert response.status_code == 200
    assert response.json()["name"] == upstream.name
    assert response.json()["related_url"] == upstream.related_url
    # TODO - bug in pytest loses request.META['HTTP_HOST'] fixing as part of another MR
    # assert dev_comp.purl in response.json()["provides"][0]["purl"]


@pytest.mark.django_db
def test_component_write_not_allowed(client, api_path):
    # Currently, only read operations are allowed on all models besides tags
    c = ComponentFactory(name="curl")
    response = client.put(f"{api_path}/components/{c.uuid}", params={"name": "wget"})
    assert response.status_code == 405
    response = client.post(f"{api_path}/components", data={"name": "curl"})
    assert response.status_code == 405


@pytest.mark.django_db(databases=("read_only",))
def test_component_does_not_exist(client, api_path):
    response = client.get(f"{api_path}/components/18ead2f2-6e0a-409c-8135-79f0f4d7740d")
    assert response.status_code == 404


@pytest.mark.skip(reason="Disabled until auth for write endpoints is implemented")
def test_component_tags_create(client, api_path):
    c1 = ComponentFactory(name="curl", tag__name="t0", tag__value="v0")
    response = client.post(
        f"{api_path}/components/{c1.uuid}/tags",
        data={"name": "t1", "value": "v1"},
    )
    assert response.status_code == 201
    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert extract_tag_tuples(response.json()["tags"]) == {("t0", "v0"), ("t1", "v1")}

    client.post(f"{api_path}/components/{c1.uuid}/tags", data={"name": "t2"})
    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    assert extract_tag_tuples(response.json()["tags"]) == {("t0", "v0"), ("t1", "v1"), ("t2", "")}


@pytest.mark.skip(reason="Disabled until auth for write endpoints is implemented")
def test_component_tags_duplicate(client, api_path):
    c1 = ComponentFactory(name="curl", tag__name="t0", tag__value="v0")
    response = client.post(
        f"{api_path}/components/{c1.uuid}/tags", data={"name": "t0", "value": "v0"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Tag already exists."


@pytest.mark.skip(reason="Disabled until auth for write endpoints is implemented")
def test_component_tags_delete(client, api_path):
    component = ComponentFactory(name="curl", tag=None)
    ComponentTagFactory(component=component, name="t0", value="v0")
    ComponentTagFactory(component=component, name="t1", value="")
    ComponentTagFactory(component=component, name="t2", value="v2")
    ComponentTagFactory(component=component, name="t3", value="v3")

    # Remove one tag
    response = client.delete(
        f"{api_path}/components/{component.uuid}/tags", data={"name": "t0", "value": "v0"}
    )
    assert response.status_code == 200
    assert response.json()["text"] == "Tag deleted."
    response = client.get(f"{api_path}/components/{component.uuid}")
    assert extract_tag_tuples(response.json()["tags"]) == {("t1", ""), ("t2", "v2"), ("t3", "v3")}

    # Try to remove a non-existent tag
    response = client.delete(f"{api_path}/components/{component.uuid}/tags", data={"name": "t2"})
    assert response.status_code == 200
    assert response.json()["text"] == "Tag not found; nothing deleted."

    # Remove the t1 tag by name only
    response = client.delete(f"{api_path}/components/{component.uuid}/tags", data={"name": "t1"})
    assert response.status_code == 200
    assert response.json()["text"] == "Tag deleted."
    response = client.get(f"{api_path}/components/{component.uuid}")
    assert extract_tag_tuples(response.json()["tags"]) == {("t2", "v2"), ("t3", "v3")}

    # Remove all tags
    response = client.delete(f"{api_path}/components/{component.uuid}/tags")
    assert response.status_code == 200
    assert response.json()["text"] == "All tags deleted."
    response = client.get(f"{api_path}/components/{component.uuid}")
    assert response.json()["tags"] == []


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_component_add_uri(client, api_path):
    c1 = ComponentFactory(
        name="curl",
        tag__name="component_review",
        tag__value="https://someexample.org/review/curl.doc",
    )
    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    tags = response.json()["tags"]
    assert len(tags) == 1
    tags = tags[0]
    # Fail if timestamp not present - we can't set value directly so can't assert it
    assert tags.pop("created_at", None)
    assert tags == {"name": "component_review", "value": "https://someexample.org/review/curl.doc"}


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_srpm_detail(client, api_path):
    c1 = SrpmComponentFactory(name="curl-7.19.7-35.el6.src")
    response = client.get(f"{api_path}/components?type=RPM&arch=src")
    assert response.status_code == 200
    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    assert response.json()["name"] == "curl-7.19.7-35.el6.src"
    response = client.get(f"{api_path}/components?type=RPM&name=curl-7.19.7-35.el6.src")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    response = client.get(f"{api_path}/components?type=RPM&re_purl=curl")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_rpm_detail(client, api_path):
    c1 = ComponentFactory(
        type=Component.Type.RPM, name="curl", version="7.19.7", release="35.el6", arch="x86_64"
    )
    response = client.get(f"{api_path}/components?type=RPM")
    assert response.status_code == 200
    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    assert response.json()["nvr"] == "curl-7.19.7-35.el6"
    response = client.get(f"{api_path}/components?type=RPM&nvr=curl-7.19.7-35.el6")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    response = client.get(f"{api_path}/components?purl={quote(c1.purl)}")
    assert response.status_code == 200


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_purl_reserved(client, api_path):
    c1 = ComponentFactory(
        name="dbus-glib-debuginfo",
        version="0.110",
        release="13.module+el9.0.0+14622+3cf1e152",
        type=Component.Type.RPM,
        arch="x86_64",
    )
    response = client.get(
        f"{api_path}/components?nvr=dbus-glib-debuginfo-0.110-13.module+el9.0.0+14622+3cf1e152"
    )
    assert response.status_code == 200
    response = client.get(f"{api_path}/components?purl={quote(c1.purl)}")
    assert response.status_code == 200


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_re_name_filter(client, api_path):
    c1 = ComponentFactory(type=Component.Type.RPM, name="autotrace-devel")
    response = client.get(f"{api_path}/components?type=RPM")
    assert response.status_code == 200
    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    response = client.get(rf"{api_path}/components?re_name=^autotrace(-devel|-libs|-utils)$")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_re_purl_filter(client, api_path):
    c1 = ComponentFactory(
        type=Component.Type.RPM, namespace=Component.Namespace.REDHAT, name="autotrace-devel"
    )
    response = client.get(f"{api_path}/components?type=RPM")
    assert response.status_code == 200
    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    response = client.get(rf"{api_path}/components?re_purl=^(.*)\/redhat\/autotrace(.*)$")
    assert response.json()["results"][0]["uuid"] == str(c1.uuid)
    response = client.get(f"{api_path}/components?re_name=^autotrace(-devel|-libs|-utils)$")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_nvr_nevra_filter(client, api_path):
    c1 = ComponentFactory(
        type=Component.Type.RPM,
        meta_attr={"epoch": "1"},
        name="autotrace-devel",
        version="3.2.1",
        release="1.0.1e",
        arch="noarch",
    )
    response = client.get(f"{api_path}/components?type=RPM")
    assert response.status_code == 200
    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.json()["epoch"] == "1"
    assert response.json()["nvr"] == "autotrace-devel-3.2.1-1.0.1e"
    assert response.json()["nevra"] == "autotrace-devel:1-3.2.1-1.0.1e.noarch"
    response = client.get(f"{api_path}/components?nvr=autotrace-devel-3.2.1-1.0.1e")
    assert response.json()["count"] == 1
    response = client.get(f"{api_path}/components?nevra=autotrace-devel:1-3.2.1-1.0.1e.noarch")
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_filter_component_tags(client, api_path):
    openssl = ComponentFactory(name="openssl", type=Component.Type.RPM, tag=None)
    ComponentTagFactory(tagged_model=openssl, name="prodsec_priority", value="")
    ComponentTagFactory(tagged_model=openssl, name="ubi8", value="")
    ComponentTagFactory(tagged_model=openssl, name="status", value="yellow")

    curl = ComponentFactory(name="curl", type=Component.Type.RPM, tag=None)
    ComponentTagFactory(tagged_model=curl, name="prodsec_priority", value="")
    ComponentTagFactory(tagged_model=curl, name="status", value="green")

    response = client.get(f"{api_path}/components?type=RPM")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    response = client.get(f"{api_path}/components?type=RPM&tags=prodsec_priority")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    # Filter conditions are ANDed, only openssl has both tags
    response = client.get(f"{api_path}/components?type=RPM&tags=prodsec_priority,ubi8")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["name"] == "openssl"

    # Filter by name and value
    response = client.get(f"{api_path}/components?type=RPM&tags=ubi8,status:yellow")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["name"] == "openssl"

    # Filter by "status" tag without value
    response = client.get(f"{api_path}/components?type=RPM&tags=status")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    # Filter by "status:green", matches curl
    response = client.get(f"{api_path}/components?type=RPM&tags=status:green")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["name"] == "curl"

    # Filter by non-existent tags
    response = client.get(f"{api_path}/components?type=RPM&tags=hello")
    assert response.status_code == 200
    assert response.json()["count"] == 0
    response = client.get(f"{api_path}/components?type=RPM&tags=status:red")
    assert response.status_code == 200
    assert response.json()["count"] == 0


@pytest.mark.skip(reason="Disabled until appstream lifecycle data is integrated into core")
def test_lifecycle_detail(client, api_path):
    response = client.get(f"{api_path}/lifecycles")
    assert response.status_code == 200
    life_cycle = LifeCycleFactory()
    response = client.get(f"{api_path}/lifecycles/{life_cycle.pk}")
    assert response.status_code == 200
    assert response.json()["name"] == "bzip2-devel"


def test_retrieve_lifecycle_defs(requests_mock):
    with open("tests/data/application_streams.yaml", "r") as app_steam_data:
        requests_mock.get(f"{settings.APP_STREAMS_LIFE_CYCLE_URL}", text=app_steam_data.read())
    collector = AppStreamLifeCycleCollector()
    data = collector.get_lifecycle_defs()
    assert isinstance(data, list)
    assert isinstance(data[0], dict)
    assert [
        "acg",
        "application_stream_name",
        "enddate",
        "initial_product_version",
        "lifecycle",
        "name",
        "private",
        "product",
        "source",
        "stream",
        "type",
    ] == sorted(data[0].keys())


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_products(client, api_path):
    ProductFactory(name="rhel")
    ProductFactory(name="rhel-av")

    response = client.get(f"{api_path}/products")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    response = client.get(f"{api_path}/products?ofuri=o:redhat:rhel-av")
    assert response.status_code == 200

    response = client.get(f"{api_path}/products?ofuri=o:redhat:rhel")
    assert response.status_code == 200


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_versions(client, api_path):
    ProductVersionFactory(name="rhel-8", version="8")
    ProductVersionFactory(name="rhel-av-8", version="8")

    response = client.get(f"{api_path}/product_versions")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    response = client.get(f"{api_path}/product_versions?ofuri=o:redhat:rhel-av:8")
    assert response.status_code == 200

    response = client.get(f"{api_path}/product_versions?ofuri=o:redhat:rhel:8")
    assert response.status_code == 200


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_streams(client, api_path):
    ProductStreamFactory(name="rhel-8.5.0-z", version="8.5.0-z")
    ProductStreamFactory(name="rhel-av-8.5.0-z", version="8.5.0-z", active=False)

    response = client.get(f"{api_path}/product_streams")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/product_streams?active=all")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    response = client.get(f"{api_path}/product_streams?ofuri=o:redhat:rhel-av:8.5.0-z")
    assert response.status_code == 200

    response = client.get(f"{api_path}/product_streams?ofuri=o:redhat:rhel:8.5.0-z")
    assert response.status_code == 200

    response = client.get(f"{api_path}/product_streams?name=rhel-av-8.5.0-z")
    assert response.status_code == 200
    assert response.json()["name"] == "rhel-av-8.5.0-z"

    response = client.get(f"{api_path}/product_streams?re_name=rhel&view=summary")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_variants(client, api_path):
    pv_appstream = ProductVariantFactory(name="AppStream-8.5.0.Z.MAIN")
    pv_baseos = ProductVariantFactory(name="BaseOS-8.5.0.Z.MAIN")

    response = client.get(f"{api_path}/product_variants")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    response = client.get(f"{api_path}/product_variants?ofuri={pv_appstream.ofuri}")
    assert response.status_code == 200

    response = client.get(f"{api_path}/product_variants?ofuri={pv_baseos.ofuri}")
    assert response.status_code == 200


@pytest.mark.django_db(databases=("read_only",))
def test_status(client, api_path):
    response = client.get(f"{api_path}/status")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "db_size" in response.json()


def test_api_listing(client, api_path):
    response = client.get(f"{api_path}/")
    assert response.status_code == 200


@pytest.mark.django_db(databases=("read_only",))
def test_api_component_404(client, api_path):
    response = client.get(
        f"{api_path}/components?purl=pkg:rpm/redhat/fake-libs@3.26.0-15.el8?arch=x86_64"
    )
    assert response.status_code == 404


def test_api_component_400(client, api_path):
    response = client.get(f"{api_path}/components?type=NONEXISTANTTYPE")
    assert response.status_code == 400


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_components_ofuri(client, api_path):
    """test 'latest' filter on components"""

    ps1 = ProductStreamFactory(name="rhel-8.6.0", version="8.6.0")
    assert ps1.ofuri == "o:redhat:rhel:8.6.0"
    ps2 = ProductStreamFactory(name="rhel-8.6.0.z", version="8.6.0.z")
    assert ps2.ofuri == "o:redhat:rhel:8.6.0.z"

    old_sb1 = SoftwareBuildFactory(
        completion_time=datetime.strptime("2017-03-29 12:13:29 GMT+0000", "%Y-%m-%d %H:%M:%S %Z%z")
    )
    old_openssl = SrpmComponentFactory(name="openssl", software_build=old_sb1, release="1")
    old_openssl.productstreams.add(ps1)

    sb1 = SoftwareBuildFactory(
        completion_time=datetime.strptime("2018-03-29 12:13:29 GMT+0000", "%Y-%m-%d %H:%M:%S %Z%z")
    )
    openssl = SrpmComponentFactory(name="openssl", software_build=sb1, release="2")
    openssl.productstreams.add(ps1)

    old_sb2 = SoftwareBuildFactory(
        completion_time=datetime.strptime("2017-03-29 12:13:29 GMT+0000", "%Y-%m-%d %H:%M:%S %Z%z")
    )
    old_curl = SrpmComponentFactory(name="curl", software_build=old_sb2, release="1")
    old_curl.productstreams.add(ps2)

    sb2 = SoftwareBuildFactory(
        completion_time=datetime.strptime("2018-03-29 12:13:29 GMT+0000", "%Y-%m-%d %H:%M:%S %Z%z")
    )
    curl = SrpmComponentFactory(name="curl", software_build=sb2, release="2")
    curl.productstreams.add(ps2)

    response = client.get(f"{api_path}/components?ofuri=o:redhat:rhel:8.6.0.z")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/components?ofuri=o:redhat:rhel:8.6.0")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_components_versions(client, api_path):
    ps1 = ProductStreamFactory(name="rhel-7", version="7")
    assert ps1.ofuri == "o:redhat:rhel:7"
    ps2 = ProductStreamFactory(name="rhel-8", version="8")
    assert ps2.ofuri == "o:redhat:rhel:8"

    sb1 = SoftwareBuildFactory(
        completion_time=datetime.strptime("2018-03-29 12:13:29 GMT+0000", "%Y-%m-%d %H:%M:%S %Z%z")
    )
    openssl = ComponentFactory(
        type=Component.Type.RPM, arch="x86_64", name="openssl", software_build=sb1
    )
    openssl.productstreams.add(ps2)
    openssl_srpm = SrpmComponentFactory(name="openssl", software_build=sb1)
    openssl_srpm.productstreams.add(ps2)

    sb2 = SoftwareBuildFactory(
        completion_time=datetime.strptime("2018-03-29 12:13:29 GMT+0000", "%Y-%m-%d %H:%M:%S %Z%z")
    )
    curl = ComponentFactory(type=Component.Type.RPM, arch="x86_64", name="curl", software_build=sb2)
    curl.productstreams.add(ps1)
    curl_srpm = SrpmComponentFactory(name="curl", software_build=sb2)
    curl_srpm.productstreams.add(ps1)

    response = client.get(f"{api_path}/components?product_streams=o:redhat:rhel:8")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    # ofuri returns 'latest' build root components (eg. including SRPM,
    #  noarch CONTAINER_IMAGE and RHEL_MODULE)
    response = client.get(f"{api_path}/components?ofuri=o:redhat:rhel:7")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/components?name=curl&view=product")
    assert response.status_code == 200
    assert response.json()["count"] == 2


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_components(client, api_path):
    openssl = ComponentFactory(name="openssl")
    curl = ComponentFactory(name="curl")
    rhel = ProductFactory(name="rhel")
    rhel_br = ProductFactory(name="rhel-br")

    openssl.products.add(rhel)
    curl.products.add(rhel_br)

    response = client.get(f"{api_path}/components?products=o:redhat:rhel")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/components?products=o:redhat:rhel-br")
    assert response.status_code == 200
    assert response.json()["count"] == 1
