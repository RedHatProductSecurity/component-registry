import timeit

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
    CORGI_API_URL = f"http://localhost:8008/api/{CORGI_API_VERSION}"

pytestmark = [pytest.mark.performance]


def display_component_with_many_sources() -> requests.Response:
    """Helper method for timeit to get a component with many sources"""
    large_component_purl = "pkg:rpm/redhat/systemd-libs@250-12.el9_1?arch=aarch64"
    response = requests.get(f"{CORGI_API_URL}/components?purl={large_component_purl}")

    # If you're running performance tests manually against a dev environment,
    # make sure that you've loaded the right data for this test
    # Any exceptions will be passed through to test code
    response.raise_for_status()
    response = response.json()
    assert len(response["sources"]) > 2000
    return response


def test_displaying_component_with_many_sources():
    """Test that displaying a component with many sources does not take a long time"""
    # Slow /components endpoint and web pod restarts (OoM) were fixed in CORGI-507
    # Now we use .iterator() so that components with many sources / provides
    # don't time out or exhaust all the web pod's memory
    timer = timeit.Timer(display_component_with_many_sources)

    # 3 test results, each of which is the average of 3 requests
    test_results = sorted(timer.repeat(repeat=3, number=3))
    assert len(test_results) == 3
    median_time_taken = test_results[1]
    assert median_time_taken < 5.0
