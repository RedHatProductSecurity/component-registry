import subprocess
from contextlib import nullcontext
from subprocess import CalledProcessError
from unittest.mock import call, patch

import pytest
from django.conf import settings

from corgi.collectors.app_interface import AppInterface
from corgi.collectors.syft import GitCloneError, QuayImagePullError, Syft
from corgi.tasks.managed_services import (
    MultiplePermissionExceptions,
    cpu_manifest_service,
)

from .factories import ProductStreamFactory

pytestmark = pytest.mark.unit


@pytest.mark.django_db
def test_metadata_fetch(requests_mock):
    example_response = {
        "data": {
            "apps_v1": [
                {
                    "path": "/services/blue/app.yml",
                    "name": "Blue",
                    "quayRepos": [
                        {
                            "org": {"name": "blue-org", "instance": {"url": "quay.io"}},
                            "items": [
                                {
                                    "name": "example",
                                    "public": True,
                                    "description": "Some tracking tool",
                                },
                                {
                                    "name": "example-ui",
                                    "public": True,
                                    "description": "Some tracking tool UI",
                                },
                            ],
                        }
                    ],
                    "codeComponents": [
                        {
                            "name": "blue-app",
                            "url": "https://github.com/blue/example",
                            "resource": "upstream",
                        }
                    ],
                },
                {
                    "path": "/services/red/app.yml",
                    "name": "Red",
                    "quayRepos": [
                        {
                            "org": {"name": "red-org", "instance": {"url": "quay.io"}},
                            "items": [
                                {
                                    "name": "hello-world",
                                    "public": True,
                                    "description": "Hello tool",
                                },
                                {
                                    "name": "hello-world-api",
                                    "public": True,
                                    "description": "Hello tool API",
                                },
                            ],
                        }
                    ],
                    "codeComponents": [
                        {
                            "name": "hello-world",
                            "url": "https://github.com/red/hello",
                            "resource": "upstream",
                        }
                    ],
                },
            ]
        }
    }

    requests_mock.post(f"{settings.APP_INTERFACE_URL}/graphql", json=example_response)
    ps = ProductStreamFactory(
        meta_attr={
            "managed_service_components": [
                {"name": "Red", "app_interface_name": "Red"},
                {"name": "Blue", "app_interface_name": "Blue"},
                # This component defines a Quay repo directly and isn't in App Interface
                # But should still end up in our results after we parse the list
                {"name": "Yellow", "quay_repo_name": "yellow-org/yellow-image"},
                # This one isn't in App Interface either, but doesn't have a quay_repo_name
                # It should not end up in the results. In the future we can raise an error
                {"name": "Green", "app_interface_name": "GreenDoesNotExist"},
            ]
        }
    )
    result = AppInterface.fetch_service_metadata(services=[ps])

    assert ps in result
    assert sorted(result[ps], key=lambda x: x["name"]) == [
        {"name": "Yellow", "quay_repo_name": "yellow-org/yellow-image"},
        {"name": "blue-app", "git_repo_url": "https://github.com/blue/example"},
        {"name": "example", "quay_repo_name": "blue-org/example"},
        {"name": "example-ui", "quay_repo_name": "blue-org/example-ui"},
        {
            "name": "hello-world",
            "quay_repo_name": "red-org/hello-world",
            "git_repo_url": "https://github.com/red/hello",
        },
        {"name": "hello-world-api", "quay_repo_name": "red-org/hello-world-api"},
    ]


@pytest.mark.parametrize(
    "quay_json,most_recent_tag,target_host,target_image,pytest_raises",
    (
        (
            # Dict values aren't used and don't matter, we just need the first dict key
            # Which is always the newest / most recently updated tag name
            # nullcontext here means "no exception raised"
            {"tags": {"t1": {"name": "t1", "date": 2}, "t2": {"name": "t2", "date": 1}}},
            "t1",
            "quay.io",
            "repo/image",
            nullcontext(),
        ),
        # Code works with public Quay.io instance or any internal Quay server
        ({"tags": {"t1": {}}}, "t1", "internal.quay", "repo2/image2", nullcontext()),
        # If no tags, raise a QuayImagePullError
        ({"tags": {}}, "latest", "quay.io", "repo3/image3", pytest.raises(QuayImagePullError)),
        # Fails with no retry if the image to pull already had a tag given explicitly
        (
            {"tags": {}},
            "latest",
            "internal.quay",
            "repo4/image4:latest",
            pytest.raises(subprocess.CalledProcessError),
        ),
        (
            {"tags": {}},
            "latest",
            "internal.quay",
            "repo5/image5:version",
            pytest.raises(subprocess.CalledProcessError),
        ),
        (
            {"tags": {}},
            "latest",
            "internal.quay",
            "repo6/image6@sha256:digest",
            pytest.raises(subprocess.CalledProcessError),
        ),
    ),
)
def test_image_pull_error_handling(
    requests_mock, quay_json, most_recent_tag, target_host, target_image, pytest_raises
):
    """Test that Quay.io images can be pulled even if known errors occur"""
    requests_mock.get(
        f"https://{target_host}/api/v1/repository/{target_image}?includeTags=true", json=quay_json
    )

    syft_args = [
        "/usr/bin/syft",
        "packages",
        "-q",
        "-o=syft-json",
        "--exclude=**/vendor/**",
        f"registry:{target_host}/{target_image}",
    ]
    syft_json = '{"source": "source_not_used"}'
    side_effects = (subprocess.CalledProcessError(1, syft_args), syft_json)

    with patch(
        "corgi.collectors.syft.subprocess.check_output", side_effect=side_effects
    ) as mock_scan:
        with pytest_raises:
            # pytest_raises is either a nullcontext manager, if no exception raised
            # or a pytest.raises context manager, if we should fail and not retry
            result = Syft.scan_repo_image(target_image, target_host)

    failed_call = call(syft_args, text=True)
    if not isinstance(pytest_raises, nullcontext):
        # One failed call, no retry
        calls = (failed_call,)
        if pytest_raises.expected_exception == QuayImagePullError:
            # One failed call was made to the Quay API
            # because the image pull for an implicit "latest" tag failed
            # and we wanted to find the other tags, but there were none
            # We ignore this error since there are no tags / versions to analyze anyway
            assert requests_mock.call_count == 1
        else:
            # No failed call was made to the Quay API
            # because the image pull was for an explicitly-given tag
            # so we just stop here and don't try to find the other tags
            assert requests_mock.call_count == 0
    else:
        # One failed call, one successful retry that uses the Quay API
        syft_args[-1] = f"{syft_args[-1]}:{most_recent_tag}"
        retried_call = call(syft_args, text=True)
        calls = (failed_call, retried_call)
        assert requests_mock.call_count == 1

        # The result is an empty list of components, plus the "source" key from the Syft JSON
        # All we care about in this test is that we can pull the image and no exception is raised
        assert result == ([], "source_not_used")

    mock_scan.assert_has_calls(calls)


@pytest.mark.django_db
def test_metadata_error_handling_for_non_quay_images(requests_mock):
    """Test that images for non-Quay repos raise an error"""
    example_response = {
        "data": {
            "apps_v1": [
                {
                    "path": "/services/blue/app.yml",
                    "name": "Blue",
                    "quayRepos": [
                        {
                            "org": {"name": "blue-org", "instance": {"url": "hub.docker.com"}},
                            "items": [
                                {
                                    "name": "example",
                                    "public": True,
                                    "description": "Some tracking tool",
                                },
                                {
                                    "name": "example-ui",
                                    "public": True,
                                    "description": "Some tracking tool UI",
                                },
                            ],
                        }
                    ],
                },
            ]
        }
    }

    requests_mock.post(f"{settings.APP_INTERFACE_URL}/graphql", json=example_response)
    ps = ProductStreamFactory(
        meta_attr={"managed_service_components": [{"name": "Blue", "app_interface_name": "Blue"}]}
    )
    # Fails because some images do not use Quay
    with pytest.raises(ValueError):
        AppInterface.fetch_service_metadata(services=[ps])
    assert requests_mock.call_count == 1


@pytest.mark.django_db
def test_missing_github_quay_repo_names():
    """Test that components without either GitHub or Quay repo names raise an error"""
    ps = ProductStreamFactory(meta_attr={"managed_service_components": [{"name": "Blue"}]})
    # Fails because some images do not define app_interface, Quay, or GitHub names
    with pytest.raises(ValueError):
        cpu_manifest_service(ps.name, ps.meta_attr["managed_service_components"])


@pytest.mark.django_db
def test_private_github_quay_repo_names(requests_mock):
    """Test that private images / repos we don't have permissions for are skipped"""
    # We should at least attempt to scan all components for a service
    # Known permission errors should not block us from checking the remaining components
    # but we should still raise an error at the end of the task, for tracking
    ps = ProductStreamFactory(
        meta_attr={
            "managed_service_components": [
                {"name": "blue-app", "git_repo_url": "https://github.com/blue/example"},
                {
                    "name": "hello-world",
                    "quay_repo_name": "red-org/hello-world",
                    "git_repo_url": "https://github.com/red/hello",
                },
                {"name": "hello-world-api", "quay_repo_name": "red-org/hello-world-api"},
            ]
        }
    )
    git_error = GitCloneError(
        "git clone of {target_url} failed with: {result.stderr.decode('utf-8')}"
    )
    podman_error = CalledProcessError(
        128,
        (
            "/usr/bin/syft",
            "packages",
            "-q",
            "-o=syft-json",
            "--exclude=**/vendor/**",
            "registry:{target_host}/{target_image}",
        ),
    )
    # No easy way to get the mock Response out of the mocker?
    # So we just hardcode the error message below
    # instead of making the request in the test, getting the response
    # then raising and storing the error, to compare with the real error raised in code later
    # and finally resetting the mock to do all that again in the real code
    requests_mock.get(
        "https://quay.io/api/v1/repository/red-org/hello-world?includeTags=true",
        reason="FORBIDDEN",
        status_code=403,
    )
    requests_mock.get(
        "https://quay.io/api/v1/repository/red-org/hello-world-api?includeTags=true",
        reason="FORBIDDEN",
        status_code=403,
    )

    with patch(
        "corgi.tasks.managed_services.Syft.scan_git_repo", side_effect=(git_error, git_error)
    ) as mock_git:
        with patch(
            "corgi.collectors.syft.subprocess.check_output",
            side_effect=(podman_error, podman_error),
        ) as mock_subprocess:
            # Git clone and Syft both fail because all images above are not accessible
            # For Quay images, we call subprocess to pull the image's implicit "latest" tag
            # #hen this fails, we call requests / use the Quay API to list the tags
            # We should try to pull a different tag if no "latest" tag exists
            # But we'll fail due to 403 / no permissions to access this image (or its tags)
            with pytest.raises(MultiplePermissionExceptions) as raised_e:
                cpu_manifest_service(ps.name, ps.meta_attr["managed_service_components"])
            assert str(raised_e.value) == (
                f"Multiple exceptions raised:\n\n"
                f"{type(git_error)}: {git_error.args}\n\n"
                "<class 'requests.exceptions.HTTPError'>: "
                "('403 Client Error: FORBIDDEN for url: "
                "https://quay.io/api/v1/repository/red-org/hello-world?includeTags=true',)\n\n"
                f"{type(git_error)}: {git_error.args}\n\n"
                "<class 'requests.exceptions.HTTPError'>: "
                "('403 Client Error: FORBIDDEN for url: "
                "https://quay.io/api/v1/repository/red-org/hello-world-api?includeTags=true',)\n"
            )
    # But only fails at the very end
    # we still try to scan other components, instead of stopping on the first error
    assert requests_mock.call_count == 2
    assert mock_git.call_count == 2
    assert mock_subprocess.call_count == 2
