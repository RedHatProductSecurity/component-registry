# -----------------
# product reconciliation scripts
# -----------------
#
# Smoke tests are designed to be run against live environments
#
# >python3 scripts/reconciliation.py {HOST} --deptopia_url {deptopia url} --stream_corpus {ga10}
#
# if no host is provided defaults to localhost:8000
#
# Note- requires corgi-bindings:
# Note- requires corgi-bindings:

import argparse
import json
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
parser.add_argument("--stream_corpus", default="ga10")  # ga11 would select larger corpus
parser.add_argument("--deptopia_url")
parser.add_argument("corgi_url", default="http://localhost:8000")
args = parser.parse_args()

logger.info("smoke-test: start")

session = corgi_bindings.new_session(corgi_server_uri=args.corgi_url)

# check specific streams [corgi ofuri, deptopia id]
stream_ga10_corpus = [
    ["o:redhat:ansible_automation_platform:1.2", "2085"],  # GA 1.0
    ["o:redhat:ansible_automation_platform:2.0", "2030"],  # GA 1.0
    ["o:redhat:ansible_automation_platform:2.1", "2154"],  # GA 1.0
    ["o:redhat:ansible_automation_platform:2.2", "2340"],  # GA 1.0
    ["o:redhat:openshift:4.8.z", "103"],  # GA 1.0
    ["o:redhat:openshift:4.9.z", "100"],  # GA 1.0
    ["o:redhat:openshift:4.10.z", "2305"],  # GA 1.0
    ["o:redhat:openshift:4.11.z", "2367"],  # GA 1.0
    ["o:redhat:rhacm:2.3.z", "104"],  # GA 1.0
    ["o:redhat:rhacm:2.4.z", "1444"],  # GA 1.0
    ["o:redhat:rhacm:2.5.z", "2397"],  # GA 1.0
    ["o:redhat:rhacm:2.6.z", "2394"],  # GA 1.0
    ["o:redhat:rhacm:2.7", "2395"],  # GA 1.0
    ["o:redhat:rhel:8.4.0.z", "35"],  # GA 1.0
    ["o:redhat:rhel:8.6.0.z", "2302"],  # GA 1.0
    ["o:redhat:rhel:8.7.0", "2301"],  # GA 1.0
    ["o:redhat:rhel:9.0.0.z", "2307"],  # GA 1.0
    ["o:redhat:rhel:9.1.0", "2308"],  # GA 1.0
    ["o:redhat:rhn_satellite:6.7", "93"],  # GA 1.0
    ["o:redhat:rhn_satellite:6.8", "50"],  # GA 1.0
    ["o:redhat:rhn_satellite:6.9", "72"],  # GA 1.0
]

stream_ga11_corpus = [
    ["o:redhat:ansible_automation_platform:1.2", "2085"],  # GA 1.0
    ["o:redhat:ansible_automation_platform:2.0", "2030"],  # GA 1.0
    ["o:redhat:ansible_automation_platform:2.1", "2154"],  # GA 1.0
    ["o:redhat:ansible_automation_platform:2.2", "2340"],  # GA 1.0
    # ["o:redhat:ceph-2-default:", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:ceph:3", "27"],  # GA 1.1
    ["o:redhat:ceph:4", "42"],  # GA 1.1
    ["o:redhat:ceph:5", "109"],  # GA 1.1
    ["o:redhat:cfme:5.11", "23"],  # GA 1.1
    # ["o:redhat:cnv:2.4", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:cnv:2.5", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:cnv:2.6", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:cnv:4.10", "137"],  # GA 1.1
    # ["o:redhat:cnv:4.11", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:cnv:4.8", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:cnv:4.9, 0", "48"],  # GA 1.1
    ["o:redhat:dts:10.1.z", "134"],  # GA 1.1
    # ["o:redhat:dts:11.0", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:dts:11.0.z", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:dts:11.1.z", "1670"],  # GA 1.1
    ["o:redhat:dts:12.0", "2334"],  # GA 1.1
    # ["o:redhat:eap:6.4.2", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:eap:6.4.23", "106"],  # GA 1.1
    ["o:redhat:eap:7.4.z", "46"],  # GA 1.1
    # ["o:redhat:fdp-el:7", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:fdp-el7-ovs:", "1437"],  # GA 1.1
    ["o:redhat:fdp-el8-ovs:", "1438"],  # GA 1.1
    # ["o:redhat:fdp-el9",], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:jws:3.1", "98"],  # GA 1.1
    # ["o:redhat:jws:5.0", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:ocp-tools:4", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:ocp-tools:4.6", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:ocp-tools:4.7", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:ocp-tools:4.8", "141"],  # GA 1.1
    # ["o:redhat:ocp-tools:4.9", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:ocp-tools:4.10", "2325"],  # GA 1.1
    # ["o:redhat:openshift:4.10", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openshift:4.10.z", "2305"],  # GA 1.0
    # ["o:redhat:openshift:4.11", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openshift:4.11.z", "2367"],  # GA 1.0
    # ["o:redhat:openshift:4.4", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:openshift:4.4.z", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:openshift:4.5", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:openshift:4.5.z", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:openshift:4.6.z", "85"], add stream to prod def
    # ["o:redhat:openshift:4.7", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:openshift:4.8", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openshift:4.8.z", "103"],  # GA 1.0
    # ["o:redhat:openshift:4.9", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openshift:4.9.z", "100"],  # GA 1.0
    # need openshift 5 ...
    # ["o:redhat:openshift-container-storage:4", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:openshift-container-storage:4.6", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openshift-container-storage:4.6.z", "1819"],  # GA 1.1
    # ["o:redhat:openshift-container-storage:4.7", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openshift-container-storage:4.7.z", "92"],  # GA 1.1
    # ["o:redhat:openshift-container-storage:4.8", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openshift-container-storage:4.8.z", "112"],  # GA 1.1
    # ["o:redhat:openshift-data-foundation:4.10", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openshift-data-foundation:4.10.z", "2331"],  # GA 1.1
    # ["o:redhat:openshift-data-foundation:4.9", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openshift-data-foundation:4.9.z", "2102"],  # GA 1.1
    # ["o:redhat:openstack:13", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openstack-13-els:", "1914"],  # GA 1.1
    # ["o:redhat:openstack:16", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:openstack:16.1", "53"],  # GA 1.1
    ["o:redhat:openstack:16.2", "63"],  # GA 1.1
    # ["o:redhat:openstack:17.0", "2214"], add product stream to prod def ?
    ["o:redhat:rhacm:2.3.z", "104"],  # GA 1.0
    ["o:redhat:rhacm:2.4.z", "1444"],  # GA 1.0
    ["o:redhat:rhacm:2.5", "1443"],  # GA 1.0
    ["o:redhat:rhel-6-els:", "120"],  # GA 1.1
    # ["o:redhat:rhel:7.3", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel:7.3.z", "116"],  # GA 1.1
    # ["o:redhat:rhel:7.4", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel:7.4.z", "121"],  # GA 1.1
    # ["o:redhat:rhel:7.6", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel:7.6.z", "119"],  # GA 1.1
    # ["o:redhat:rhel:7.7", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel:7.7.z", "114"],  # GA 1.1
    # ["o:redhat:rhel:7.9", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel:7.9.z", "2"],  # GA 1.1
    # ["o:redhat:rhel:8.1.0", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel:8.1.0.z", "14"],  # GA 1.1
    # ["o:redhat:rhel:8.2.0", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel:8.2.0.z", "26"],  # GA 1.1
    # ["o:redhat:rhel:8.4.0", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel:8.4.0.z", "35"],  # GA 1.0
    # ["o:redhat:rhel:8.5.0", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:rhel:8.5.0.z", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:rhel:8.6.0", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel:8.6.0.z", "2302"],  # GA 1.0
    ["o:redhat:rhel:8.7.0", "2301"],  # GA 1.0
    # ["o:redhat:rhel:9", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:rhel:9.0", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel:9.0.0.z", "2307"],  # GA 1.0
    ["o:redhat:rhel:9.1.0", "2308"],  # GA 1.0
    # ["o:redhat:rhel-av:8.2.1", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel-av:8.2.1.z", "1739"],  # GA 1.1
    # ["o:redhat:rhel-av:8.4.0", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel-av:8.4.0.z", "5"],  # GA 1.1
    # ["o:redhat:rhel-av:8.5.0", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhel-av:8.5.0.z", "1439"],  # GA 1.1
    ["o:redhat:rhel-av:8.6.0", "1440"],  # GA 1.1
    # ["o:redhat:rhel-br:8", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:rhel-br:8.1.0", ], # DOES NOT EXIST IN DEPTOPIA
    [
        "o:redhat:rhel-br:8.1.0.z",
    ],  # GA 1.1
    # ["o:redhat:rhel-br:8.2.0", ], # DOES NOT EXIST IN DEPTOPIA
    [
        "o:redhat:rhel-br:8.2.0.z",
    ],  # GA 1.1
    # ["o:redhat:rhel-br:8.4.0", ], # DOES NOT EXIST IN DEPTOPIA
    [
        "o:redhat:rhel-br:8.4.0.z",
    ],  # GA 1.1
    # ["o:redhat:rhel-br:8.5.0", ], # DOES NOT EXIST IN DEPTOPIA
    [
        "o:redhat:rhel-br:8.5.0.z",
    ],  # GA 1.1
    # ["o:redhat:rhel-br:8.6.0", ], # DOES NOT EXIST IN DEPTOPIA
    [
        "o:redhat:rhel-br:8.6.0.z",
    ],  # GA 1.1
    # ["o:redhat:rhel-br:8.7.0", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:rhel-br:9", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:rhel-br:9.0", ], # DOES NOT EXIST IN DEPTOPIA
    [
        "o:redhat:rhel-br:9.0.0.z",
    ],  # GA 1.1
    # ["o:redhat:rhel-br:9.1.0", ], # DOES NOT EXIST IN DEPTOPIA
    # ["o:redhat:rhes:3.5", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhev-m:4.3.z", "123"],  # GA 1.1
    ["o:redhat:rhev-m:4.4.z", "124"],  # GA 1.1
    # ["o:redhat:rhn_satellite:6", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhn_satellite:6.7", "93"],  # GA 1.0
    ["o:redhat:rhn_satellite:6.8", "50"],  # GA 1.0
    ["o:redhat:rhn_satellite:6.9", "72"],  # GA 1.0
    ["o:redhat:rhscl:3.8.z", "152"],  # GA 1.1
    # ["o:redhat:rhscl:3.9", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:rhui:3.0", "19"],  # GA 1.1
    # ["o:redhat:rhui:4.0", ], add product stream to prod def ??
    # ["o:redhat:stf:1.2", ], # DOES NOT EXIST IN DEPTOPIA
    ["o:redhat:stf:1.3", "4"],  # GA 1.1
    # ["o:redhat:stf:1.4", ], add product stream to prod def ??
]

# check if stream exists in product stream
stream_corpus = stream_ga10_corpus
if args.stream_corpus == "ga11":
    stream_corpus = stream_ga11_corpus

for stream_ofuri, deptopia_id in stream_corpus:
    logger.info(f"reconciliation: check product_stream: {stream_ofuri}.")

    response = requests.get(f"{args.deptopia_url}/api/v1/products/id/{deptopia_id}/builds")
    response.raise_for_status()
    deptopia_builds = json.loads(response.text)

    logger.info(f"deptopia ps_update_stream {deptopia_builds['ps_update_stream']}")

    success = []
    failure = []
    different = []
    cnt = 0

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
                    str(check_version.count),
                )
                different.append(deptopia_build["nvr"])
            else:
                logger.warning(
                    "FAILURE: %s", deptopia_build["nvr"] + "," + deptopia_build["build_type"]
                )
                failure.append(deptopia_build["nvr"])
        cnt += 1

    logger.info(f"# success:{len(success)}")
    logger.info(f"# failures:{len(failure)}")
    logger.info(f"# different:{len(different)}")
    logger.info(f"# total:{cnt}")
    logger.info(f"# coverage:{len(success)/cnt}")
    logger.info(f"reconciliation: done product_stream: {stream_ofuri}.")
