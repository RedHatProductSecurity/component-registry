import pytest
from django.conf import settings

from corgi.collectors.app_interface import AppInterface

from .factories import ProductStreamFactory

pytestmark = pytest.mark.unit


@pytest.mark.django_db(databases=("default",))
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
                            "org": {"name": "blue-org", "instance": {"url": "quay.io"}},
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
                            "name": "hello",
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
            ]
        }
    )
    result = AppInterface.fetch_service_metadata(services=[ps])

    assert ps in result
    assert sorted(result[ps], key=lambda x: x["name"]) == sorted(
        [
            {"name": "hello-world", "quay_repo_name": "blue-org/hello-world"},
            {"name": "hello-world-api", "quay_repo_name": "blue-org/hello-world-api"},
            {"name": "hello", "git_repo_url": "https://github.com/red/hello"},
            {"name": "example", "quay_repo_name": "blue-org/example"},
            {"name": "example-ui", "quay_repo_name": "blue-org/example-ui"},
            {"name": "blue-app", "git_repo_url": "https://github.com/blue/example"},
        ],
        key=lambda x: x["name"],
    )
