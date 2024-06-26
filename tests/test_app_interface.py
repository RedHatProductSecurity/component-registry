from contextlib import nullcontext
from subprocess import CalledProcessError
from unittest.mock import call, patch

import pytest
from django.conf import settings

from corgi.collectors.app_interface import AppInterface
from corgi.collectors.syft import GitCloneError, Syft
from corgi.core.models import ProductStream
from corgi.tasks.managed_services import (
    MultiplePermissionExceptions,
    cpu_manifest_service_component,
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
    stream_names = {ps.name}
    service_names_and_components = ProductStream.objects.values_list(
        "name", "meta_attr__managed_service_components"
    )
    result = AppInterface.fetch_service_metadata(service_names_and_components)

    assert result == {
        "Yellow": {
            "name": "Yellow",
            "quay_repo_name": "yellow-org/yellow-image",
            "services": stream_names,
        },
        "blue-app": {
            "name": "blue-app",
            "git_repo_url": "https://github.com/blue/example",
            "services": stream_names,
        },
        "example": {
            "name": "example",
            "quay_repo_name": "blue-org/example",
            "services": stream_names,
        },
        "example-ui": {
            "name": "example-ui",
            "quay_repo_name": "blue-org/example-ui",
            "services": stream_names,
        },
        "hello-world": {
            "name": "hello-world",
            "quay_repo_name": "red-org/hello-world",
            "git_repo_url": "https://github.com/red/hello",
            "services": stream_names,
        },
        "hello-world-api": {
            "name": "hello-world-api",
            "quay_repo_name": "red-org/hello-world-api",
            "services": stream_names,
        },
    }


@pytest.mark.parametrize(
    "quay_json,most_recent_tag,target_host,target_image,pytest_raises",
    (
        (
            # Dict values aren't used and don't matter, we just need the first dict key
            # Which is always the newest / most recently updated tag name
            {"tags": {"t1": {"name": "t1", "date": 2}, "t2": {"name": "t2", "date": 1}}},
            "t1",
            "quay.io",
            "repo/image",
            nullcontext(),
        ),
        # Code works with public Quay.io instance or any internal Quay server
        ({"tags": {"t1": {}}}, "t1", "internal.quay", "repo2/image2", nullcontext()),
        # If no tags, default to latest. nullcontext here means "no exception raised"
        ({"tags": {}}, "latest", "quay.io", "repo3/image3", nullcontext()),
        # Fails with no retry if the image to pull already had a tag given explicitly
        (
            {"tags": {}},
            "latest",
            "internal.quay",
            "repo4/image4:latest",
            pytest.raises(CalledProcessError),
        ),
        (
            {"tags": {}},
            "latest",
            "internal.quay",
            "repo5/image5:version",
            pytest.raises(CalledProcessError),
        ),
        (
            {"tags": {}},
            "latest",
            "internal.quay",
            "repo6/image6@sha256:digest",
            pytest.raises(CalledProcessError),
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
    side_effects = (CalledProcessError(1, syft_args), syft_json)

    with patch(
        "corgi.collectors.syft.subprocess.check_output", side_effect=side_effects
    ) as mock_scan:
        with pytest_raises:
            # pytest_raises is either a nullcontext manager, if no exception raised
            # or a pytest.raises(CalledProcessError) context manager,
            # if we should fail and not retry
            result = Syft.scan_repo_image(target_image, target_host)

    failed_call = call(syft_args, text=True)
    if not isinstance(pytest_raises, nullcontext):
        # One failed call, no retry or call to Quay API
        calls = (failed_call,)
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
    ps = ProductStreamFactory.create(meta_attr={"managed_service_components": [{"name": "Blue"}]})
    stream_names = {ps.name}
    # Fails because some images do not define app_interface, Quay, or GitHub names
    for component in ps.meta_attr["managed_service_components"]:
        with pytest.raises(ValueError):
            cpu_manifest_service_component(stream_names, component)


@pytest.mark.django_db
def test_private_github_quay_repo_names():
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
    stream_names = [ps.name]
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
    exceptions = [
        MultiplePermissionExceptions((f"{type(podman_error)}: {podman_error.args}\n",)),
        MultiplePermissionExceptions(
            (
                f"{type(podman_error)}: {podman_error.args}\n",
                f"{type(git_error)}: {git_error.args}\n",
            )
        ),
        MultiplePermissionExceptions((f"{type(git_error)}: {git_error.args}\n",)),
    ]
    with patch("corgi.tasks.managed_services.Syft") as mock_syft:
        mock_syft.scan_repo_image.side_effect = (podman_error, podman_error)
        mock_syft.scan_git_repo.side_effect = (git_error, git_error)

        with patch("corgi.tasks.managed_services.slow_save_taxonomy") as mock_save_taxonomy:
            # Fails because all images above are not accessible
            for component in ps.meta_attr["managed_service_components"]:
                with pytest.raises(MultiplePermissionExceptions) as raised_e:
                    cpu_manifest_service_component(stream_names, component)
                assert str(raised_e.value) == str(exceptions.pop())
    # But only fails at the very end
    # we still try to scan other components, instead of stopping on the first error
    mock_syft.scan_repo_image.assert_has_calls(
        calls=(
            call(target_image="red-org/hello-world"),
            call(target_image="red-org/hello-world-api"),
        )
    )
    mock_syft.scan_git_repo.assert_has_calls(
        calls=(
            call(target_url="https://github.com/blue/example"),
            call(target_url="https://github.com/red/hello"),
        )
    )
    assert mock_save_taxonomy.apply_async.call_count == 3
