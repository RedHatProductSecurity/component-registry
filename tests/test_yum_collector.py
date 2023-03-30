from unittest.mock import call, patch

import pytest

from corgi.collectors.yum import DNF_BASE_COMMAND, Yum
from corgi.tasks.common import BUILD_TYPE
from tests.test_pulp_collector import TEST_REPO

pytestmark = pytest.mark.unit

FIND_MODULES_OUTPUT = [
    "Red Hat Enterprise Linux 8 for aarch64 - AppStream (RPMs)",
    "Name           Stream          Profiles              Summary",
    "python27       2.7 [d][e]      common [d]            Python programming language, version 2.7",
    "python36       3.6 [d][e]      build, common [d]     Python programming language, version 3.6",
    "",
    "rhel8-latest-appstream",
    "Name           Stream          Profiles              Summary",
    "python27       2.7 [d][e]      common [d]            Python programming language, version 2.7",
    "python36       3.6 [d][e]      build, common [d]     Python programming language, version 3.6",
    "",
    "Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled",
    "",
]

GET_MODULE_OUTPUT = [
    "Name             : python27",
    "Stream           : 2.7 [d][e][a]",
    "Version          : 820190212161047",
    "Context          : 43711c95",
    "Architecture     : aarch64",
    "Profiles         : common [d]",
    "Default profiles : common",
    "Repo             : rhel-8-latest-appstream",
    "Summary          : Python programming language, version 2.7",
    "Description      : This module provides the Python 2.7 interpreter and additional Python",
    "                 : packages the users might need.",
    "Requires         : platform:[el8]",
    "Artifacts        : python-nose-docs-0:1.3.7-29.module+el8+2540+b19c9b35.noarch",
    "                 : python2-0:2.7.15-21.module+el8+2540+b19c9b35.aarch64",
    "                 : python2-idna-0:2.5-6.module+el8+2540+b19c9b35.noarch",
    "                 : python2-py-0:1.5.3-5.module+el8+2540+b19c9b35.noarch",
    "",
    "Name             : python36",
    "Stream           : 3.6 [d][e][a]",
    "Version          : 820190123171828",
    "Context          : 17efdbc7",
    "Architecture     : aarch64",
    "Profiles         : build, common [d]",
    "Default profiles : common",
    "Repo             : rhel-8-for-aarch64-appstream-rpms",
    "Summary          : Python programming language, version 3.6",
    "Description      : This module gives users access to the internal Python 3.6 in RHEL8, as",
    "                 : well as provides some additional Python packages the users might need.",
    "                 : In addition to these you can install any python3-* package available",
    "                 : in RHEL and use it with Python from this module.",
    "Requires         : platform:[el8]",
    "Artifacts        : python-nose-docs-0:1.3.7-29.module+el8+2339+1a6691f8.noarch",
    "                 : python3-docs-0:3.6.7-1.module+el8+2339+1a6691f8.noarch",
    "                 : python36-0:3.6.8-1.module+el8+2710+846623d6.aarch64",
    "",
    "Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled, [a]ctive",
]

GET_NEVRAS_OUTPUT = [
    "(none) telegram-desktop:0-2.4.4-1.el7.src",
    "python3-3.6.8-47.el8_6.src.rpm platform-python:0-3.6.8-47.el8_6.x86_64",
]


def test_get_modules_from_yum_repos():
    """Test that the Yum repo collector can get a list of module build IDs
    for all modules shipped to a particular repo"""
    mock_find_modules_call = call(
        DNF_BASE_COMMAND + ["module", "list", f"--repofrompath=1,{TEST_REPO}", "--repoid=1"]
    )
    mock_get_module_call = call(
        DNF_BASE_COMMAND
        + [
            "module",
            "info",
            f"--repofrompath=1,{TEST_REPO}",
            "--repoid=1",
            "python27",
            "python36",
        ]
    )

    # Python2.7 and 3.6
    module_build_ids = (834215, 844493)

    with patch(
        "corgi.collectors.yum.run_external",
        side_effect=((None, FIND_MODULES_OUTPUT), (None, GET_MODULE_OUTPUT)),
    ) as mock_runner:
        with patch(
            "corgi.collectors.brew.Brew.persist_modules", return_value=module_build_ids
        ) as mock_saver:
            result_build_ids = Yum(BUILD_TYPE).get_modules_from_yum_repos((TEST_REPO,))
            mock_runner.assert_has_calls(
                calls=(mock_find_modules_call, mock_get_module_call), any_order=False
            )
            mock_saver.assert_called_once()
    assert result_build_ids == module_build_ids


def test_get_srpms_from_yum_repos():
    """Test that the Yum repo collector can get a list of SRPM build IDs
    for all RPMs shipped to a particular repo"""
    srpm_build_ids = (2050029,)
    with patch(
        "corgi.collectors.yum.run_external", return_value=(None, GET_NEVRAS_OUTPUT)
    ) as mock_runner:
        with patch(
            "corgi.collectors.brew.Brew.lookup_build_ids", return_value=srpm_build_ids
        ) as mock_lookup:
            result_build_ids = Yum(BUILD_TYPE).get_srpms_from_yum_repos(TEST_REPO)
            mock_runner.assert_called_once()
            mock_lookup.assert_called_once()
    assert result_build_ids == srpm_build_ids
