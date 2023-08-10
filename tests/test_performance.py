import timeit
from urllib.parse import quote_plus

import pytest
import requests
from django.conf import settings

# Performance tests always run against a live environment (stage by default)
# to detect slow queries, missing indexes, and other issues that need large data volumes
# To run tests against a local development environment, make sure that CORGI_DOMAIN is unset
from corgi.api.constants import CORGI_API_VERSION

if settings.CORGI_DOMAIN:
    CORGI_API_URL = f"https://{settings.CORGI_DOMAIN}/api/{CORGI_API_VERSION}"
else:
    CORGI_API_URL = f"http://localhost:8080/api/{CORGI_API_VERSION}"

pytestmark = [pytest.mark.performance]


def display_product_stream_with_many_roots() -> dict:
    stream_with_many_root = "o:redhat:rhel:7.9.z"
    response = requests.get(
        f"{CORGI_API_URL}/components?ofuri={stream_with_many_root}" f"&view=summary&limit=5000"
    )

    # If you're running performance tests manually against a dev environment,
    # make sure that you've loaded the right data for this test
    # Any exceptions will be passed through to test code
    response.raise_for_status()
    response_json = response.json()
    assert response_json["count"] > 3000
    return response_json


def test_displaying_product_stream_with_many_roots() -> None:
    """Test that displaying a product stream with many root components does not take a long time"""
    # This request uses the latest_components filter to find the latest version for a component
    # the query was changed in CORGI-680 to do a single query for all components vs one for each
    # root component.
    timer = timeit.Timer(display_product_stream_with_many_roots)

    # 3 test results, each of which makes only 1 request
    test_results = sorted(timer.repeat(repeat=3, number=1))
    assert len(test_results) == 3
    median_time_taken = test_results[1]
    assert median_time_taken < 20.0


# This still sometimes raises a 502 Bad Gateway error
def display_component_with_many_sources() -> dict:
    """Helper method for timeit to get a component with many sources"""
    large_component_purl = "pkg:rpm/redhat/systemd-libs@250-12.el9_1?arch=aarch64"
    response = requests.get(f"{CORGI_API_URL}/components?purl={large_component_purl}")

    # If you're running performance tests manually against a dev environment,
    # make sure that you've loaded the right data for this test
    # Any exceptions will be passed through to test code
    response.raise_for_status()
    large_component_uuid = response.json()["uuid"]
    response = requests.get(f"{CORGI_API_URL}/components/{large_component_uuid}/sources")
    response.raise_for_status()
    response_json = response.json()
    assert response_json["count"] > 2000
    return response_json


def test_displaying_component_with_many_sources() -> None:
    """Test that displaying a component with many sources does not take a long time"""
    # Slow /components endpoint and web pod restarts (OoM) were fixed in CORGI-507
    # Now we use .iterator() so that components with many sources / provides
    # don't time out or exhaust all the web pod's memory
    # We also moved sources / provides / upstreams to a separate endpoint
    # which is also paginated, to fix Bad Gateway errors (CORGI-729)
    # This separate endpoint will be very slow if users set a high ?limit=
    # so we don't recommend / support this
    timer = timeit.Timer(display_component_with_many_sources)

    # 3 test results, each of which makes only 1 request
    test_results = sorted(timer.repeat(repeat=3, number=1))
    assert len(test_results) == 3
    median_time_taken = test_results[1]
    assert median_time_taken < 1.0


def display_manifest_with_many_components() -> dict:
    """Helper method for timeit to display a manifest with many components"""
    large_stream_ofuri = "o:redhat:rhel:9.2.0"
    response = requests.get(f"{CORGI_API_URL}/product_streams?ofuri={large_stream_ofuri}")
    response.raise_for_status()
    response_json = response.json()

    name = response_json["name"]
    uuid = response_json["uuid"]
    manifest_link = response_json["manifest"]
    assert manifest_link == f"{CORGI_API_URL.replace('/api/v1', '/staticfiles')}/{name}-{uuid}.json"

    response = requests.get(manifest_link)
    response.raise_for_status()
    response_json = response.json()

    assert len(response_json["packages"]) > 9000
    return response_json


def test_displaying_pregenerated_manifest() -> None:
    """Test that displaying a pre-generated stream manifest with many components is not slow"""
    # Slow manifests and web pod restarts (OoM) were fixed
    # We now pre-generate all the product stream manifests in a Celery task
    # so that displaying a large manifest doesn't time out or exhaust all the web pod's memory
    timer = timeit.Timer(display_manifest_with_many_components)

    # 3 test results, each of which makes only 1 request
    test_results = sorted(timer.repeat(repeat=3, number=1))
    assert len(test_results) == 3
    median_time_taken = test_results[1]
    assert median_time_taken < 12.0


def generate_manifest_with_many_components() -> dict:
    """Helper method for timeit to generate a manifest with many components"""
    large_component_purl = (
        "pkg:oci/ubi9-container"
        "@sha256:276b287ff6143f807342296908cc4ae09bfd584d66ba35dab5efc726b7be097b"
        "?repository_url=registry.redhat.io/ubi9&tag=9.1.0-1822"
    )
    response = requests.get(f"{CORGI_API_URL}/components?purl={quote_plus(large_component_purl)}")
    response.raise_for_status()
    response_json = response.json()

    manifest_link = f"{CORGI_API_URL}/components/{response_json['uuid']}/manifest?format=json"
    assert manifest_link == response_json["manifest"]

    response = requests.get(manifest_link)
    response.raise_for_status()
    response_json = response.json()

    assert len(response_json["packages"]) > 600
    return response_json


def test_generating_manifest() -> None:
    """Test that generating a component (UBI) manifest with many components is not slow"""
    # Slow manifests and web pod restarts (OoM) were fixed
    # We inlined templates, fixed some unneeded queries, added .iterator()
    # and fixed a bug that duplicated provided components in each manifest
    # so that generating a manifest for a large component like UBI9
    # doesn't time out or exhaust all the web pod's memory
    timer = timeit.Timer(generate_manifest_with_many_components)

    # 3 test results, each of which makes only 1 request
    test_results = sorted(timer.repeat(repeat=3, number=1))
    assert len(test_results) == 3
    median_time_taken = test_results[1]
    assert median_time_taken < 2.0
