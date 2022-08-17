# -----------------
# Corgi smoke tests
# -----------------
#
# Smoke tests are designed to be run against live environments
#
# > python3 scripts/smoke-tests.py {HOST}
#
# if no host is provided defaults to localhost:8000
#
# Note- require installation of corgi-bindings:

import argparse
import logging
import sys

import corgi_bindings
import requests

# setup logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

# get args
parser = argparse.ArgumentParser()
parser.add_argument("corgi_url", default="http://localhost:8000", nargs="?")
args = parser.parse_args()

logger.info("smoke-test: start")

# simple rest api checks
logger.info("smoke-test: simple REST-API checks")
response = requests.get(args.corgi_url)
response.raise_for_status()
response = requests.get(f"{args.corgi_url}/api/v1/")
response.raise_for_status()
response = requests.get(f"{args.corgi_url}/api/v1/non-existent-endpoint")
assert response.status_code == 404

logger.info("smoke-test: access REST API")
session = corgi_bindings.new_session(corgi_server_uri=f"{args.corgi_url}")

status = session.status()
products = session.products.retrieve_list()
product_versions = session.product_versions.retrieve_list()
product_streams = session.product_streams.retrieve_list()
product_variants = session.product_variants.retrieve_list()
components = session.components.retrieve_list()

# check specific streams
stream_corpus = [
    "rhel-8.4.0.z",
    "rhel-8.5.0.z",
    "rhel-8.6.0.z",
    "rhel-8.7.0.z",
    "rhel-8.8.0.z",
    "rhel-8.9.0.z",
    "rhel-8.10.0.z",
    "rhel-9.0.0.z",
    "openshift-4.6.z",
    "openshift-4.7.z",
    "openshift-4.8.z",
    "openshift-4.9.z",
    "openshift-4.10.z",
    "rhacm-2.3.z",
    "rhacm-2.4.z",
    "rhacm-2.5.z",
    "ansible_automation_platform-1.2",
    "ansible_automation_platform-2.0",
    "ansible_automation_platform-2.1",
    "ansible_automation_platform-2.2",
    "rhn_satellite_6.7",
    "rhn_satellite_6.8",
    "rhn_satellite_6.9",
    "rhn_satellite_6.10",
    "rhn_satellite_6.11",
]

# check if stream exists in product stream
for stream_name in stream_corpus:
    logger.info(f"check product_stream: {stream_name}.")
    stream = session.product_streams.retrieve_list(name=stream_name)
    if not stream.results:
        logger.warning(f"{stream_name} does not exist")
    # TODO - add more checks (ex. # of builds, specific builds, coverage, etc, etc)

logger.info("smoke-test: done")
