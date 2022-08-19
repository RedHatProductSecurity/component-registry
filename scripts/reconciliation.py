# -----------------
# product reconciliation scripts
# -----------------
#
# Smoke tests are designed to be run against live environments
#
# > python3 scripts/reconciliation.py {HOST}
#
# if no host is provided defaults to localhost:8000
#
# Note- require installation of corgi-bindings:

import argparse
import json
import logging
import sys

import corgi_bindings
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
parser.add_argument("--deptopia_url")
parser.add_argument("corgi_url", default="http://localhost:8000")
args = parser.parse_args()

logger.info("smoke-test: start")

session = corgi_bindings.new_session(corgi_server_uri=args.corgi_url, verify_ssl=False)

# check specific streams [corgi ofuri, deptopia id]
stream_corpus = [
    ["o:redhat:rhn_satellite:6.7", "93"],
    ["o:redhat:rhn_satellite:6.8", "50"],
    ["o:redhat:rhn_satellite:6.9", "72"],
    ["o:redhat:ansible_automation_platform:1.2", "2085"],
    ["o:redhat:ansible_automation_platform:2.0", "2030"],
    ["o:redhat:ansible_automation_platform:2.1", "2154"],
    ["o:redhat:ansible_automation_platform:2.2", "2340"],
    ["o:redhat:openshift:4.9.z", "100"],
    ["o:redhat:rhacm:2.4.z", "1444"],
    ["o:redhat:rhel:8.6.0", "2302"],
]

# check if stream exists in product stream
for stream_ofuri, deptopia_id in stream_corpus:
    logger.info(f"reconciliation: check product_stream: {stream_ofuri}.")

    response = requests.get(
        f"{args.deptopia_url}/api/v1/products/id/{deptopia_id}/builds",  # noqa
        verify=False,
    )
    response.raise_for_status()
    deptopia_builds = json.loads(response.text)

    logger.info(f"deptopia ps_update_stream {deptopia_builds['ps_update_stream']}")

    success = []
    failure = []
    different = []

    for deptopia_build in reversed(deptopia_builds["builds"]):
        component_type = None
        if deptopia_build["build_type"] == "rpm":
            component_type = "SRPM"
        if deptopia_build["build_type"] == "image":
            component_type = "CONTAINER_IMAGE"
        if deptopia_build["build_type"] == "maven":
            component_type = "MAVEN"
        single_response = session.components.retrieve_list(
            nvr=deptopia_build["nvr"],
            type=component_type,
            ofuri=stream_ofuri,
        )
        if single_response.count > 0:
            logger.info("SUCCESS: %s", deptopia_build["nvr"] + "," + deptopia_build["build_type"])
            success.append(deptopia_build["nvr"])
        else:
            check_version = session.components.retrieve_list(
                name=deptopia_build["name"],
                type=component_type,
                ofuri=stream_ofuri,
            )
            if check_version.count > 0:
                logger.info(
                    "DIFFERENT: deptopia: %s | compreg: %s",
                    deptopia_build["nvr"] + "," + deptopia_build["build_type"],
                    str(check_version.count) + " " + check_version.results[0].nvr,
                )
                different.append(deptopia_build["nvr"])
            else:
                logger.warning(
                    "FAILURE: %s", deptopia_build["nvr"] + "," + deptopia_build["build_type"]
                )
                failure.append(deptopia_build["nvr"])

    logger.info(f"failures:{failure}")
    logger.info(f"different:{different}")
    logger.info(f"reconciliation: done product_stream: {stream_ofuri}.")
