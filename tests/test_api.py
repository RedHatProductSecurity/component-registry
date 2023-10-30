from urllib.parse import quote

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

from corgi.collectors.appstream_lifecycle import AppStreamLifeCycleCollector
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    ProductStream,
    SoftwareBuild,
)

from .factories import (
    BinaryRpmComponentFactory,
    ChannelFactory,
    ComponentFactory,
    ComponentTagFactory,
    LifeCycleFactory,
    ProductComponentRelationFactory,
    ProductFactory,
    ProductStreamFactory,
    ProductVariantFactory,
    ProductVersionFactory,
    SoftwareBuildFactory,
    SrpmComponentFactory,
    UpstreamComponentFactory,
)

User = get_user_model()

pytestmark = pytest.mark.unit


def extract_tag_tuples(tags: list[dict]) -> set[tuple]:
    return {(t["name"], t["value"]) for t in tags}


@pytest.mark.parametrize("build_type", SoftwareBuild.Type.values)
# Different DB names which really point to the same DB run in different transactions by default
# so writes to "default" don't appear in "read_only" without workarounds
# https://code.djangoproject.com/ticket/23718#comment:6
@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_software_build_details(client, api_path, build_type):
    build = SoftwareBuildFactory(build_type=build_type, tag__name="t0", tag__value="v0")
    SoftwareBuildFactory(build_type=build_type)

    response = client.get(f"{api_path}/builds/{build.uuid}")
    assert response.status_code == 200
    data = response.json()
    assert data["build_id"] == build.build_id
    assert data["name"] == build.name
    assert extract_tag_tuples(data["tags"]) == {("t0", "v0")}

    response = client.get(f"{api_path}/builds?build_id={build.build_id}")
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["uuid"] == str(build.uuid)

    response = client.get(f"{api_path}/builds?tags=t0:v0")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_software_build_by_product(client, api_path):
    version = ProductVersionFactory()
    stream_a = ProductStreamFactory(products=version.products, productversions=version)
    stream_b = ProductStreamFactory(products=version.products, productversions=version)
    build_a = SoftwareBuildFactory()
    build_b = SoftwareBuildFactory()
    ProductComponentRelationFactory(product_ref=stream_a, software_build=build_a)
    ProductComponentRelationFactory(product_ref=stream_b, software_build=build_b)

    response = client.get(f"{api_path}/builds")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    response = client.get(f"{api_path}/builds?ofuri={stream_a.ofuri}")
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["count"] == 1
    assert response_json["results"][0]["uuid"] == str(build_a.pk)


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
    c1 = ComponentFactory(name="curl", related_url="https://curl.se")

    response = client.get(f"{api_path}/components")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    assert response.json()["name"] == "curl"

    response = client.get(f"{api_path}/components?related_url=curl")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_latest_components_by_streams_filter(client, api_path, stored_proc):
    # Create many components so we have robust test data
    # 2 components (1 older version, 1 newer version) for each name / arch pair in REDHAT namespace
    # 12 REDHAT components across 6 pairs
    # plus 2 UPSTREAM components per name for src architecture only, 4 upstreams total
    # plus 2 non-RPMs components per name for src architecture only, 4 PyPI packages total
    # Overall 20 components, and latest filter should show 10 (newer, when on or older, when off)
    components = {}
    stream = ProductStreamFactory()
    for name in "red", "blue":
        for arch in "aarch64", "x86_64", "src":
            older_component = ComponentFactory(
                type=Component.Type.RPM,
                namespace=Component.Namespace.REDHAT,
                name=name,
                version="9",
                arch=arch,
            )
            older_component.productstreams.add(stream)
            # Create newer components with the same type, namespace, name, release, and arch
            # But a different version and build
            newer_component = ComponentFactory(
                type=older_component.type,
                namespace=older_component.namespace,
                name=older_component.name,
                version="10",
                release=older_component.release,
                arch=older_component.arch,
            )
            newer_component.productstreams.add(stream)
            components[(older_component.type, name, arch)] = (older_component, newer_component)
        # Create UPSTREAM components for src architecture only
        # with the same type, name, and version as REDHAT src components
        # but no release or software_build
        older_upstream_component = ComponentFactory(
            type=older_component.type,
            namespace=Component.Namespace.UPSTREAM,
            name=older_component.name,
            version=older_component.version,
            release="",
            arch="noarch",
            software_build=None,
        )
        older_upstream_component.productstreams.add(stream)
        newer_upstream_component = ComponentFactory(
            type=newer_component.type,
            namespace=older_upstream_component.namespace,
            name=newer_component.name,
            version=newer_component.version,
            release=older_upstream_component.release,
            arch=older_upstream_component.arch,
            software_build=older_upstream_component.software_build,
        )
        newer_upstream_component.productstreams.add(stream)
        components[(older_upstream_component.type, name, older_upstream_component.arch)] = (
            older_upstream_component,
            newer_upstream_component,
        )

        # A NEVRA like "PyYAML version 1.2.3 with no release or architecture"
        # could be a PyPI package, or an upstream RPM
        # We want to see the latest version of both components
        # Create PyPI components for src architecture only
        # with the same namespace, name and version as UPSTREAM noarch components
        # and no release, arch, or software_build
        older_unrelated_component = ComponentFactory(
            type=Component.Type.PYPI,
            namespace=older_upstream_component.namespace,
            name=older_component.name,
            version=older_component.version,
            release=older_upstream_component.release,
            arch=older_upstream_component.arch,
            software_build=older_upstream_component.software_build,
        )
        older_unrelated_component.productstreams.add(stream)
        newer_unrelated_component = ComponentFactory(
            type=older_unrelated_component.type,
            namespace=older_upstream_component.namespace,
            name=newer_component.name,
            version=newer_component.version,
            release=older_upstream_component.release,
            arch=older_upstream_component.arch,
            software_build=older_upstream_component.software_build,
        )
        newer_unrelated_component.productstreams.add(stream)
        components[(older_unrelated_component.type, name, older_unrelated_component.arch)] = (
            older_unrelated_component,
            newer_unrelated_component,
        )

    response = client.get(f"{api_path}/components")
    assert response.status_code == 200
    assert response.json()["count"] == 20

    response = client.get(f"{api_path}/components?latest_components_by_streams=True")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2
    for result in response["results"]:
        assert (
            result["purl"] == components[(result["type"], result["name"], result["arch"])][1].purl
        )

    response = client.get(f"{api_path}/components?latest_components_by_streams=False")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2
    for result in response["results"]:
        assert (
            result["purl"] == components[(result["type"], result["name"], result["arch"])][0].purl
        )

    # Also test latest_components_by_streams filter when combined with root_components filter
    # Note that order doesn't matter here, e.g. before CORGI-609 both of below gave 0 results:
    # /api/v1/components?re_name=webkitgtk&root_components=True&latest_components=True
    # /api/v1/components?re_name=webkitgtk&latest_components=True&root_components=True
    #
    # There are 17 root components in the above queryset:
    # /api/v1/components?re_name=webkitgtk&root_components=True
    # But the latest_components filter is always applied first, and previously chose a binary RPM
    # So the source RPMs were filtered out, and the root_components filter had no data to report
    # This is likely due to the order the filters are defined in (see corgi/api/filters.py)
    # Fixed by CORGI-609, and this test makes sure the bug doesn't come back
    response = client.get(
        f"{api_path}/components?root_components=True&latest_components_by_streams=True"
    )
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2
    # Red and blue components with arch "src" each have 1 latest
    for result in response["results"]:
        assert (
            result["purl"] == components[(result["type"], result["name"], result["arch"])][1].purl
        )

    response = client.get(
        f"{api_path}/components?root_components=True&latest_components_by_streams=False"
    )
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2
    # Red and blue components with arch "src" each have 1 non-latest
    for result in response["results"]:
        assert (
            result["purl"] == components[(result["type"], result["name"], result["arch"])][0].purl
        )

    response = client.get(
        f"{api_path}/components?root_components=False&latest_components_by_streams=True"
    )
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 0
    # Red and blue components for 2 arches each have 1 latest
    # Red and blue components for upstream each have 1 latest
    # Red and blue components for non-RPMs each have 1 latest
    # These are all non-root components, so are excluded in latest_components_by_stream filter
    for result in response["results"]:
        assert (
            result["purl"] == components[(result["type"], result["name"], result["arch"])][1].purl
        )

    response = client.get(
        f"{api_path}/components?root_components=False&latest_components_by_streams=False&limit=20"
    )
    assert response.status_code == 200
    response = response.json()
    # "Find all the non-root components, then exclude latest root component versions"
    # "Report only the non-latest root component versions in each stream"
    #
    # The latest_components_by_stream filter only looks at root components
    # So when all root components are filtered out,
    # there are no "latest root component versions" to exclude
    #
    # So when root_components=False,
    # latest_components_by_stream=False is a no-op and has no NEVRAs to exclude
    # We report all 16 non-root components, regardless of latest / non-latest version
    assert response["count"] == 16
    # Red and blue components for 2 arches each have 2 components (1 latest and 1 non-latest)
    # Red and blue components for upstream each have 2 components (1 latest and 1 non-latest)
    # Red and blue components for non-RPMs each have 2 components (1 latest and 1 non-latest)
    for result in response["results"]:
        assert result["purl"] in tuple(
            component.purl
            for component in components[(result["type"], result["name"], result["arch"])]
        )


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_latest_components_by_streams_filter_with_multiple_products(client, api_path, stored_proc):
    ps1 = ProductStreamFactory(name="rhel-7", version="7")
    assert ps1.ofuri == "o:redhat:rhel:7"
    ps2 = ProductStreamFactory(name="rhel-8", version="8")
    assert ps2.ofuri == "o:redhat:rhel:8"

    # Only belongs to RHEL7 stream
    oldest_component = SrpmComponentFactory(release="8")
    oldest_component.productstreams.add(ps1)

    # Belongs to both RHEL7 and RHEL8 streams
    older_component = SrpmComponentFactory(
        name=oldest_component.name, version=oldest_component.version, release="9"
    )
    older_component.productstreams.add(ps1)
    older_component.productstreams.add(ps2)

    newer_component = SrpmComponentFactory(
        name=oldest_component.name,
        version=oldest_component.version,
        release="10",
    )
    newer_component.productstreams.add(ps1)
    newer_component.productstreams.add(ps2)

    # Only belongs to RHEL8 stream
    newest_component = SrpmComponentFactory(
        name=oldest_component.name,
        version=oldest_component.version,
        release="11",
    )
    newest_component.productstreams.add(ps2)

    response = client.get(f"{api_path}/components")
    assert response.status_code == 200
    assert response.json()["count"] == 4

    response = client.get(f"{api_path}/components?latest_components_by_streams=True")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2
    # Report newer_component as the latest for the RHEL7 stream
    # Even though it's "not the latest" for the RHEL8 stream
    # Report newest_component as the latest for the RHEL8 stream
    # Even though both have the same name / only one is latest overall
    # TODO: This is failing occasionally, for unknown reasons
    #  AssertionError: assert 'memory:3-8.6.5-11.src' == 'memory:3-8.6.5-10.src'
    assert response["results"][0]["nevra"] == newer_component.nevra
    assert response["results"][1]["nevra"] == newest_component.nevra

    response = client.get(f"{api_path}/components?latest_components_by_streams=False")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2
    # Report oldest_component as "not the latest" for the RHEL7 stream
    # Report older_component as "not the latest" for the RHEL7 and RHEL8 streams
    # Don't report newer_component, even though it's "not the latest" for the RHEL8 stream
    # because it is the latest for the RHEL7 stream
    # So it gets added to the list of latest_nevras and excluded from the results
    assert response["results"][0]["nevra"] == oldest_component.nevra
    assert response["results"][1]["nevra"] == older_component.nevra


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_released_components_filter(client, api_path):
    """Test that the released components filter
    gives the correct results with no duplicates in the API"""
    released_build = SoftwareBuildFactory()
    unreleased_build = SoftwareBuildFactory()
    ProductComponentRelationFactory(
        type=ProductComponentRelation.Type.ERRATA,
        software_build=released_build,
        build_id=released_build.build_id,
        build_type=released_build.build_type,
    )
    # Duplicate relations for the same build
    # shouldn't give duplicate results in the API
    ProductComponentRelationFactory(
        type=ProductComponentRelation.Type.ERRATA,
        software_build=released_build,
        build_id=released_build.build_id,
        build_type=released_build.build_type,
    )

    ComponentFactory(name="released", software_build=released_build)
    ComponentFactory(name="unreleased", software_build=unreleased_build)

    response = client.get(f"{api_path}/components")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    response = client.get(f"{api_path}/components?released_components=True")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["name"] == "released"

    response = client.get(f"{api_path}/components?released_components=False")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["name"] == "unreleased"


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_root_components_filter(client, api_path):
    ComponentFactory(name="srpm", type="RPM", arch="src")
    ComponentFactory(name="binary_rpm", type="RPM", arch="x86_64")

    response = client.get(f"{api_path}/components")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    response = client.get(f"{api_path}/components?root_components=True")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["name"] == "srpm"

    response = client.get(f"{api_path}/components?root_components=False")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["name"] == "binary_rpm"


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_component_detail_unscanned_filter(client, api_path):
    ComponentFactory(name="copyright", copyright_text="test", license_concluded_raw="")
    ComponentFactory(name="license", license_concluded_raw="test")
    ComponentFactory(name="unscanned", license_concluded_raw="")

    response = client.get(f"{api_path}/components")
    assert response.status_code == 200
    assert response.json()["count"] == 3

    response = client.get(f"{api_path}/components?missing_copyright=True")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2
    assert response["results"][0]["name"] == "license"
    assert response["results"][1]["name"] == "unscanned"

    response = client.get(f"{api_path}/components?missing_copyright=False")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["name"] == "copyright"

    response = client.get(f"{api_path}/components?missing_license=True")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2
    assert response["results"][0]["name"] == "copyright"
    assert response["results"][1]["name"] == "unscanned"

    response = client.get(f"{api_path}/components?missing_license=False")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["name"] == "license"


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_component_detail_olcs_put(client, api_path):
    """Test that OpenLCS can upload scan results for a component"""
    c1 = ComponentFactory(license_concluded_raw="")
    component_path = f"{api_path}/components/{c1.uuid}"
    openlcs_data = {
        "copyright_text": "Copyright Test",
        "license_concluded": "BSD or MIT",
        "openlcs_scan_url": "a link",
        "openlcs_scan_version": "a version",
    }

    # User for OpenLCS authentication
    olcs_user = User.objects.create_user(username="olcs", email="olcs@example.com")
    olcs_token = Token.objects.create(user=olcs_user, key="mysteries_quirewise_volitant_woolshed")

    response = client.get(component_path)
    assert response.status_code == 200
    response = response.json()
    for key in openlcs_data:
        # Values are unset by default
        assert response[key] == ""

    # Require authentication for put
    response = client.put(f"{component_path}/update_license", data=openlcs_data, format="json")
    assert response.status_code == 401

    # Authenticate
    client.credentials(HTTP_AUTHORIZATION=f"Token {olcs_token.key}")

    # Subtly different from requests.put(), so json= kwarg doesn't work
    # Below should use follow=True, but it's currently broken
    # https://github.com/encode/django-rest-framework/discussions/8695
    response = client.put(f"{component_path}/update_license", data=openlcs_data, format="json")
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
    response = client.put(f"{component_path}/update_license", data={}, format="json")
    assert response.status_code == 400

    # Return a 404 if that component can't be found
    response = client.put(
        f"{api_path}/components/00000000-0000-0000-0000-000000000000/update_license",
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
    upstream_node = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        obj=upstream,
    )
    dev_comp = ComponentFactory(
        name="dev", type=Component.Type.NPM, namespace=Component.Namespace.REDHAT
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES_DEV,
        parent=upstream_node,
        obj=dev_comp,
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

    # Write should only be allowed through the specified endpoint,
    # even for authenticated users.
    olcs_user = User.objects.create_user(username="olcs", email="olcs@example.com")
    olcs_token = Token.objects.create(user=olcs_user, key="mysteries_quirewise_volitant_woolshed")
    client.credentials(HTTP_AUTHORIZATION=f"Token {olcs_token.key}")
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


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_stream_tag_filter(client, api_path):
    ProductStreamFactory()
    response = client.get(f"{api_path}/product_streams?tags=manifest")
    assert response.status_code == 200
    assert response.json()["count"] == 0

    the_second_stream = ProductStreamFactory(tag__name="manifest", tag__value="")
    response = client.get(f"{api_path}/product_streams?tags=manifest")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["name"] == the_second_stream.name


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_stream_without_tag_filter(client, api_path):
    the_first_stream = ProductStreamFactory()
    assert ProductStream.objects.exclude(tags__name="manifest").count() == 1
    response = client.get(f"{api_path}/product_streams?tags=!manifest")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    ProductStreamFactory(tag__name="manifest", tag__value="")
    assert ProductStream.objects.exclude(tags__name="manifest").count() == 1
    response = client.get(f"{api_path}/product_streams?tags=!manifest")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["name"] == the_first_stream.name


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
    c1 = SrpmComponentFactory(name="curl", version="7.19.7", release="35.el6")
    response = client.get(f"{api_path}/components?type=RPM&arch=src")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(c1.uuid)

    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    response = response.json()
    # No "count" key here because the APi does a retrieve() / returns exactly one result
    # It doesn't do a list() / return multiple results
    assert response["name"] == c1.name

    response = client.get(f"{api_path}/components?type=RPM&name={c1.name}")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(c1.uuid)

    response = client.get(f"{api_path}/components?type=RPM&re_purl={c1.name}")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(c1.uuid)

    # Regex filters should be case-insensitive, give the same results
    response = client.get(f"{api_path}/components?type=RPM&re_purl={c1.name.upper()}")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(c1.uuid)


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
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(c1.uuid)

    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    response = response.json()
    # No "count" key here because the APi does a retrieve() / returns exactly one result
    # It doesn't do a list() / return multiple results
    assert response["uuid"] == str(c1.uuid)

    response = client.get(f"{api_path}/components?re_name=^autotrace(-devel|-libs|-utils)$")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(c1.uuid)

    # Regex filters should be case-insensitive, give the same results
    response = client.get(f"{api_path}/components?re_name=^AUTOTRACE(-devel|-libs|-utils)$")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(c1.uuid)


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_re_purl_filter(client, api_path):
    c1 = ComponentFactory(
        type=Component.Type.RPM, namespace=Component.Namespace.REDHAT, name="autotrace-devel"
    )
    response = client.get(f"{api_path}/components?type=RPM")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(c1.uuid)

    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.status_code == 200
    response = response.json()
    # No "count" key here because the APi does a retrieve() / returns exactly one result
    # It doesn't do a list() / return multiple results
    assert response["uuid"] == str(c1.uuid)

    response = client.get(rf"{api_path}/components?re_purl=^(.*)\/redhat\/autotrace(.*)$")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(c1.uuid)

    # Regex filters should be case-insensitive, give the same results
    response = client.get(rf"{api_path}/components?re_purl=^(.*)\/REDHAT\/AUTOTRACE(.*)$")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(c1.uuid)


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_nvr_nevra_filter(client, api_path):
    c1 = ComponentFactory(
        type=Component.Type.RPM,
        epoch=1,
        name="autotrace-devel",
        version="3.2.1",
        release="1.0.1e",
        arch="noarch",
    )
    response = client.get(f"{api_path}/components?type=RPM")
    assert response.status_code == 200
    response = client.get(f"{api_path}/components/{c1.uuid}")
    assert response.json()["epoch"] == 1
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
    rhel_8_5_stream = ProductStreamFactory(name="rhel-8.5.0-z", version="8.5.0-z")
    rhel_av_8_5_stream = ProductStreamFactory(
        name="rhel-av-8.5.0-z", version="8.5.0-z", active=False
    )

    response = client.get(f"{api_path}/product_streams")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["uuid"] == str(rhel_8_5_stream.uuid)

    response = client.get(f"{api_path}/product_streams?active=all")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2
    assert response["results"][0]["uuid"] == str(rhel_8_5_stream.uuid)
    assert response["results"][1]["uuid"] == str(rhel_av_8_5_stream.uuid)

    response = client.get(f"{api_path}/product_streams?ofuri={rhel_av_8_5_stream.ofuri}")
    assert response.status_code == 200
    response = response.json()
    # No "count" key here because the APi does a retrieve() / returns exactly one result
    # It doesn't do a list() / return multiple results
    assert response["uuid"] == str(rhel_av_8_5_stream.uuid)

    response = client.get(f"{api_path}/product_streams?ofuri={rhel_8_5_stream.ofuri}")
    assert response.status_code == 200
    response = response.json()
    # No "count" key here because the APi does a retrieve() / returns exactly one result
    # It doesn't do a list() / return multiple results
    assert response["uuid"] == str(rhel_8_5_stream.uuid)

    response = client.get(f"{api_path}/product_streams?name={rhel_av_8_5_stream.name}")
    assert response.status_code == 200
    response = response.json()
    # No "count" key here because the APi does a retrieve() / returns exactly one result
    # It doesn't do a list() / return multiple results
    assert response["name"] == rhel_av_8_5_stream.name

    response = client.get(f"{api_path}/product_streams?re_name=rhel&view=summary")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["name"] == str(rhel_8_5_stream.name)

    # Regex filters should be case-insensitive, give the same results
    response = client.get(f"{api_path}/product_streams?re_name=RHEL&view=summary")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
    assert response["results"][0]["name"] == str(rhel_8_5_stream.name)


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
def test_product_components_ofuri(client, api_path, stored_proc):
    """test 'latest' filter on components"""

    ps1 = ProductStreamFactory(name="rhel-8.6.0", version="8.6.0")
    assert ps1.ofuri == "o:redhat:rhel:8.6.0"
    ps2 = ProductStreamFactory(name="rhel-8.6.0.z", version="8.6.0.z")
    assert ps2.ofuri == "o:redhat:rhel:8.6.0.z"
    ps3 = ProductStreamFactory(name="rhel-8.5.0", version="8.5.0")
    assert ps3.ofuri == "o:redhat:rhel:8.5.0"

    old_openssl = SrpmComponentFactory(name="openssl", version="1.1.1k", release="5.el8_5")
    old_openssl.productstreams.add(ps1)

    openssl = SrpmComponentFactory(name="openssl", version="1.1.1k", release="6.el8_5")
    openssl.productstreams.add(ps1)

    old_curl = SrpmComponentFactory(name="curl", version="7.61.1", release="14.el8")
    old_curl.productstreams.add(ps2)

    curl = SrpmComponentFactory(name="curl", version="7.61.1", release="22.el8_6.3")
    curl.productstreams.add(ps2)

    response = client.get(f"{api_path}/components?ofuri=o:redhat:rhel:8.6.0.z")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["nvr"] == "curl-7.61.1-22.el8_6.3"

    response = client.get(f"{api_path}/components?ofuri=o:redhat:rhel:8.6.0")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["nvr"] == "openssl-1.1.1k-6.el8_5"

    # stream with no components
    response = client.get(f"{api_path}/components?ofuri=o:redhat:rhel:8.5.0")
    assert response.status_code == 200
    assert response.json()["count"] == 0

    response = client.get(
        f"{api_path}/components?ofuri=o:redhat:rhel:8.6.0&include_fields=purl,product_streams.name"
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert "purl" in response.json()["results"][0]
    assert len(response.json()["results"][0]["product_streams"]) == 1
    assert "name" in response.json()["results"][0]["product_streams"][0]
    assert "purl" not in response.json()["results"][0]["product_streams"][0]
    assert response.json()["results"][0]["product_streams"][0]["name"] == "rhel-8.6.0"


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_components_versions(client, api_path, stored_proc):
    ps1 = ProductStreamFactory(name="rhel-7", version="7")
    assert ps1.ofuri == "o:redhat:rhel:7"
    ps2 = ProductStreamFactory(name="rhel-8", version="8")
    assert ps2.ofuri == "o:redhat:rhel:8"

    openssl = ComponentFactory(
        type=Component.Type.RPM, arch="x86_64", name="openssl", version="1.1.1k", release="5.el8_5"
    )

    openssl.productstreams.add(ps2)
    openssl_srpm = SrpmComponentFactory(name="openssl", version="1.1.1k", release="6.el8_5")
    openssl_srpm.productstreams.add(ps2)

    curl = ComponentFactory(
        type=Component.Type.RPM, arch="x86_64", name="curl", version="7.61.1", release="22.el8_6.3"
    )
    curl.productstreams.add(ps1)
    curl_srpm = SrpmComponentFactory(name="curl", version="7.61.1", release="22.el8_6.3")
    curl_srpm.productstreams.add(ps1)

    response = client.get(f"{api_path}/components?product_streams={ps2.ofuri}")
    assert response.status_code == 200
    assert response.json()["count"] == 2
    response = client.get(f"{api_path}/components?product_streams={ps2.name}")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    # ofuri returns 'latest' build root components (eg. including SRPM,
    #  noarch CONTAINER_IMAGE and RHEL_MODULE)
    response = client.get(f"{api_path}/components?ofuri={ps1.ofuri}")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/components?name={curl.name}&view=product")
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

    response = client.get(f"{api_path}/components?products={rhel.ofuri}")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    response = client.get(f"{api_path}/components?products={rhel.name}")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/components?products={rhel_br.ofuri}")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    response = client.get(f"{api_path}/components?products={rhel_br.name}")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_oci_component_provides_sources_upstreams(client, api_path):
    """
    Given an OCI component node structure:
    /
    └──(SOURCE) root_comp: top level root component
        ├──(SOURCE) upstream_comp
        └──(PROVIDES) dep1_comp
            └──(PROVIDES) dep2_comp

    The following are expectations in terms of sources, providers and upstreams on these components:

    +--------------------+---------+----------+-----------+
    |                    | sources | provides | upstreams |
    +--------------------+---------+----------+-----------+
    | root_component     | 0       | 2        | 1         |
    +--------------------+---------+----------+-----------+
    | upstream_component | 0       | 0        | 0         |
    +--------------------+---------+----------+-----------+
    | dep1_component     | 1       | 1        | 0         |
    +--------------------+---------+----------+-----------+
    | dep2_component     | 2       | 0        | 0         |
    +--------------------+---------+----------+-----------+

    The data model does not impose this constraint
    eg. provides, sources and upstream property queries enforce this behaviour.

    """

    api_path_with_domain = f"https://{settings.CORGI_DOMAIN}{api_path}"
    # create a top level root source component
    root_comp = ComponentFactory(
        name="root_comp",
        type=Component.Type.CONTAINER_IMAGE,
        arch="noarch",
        namespace=Component.Namespace.REDHAT,
        related_url="https://example.org/related",
    )
    root_node = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        obj=root_comp,
    )

    # create upstream child node
    upstream_comp = ComponentFactory(
        name="upstream_comp",
        type=Component.Type.RPM,
        namespace=Component.Namespace.UPSTREAM,
        related_url="https://example.org/related",
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=root_node,
        obj=upstream_comp,
    )

    # create dep component
    dep_comp = ComponentFactory(
        name="cool_dep_component",
        arch="src",
        type=Component.Type.RPM,
        namespace=Component.Namespace.REDHAT,
    )
    # make it a child of root OCI component
    dep_provide_node = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=root_node,
        obj=dep_comp,
    )
    # create 2nd level dep
    dep2_comp = ComponentFactory(
        name="cool_dep2_component",
        type=Component.Type.RPM,
        arch="aarch64",
        namespace=Component.Namespace.REDHAT,
    )
    # making it a child node of dep1_component
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=dep_provide_node,
        obj=dep2_comp,
    )

    # TODO - investigate if invoking any of the following in any order is stable
    # it is intentional to invoke these twice during the test
    upstream_comp.save_component_taxonomy()
    upstream_comp.save()
    dep2_comp.save_component_taxonomy()
    dep2_comp.save()
    dep_comp.save_component_taxonomy()
    dep_comp.save()
    root_comp.save_component_taxonomy()
    root_comp.save()
    upstream_comp.save_component_taxonomy()
    upstream_comp.save()
    dep2_comp.save_component_taxonomy()
    dep2_comp.save()
    dep_comp.save_component_taxonomy()
    dep_comp.save()
    root_comp.save_component_taxonomy()
    root_comp.save()

    assert dep_comp.purl in root_comp.provides.values_list("purl", flat=True)
    assert dep2_comp.purl in root_comp.provides.values_list("purl", flat=True)

    response = client.get(f"{api_path}/components?namespace=REDHAT")
    assert response.status_code == 200
    assert response.json()["count"] == 3

    response = client.get(f"{api_path}/components/{root_comp.uuid}")
    assert response.status_code == 200
    response = response.json()

    assert response["name"] == root_comp.name
    assert response["related_url"] == root_comp.related_url
    assert (
        response["sources"] == f"{api_path_with_domain}/components?provides={quote(root_comp.purl)}"
    )
    related_response = client.get(response["sources"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 0

    assert (
        response["provides"] == f"{api_path_with_domain}/components?sources={quote(root_comp.purl)}"
    )
    related_response = client.get(response["provides"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 2

    assert (
        response["upstreams"]
        == f"{api_path_with_domain}/components?downstreams={quote(root_comp.purl)}"
    )
    related_response = client.get(response["upstreams"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 1

    response = client.get(f"{api_path}/components/{dep_comp.uuid}")
    assert response.status_code == 200
    response = response.json()
    assert (
        response["sources"] == f"{api_path_with_domain}/components?provides={quote(dep_comp.purl)}"
    )
    related_response = client.get(response["sources"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 1

    assert (
        response["provides"] == f"{api_path_with_domain}/components?sources={quote(dep_comp.purl)}"
    )
    related_response = client.get(response["provides"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 1

    assert (
        response["upstreams"]
        == f"{api_path_with_domain}/components?downstreams={quote(dep_comp.purl)}"
    )
    related_response = client.get(response["upstreams"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 0

    response = client.get(f"{api_path}/components/{dep2_comp.uuid}")
    assert response.status_code == 200
    response = response.json()
    assert (
        response["sources"] == f"{api_path_with_domain}/components?provides={quote(dep2_comp.purl)}"
    )
    related_response = client.get(response["sources"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 2

    assert (
        response["provides"] == f"{api_path_with_domain}/components?sources={quote(dep2_comp.purl)}"
    )
    related_response = client.get(response["provides"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 0

    assert (
        response["upstreams"]
        == f"{api_path_with_domain}/components?downstreams={quote(dep2_comp.purl)}"
    )
    related_response = client.get(response["upstreams"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 0

    response = client.get(f"{api_path}/components/{upstream_comp.uuid}")
    assert response.status_code == 200
    response = response.json()
    assert (
        response["sources"]
        == f"{api_path_with_domain}/components?provides={quote(upstream_comp.purl)}"
    )
    related_response = client.get(response["sources"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 0

    assert (
        response["provides"]
        == f"{api_path_with_domain}/components?sources={quote(upstream_comp.purl)}"
    )
    related_response = client.get(response["provides"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 0

    assert (
        response["upstreams"]
        == f"{api_path_with_domain}/components?downstreams={quote(upstream_comp.purl)}"
    )
    related_response = client.get(response["upstreams"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 0

    # retrieve all sources of root_comp
    response = client.get(f"{api_path}/components?provides={quote(root_comp.purl)}")
    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["count"] == 0
    # retrieve all provides of root_comp
    response = client.get(f"{api_path}/components?sources={quote(root_comp.purl)}")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    # retrieve all sources of dep_comp component
    response = client.get(f"{api_path}/components?provides={quote(dep_comp.purl)}")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    # retrieve all provides of dep_comp
    response = client.get(f"{api_path}/components?sources={quote(dep_comp.purl)}")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    # retrieve all sources of dep2_comp component
    response = client.get(f"{api_path}/components?provides={quote(dep2_comp.purl)}")
    assert response.status_code == 200
    assert response.json()["count"] == 2
    # retrieve all provides of dep2_comp
    response = client.get(f"{api_path}/components?sources={quote(dep2_comp.purl)}")
    assert response.status_code == 200
    assert response.json()["count"] == 0

    # retrieve all components with upstream_comp upstream
    response = client.get(f"{api_path}/components?upstreams={quote(upstream_comp.purl)}")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    response = client.get(f"{api_path}/components/{root_comp.uuid}/taxonomy")
    assert response.status_code == 200
    assert len(response.json()[0]["provides"]) == 3

    response = client.get(
        f"{api_path}/components/{root_comp.uuid}?include_fields=provides.name,provides.purl"
    )
    assert response.status_code == 200
    assert "upstreams" not in response.json()
    assert (
        response.json()["provides"]
        == f"{api_path_with_domain}/components?sources={quote(root_comp.purl)}"
    )
    related_response = client.get(response.json()["provides"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 2

    response = client.get(
        f"{api_path}/components/{root_comp.uuid}?include_fields=upstreams.name,upstreams.purl"
    )
    assert response.status_code == 200
    assert "provides" not in response.json()
    assert (
        response.json()["upstreams"]
        == f"{api_path_with_domain}/components?downstreams={quote(root_comp.purl)}"
    )
    related_response = client.get(response.json()["upstreams"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 1

    response = client.get(
        f"{api_path}/components/{dep_comp.uuid}?include_fields=sources.name,sources.purl"
    )
    assert response.status_code == 200
    assert "provides" not in response.json()
    assert (
        response.json()["sources"]
        == f"{api_path_with_domain}/components?provides={quote(dep_comp.purl)}"
    )
    related_response = client.get(response.json()["sources"])
    assert related_response.status_code == 200
    assert related_response.json()["count"] == 1


def _get_related_data_from_link(client, link):
    """Helper method to fetch and return provides / sources / upstreams data
    from a separately-linked page, as if it were still inline on a component's main page"""
    response = client.get(link)
    assert response.status_code == 200
    return response.json()


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_srpm_component_provides_sources_upstreams(client, api_path):
    """
    Given a SRPM root component node structure:
    /
    └──(SOURCE) root_comp: top level root component
        ├──(SOURCE) upstream_comp
        └──(PROVIDES) dep1_comp

    Then we expect the following in terms of sources, providers and upstreams for these components:

    +--------------------+---------+----------+-----------+-------------+
    |                    | sources | provides | upstreams | downstreams |
    +--------------------+---------+----------+-----------+-------------+
    | root_component     | 0       | 1        | 1         | 0           |
    +--------------------+---------+----------+-----------+-------------+
    | upstream_component | 0       | 0        | 0         | 2           |
    +--------------------+---------+----------+-----------+-------------+
    | dep1_component     | 1       | 0        | 1         | 0           |
    +--------------------+---------+----------+-----------+-------------+

    This behaviour is predicated on the following assumptions:
    * one SRPM provides many binary RPMs
    * binary RPMs may provide children of their own (none here)
    * one SRPM has exactly one upstream (containers can have many upstreams)
    * sources is the inverse of provides
    * downstreams is the inverse of upstreams

    The data model does not impose these constraints
    eg. provides, sources, upstreams, and downstreams property queries enforce these behaviours.

    """

    api_path_with_domain = f"https://{settings.CORGI_DOMAIN}{api_path}"
    # create a top level root source component
    root_comp = SrpmComponentFactory(
        name="root_comp",
        related_url="https://example.org/related",
    )
    root_node = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        obj=root_comp,
    )

    # create upstream child node
    upstream_comp = UpstreamComponentFactory(
        name="upstream_comp",
        type=Component.Type.RPM,
        related_url="https://example.org/related",
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=root_node,
        obj=upstream_comp,
    )
    # create dep component
    dep_comp = BinaryRpmComponentFactory(name="cool_dep_component")
    # make it a child of root OCI component
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=root_node,
        obj=dep_comp,
    )

    root_comp.save_component_taxonomy()
    upstream_comp.save_component_taxonomy()
    dep_comp.save_component_taxonomy()

    response = client.get(f"{api_path}/components?namespace=REDHAT")
    assert response.status_code == 200
    assert response.json()["count"] == 2

    response = client.get(f"{api_path}/components?namespace=UPSTREAM")
    assert response.status_code == 200
    assert response.json()["count"] == 1

    # Check sources, provides, upstreams, and downstreams for root (source RPM)
    response = client.get(f"{api_path}/components/{root_comp.uuid}")
    assert response.status_code == 200
    response = response.json()

    assert (
        response["sources"] == f"{api_path_with_domain}/components?provides={quote(root_comp.purl)}"
    )
    related_response = _get_related_data_from_link(client, response["sources"])
    assert related_response["count"] == 0

    assert (
        response["provides"] == f"{api_path_with_domain}/components?sources={quote(root_comp.purl)}"
    )
    related_response = _get_related_data_from_link(client, response["provides"])
    assert related_response["count"] == 1

    assert (
        response["upstreams"]
        == f"{api_path_with_domain}/components?downstreams={quote(root_comp.purl)}"
    )
    related_response = _get_related_data_from_link(client, response["upstreams"])
    assert related_response["count"] == 1

    # TODO: Uncomment when we expose this on the API
    #  Without this, users can go from a downstream component to its upstream
    #  but it's currently impossible to go from the upstream back to its downstream
    # assert (
    #     response["downstreams"]
    #     == f"{api_path_with_domain}/components?upstreams={quote(root_comp.purl)}"
    # )
    # related_response = _get_related_data_from_link(client, response["downstreams"])
    # assert related_response["count"] == 0

    # Check sources, provides, upstreams, and downstreams for dep (binary RPM)
    response = client.get(f"{api_path}/components/{dep_comp.uuid}")
    assert response.status_code == 200
    response = response.json()
    assert (
        response["sources"] == f"{api_path_with_domain}/components?provides={quote(dep_comp.purl)}"
    )
    related_response = _get_related_data_from_link(client, response["sources"])
    assert related_response["count"] == 1

    assert (
        response["provides"] == f"{api_path_with_domain}/components?sources={quote(dep_comp.purl)}"
    )
    related_response = _get_related_data_from_link(client, response["provides"])
    assert related_response["count"] == 0

    assert (
        response["upstreams"]
        == f"{api_path_with_domain}/components?downstreams={quote(dep_comp.purl)}"
    )
    related_response = _get_related_data_from_link(client, response["upstreams"])
    assert related_response["count"] == 1

    # assert (
    #     response["downstreams"]
    #     == f"{api_path_with_domain}/components?upstreams={quote(dep_comp.purl)}"
    # )
    # related_response = _get_related_data_from_link(client, response["downstreams"])
    # assert related_response["count"] == 0

    # Check sources, provides, upstreams, and downstreams for upstream (noarch RPM)
    response = client.get(f"{api_path}/components/{upstream_comp.uuid}")
    assert response.status_code == 200
    response = response.json()
    assert (
        response["sources"]
        == f"{api_path_with_domain}/components?provides={quote(upstream_comp.purl)}"
    )
    related_response = _get_related_data_from_link(client, response["sources"])
    assert related_response["count"] == 0

    assert (
        response["provides"]
        == f"{api_path_with_domain}/components?sources={quote(upstream_comp.purl)}"
    )
    related_response = _get_related_data_from_link(client, response["provides"])
    assert related_response["count"] == 0

    assert (
        response["upstreams"]
        == f"{api_path_with_domain}/components?downstreams={quote(upstream_comp.purl)}"
    )
    related_response = _get_related_data_from_link(client, response["upstreams"])
    assert related_response["count"] == 0

    # assert (
    #     response["downstreams"]
    #     == f"{api_path_with_domain}/components?upstreams={quote(upstream_comp.purl)}"
    # )
    # related_response = _get_related_data_from_link(client, response["downstreams"])
    # assert related_response["count"] == 2

    response = client.get(f"{api_path}/components/{root_comp.uuid}/taxonomy")
    assert response.status_code == 200
    response = response.json()
    # TODO: Calling this "provides" is incorrect. It includes "upstreams" too
    #  We should call it either "children" (if it only shows the first level of the tree)
    #  or "descendants" (if it shows all levels of the tree)
    assert len(response[0]["provides"]) == 2


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_streams_exclude_components(client, api_path):
    stream = ProductStreamFactory(
        name="rhel-7", version="7", exclude_components=["starfish", "seahorse"]
    )
    response = client.get(f"{api_path}/product_streams?ofuri={stream.ofuri}")
    assert response.status_code == 200
    response = response.json()
    assert response["exclude_components"] == ["starfish", "seahorse"]


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_latest_components_by_active_filter(client, api_path):
    active_stream = ProductStreamFactory(active=True)
    other_active_stream = ProductStreamFactory(active=True)
    inactive_stream = ProductStreamFactory(active=False)

    for active_name in "red", "orange":
        new_srpm = SrpmComponentFactory(name=active_name)
        new_srpm.productstreams.add(active_stream)
        for arch in "aarch64", "x86_64":
            new_binary_rpm = BinaryRpmComponentFactory(
                name=active_name,
                arch=arch,
            )
            new_binary_rpm.sources.add(new_srpm)
            new_binary_rpm.productstreams.add(active_stream)

    for active_name in "blue", "green":
        new_srpm = SrpmComponentFactory(name=active_name)
        new_srpm.productstreams.add(active_stream)
        new_srpm.productstreams.add(other_active_stream)
        for arch in "aarch64", "x86_64":
            new_binary_rpm = BinaryRpmComponentFactory(
                name=active_name,
                arch=arch,
            )
            new_binary_rpm.sources.add(new_srpm)
            new_binary_rpm.productstreams.add(active_stream)
            new_binary_rpm.productstreams.add(other_active_stream)

    for inactive_name in "purple", "pink":
        inactive_component = BinaryRpmComponentFactory(
            name=inactive_name,
        )
        inactive_component.productstreams.add(inactive_stream)

    for both_name in "brown", "yellow":
        both_component = BinaryRpmComponentFactory(
            name=both_name,
        )
        both_component.productstreams.add(active_stream)
        both_component.productstreams.add(inactive_stream)

    response = client.get(f"{api_path}/components")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 16

    response = client.get(f"{api_path}/components?active_streams=True")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 14

    response = client.get(f"{api_path}/components?active_streams=False")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_re_provides_upstreams_names(setup_gin_extension, client, api_path):
    """
    Given an OCI component node structure:
    /
    └──(SOURCE) root_comp: top level root component
        ├──(SOURCE) upstream_comp
        └──(PROVIDES) dep1_comp
            └──(PROVIDES) dep2_comp

    The following are expectations in terms of sources, providers and upstreams on these components:

    +--------------------+---------+----------+-----------+
    |                    | sources | provides | upstreams |
    +--------------------+---------+----------+-----------+
    | root_component     | 0       | 2        | 1         |
    +--------------------+---------+----------+-----------+
    | upstream_component | 0       | 0        | 0         |
    +--------------------+---------+----------+-----------+
    | dep1_component     | 1       | 1        | 0         |
    +--------------------+---------+----------+-----------+
    | dep2_component     | 2       | 0        | 0         |
    +--------------------+---------+----------+-----------+

    The data model does not impose this constraint
    eg. provides, sources and upstream property queries enforce this behaviour.

    """

    # create a top level root source component
    root_comp = ComponentFactory(
        name="root_comp",
        type=Component.Type.CONTAINER_IMAGE,
        arch="noarch",
        namespace=Component.Namespace.REDHAT,
        related_url="https://example.org/related",
    )
    root_node = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        obj=root_comp,
    )

    # create upstream child node
    upstream_comp = ComponentFactory(
        name="upstream_comp",
        type=Component.Type.RPM,
        namespace=Component.Namespace.UPSTREAM,
        related_url="https://example.org/related",
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=root_node,
        obj=upstream_comp,
    )

    # create dep component
    dep_comp = ComponentFactory(
        name="cool_dep_component",
        arch="src",
        type=Component.Type.RPM,
        namespace=Component.Namespace.REDHAT,
    )
    # make it a child of root OCI component
    dep_provide_node = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=root_node,
        obj=dep_comp,
    )
    # create 2nd level dep
    dep2_comp = ComponentFactory(
        name="cool_dep2_component",
        type=Component.Type.RPM,
        arch="aarch64",
        namespace=Component.Namespace.REDHAT,
    )
    # making it a child node of dep1_component
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=dep_provide_node,
        obj=dep2_comp,
    )
    root_comp.save_component_taxonomy()
    dep_comp.save_component_taxonomy()
    dep2_comp.save_component_taxonomy()

    response = client.get(f"{api_path}/components")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 4

    response = client.get(f"{api_path}/components?re_name=cool")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2

    response = client.get(f"{api_path}/components?re_purl=oci/root_c")
    assert response.status_code == 200
    response = response.json()
    assert "pkg:oci/root_comp?tag=" in response["results"][0]["purl"]
    assert response["count"] == 1

    response = client.get(f"{api_path}/components?sources={quote(root_comp.purl)}")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2

    response = client.get(f"{api_path}/components?re_sources=root_")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2

    response = client.get(f"{api_path}/components?re_sources_name=root_")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2

    response = client.get(f"{api_path}/components?provides={quote(dep_comp.purl)}")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1

    response = client.get(f"{api_path}/components?re_provides=dep_")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1

    response = client.get(f"{api_path}/components?re_provides_name=dep")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 2

    response = client.get(f"{api_path}/components?upstreams={quote(upstream_comp.purl)}")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1

    response = client.get(f"{api_path}/components?re_upstreams=stream_")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1

    response = client.get(f"{api_path}/components?re_upstreams_name=upstream_")
    assert response.status_code == 200
    response = response.json()
    assert response["count"] == 1
