import logging
from collections import defaultdict

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class AppInterface:
    @classmethod
    def fetch_service_metadata(
        cls, services: tuple[tuple[str, list[dict[str, str | set[str]]]], ...]
    ) -> dict[str, dict[str, str | set[str]]]:
        """Fetch Quay images and Git repos for all components in App-Interface,
        and return only components used by a managed-service in product-definitions
        deduplicated so that we only analyze each component once, even if used by many services"""
        # TODO: Check parentApp / childrenApps / dependencies / statusPageComponents as well?
        repo_query = """
        {
            apps_v1 {
                path
                name
                quayRepos {
                    org {
                        name
                        instance {
                            url
                        }
                    }
                    items {
                        name
                        public
                        description
                    }
                }
                codeComponents {
                    name
                    url
                    resource
                }
            }
        }
        """
        response = requests.post(
            f"{settings.APP_INTERFACE_URL}/graphql",
            auth=(settings.APP_INTERFACE_USERNAME, settings.APP_INTERFACE_PASSWORD),
            json={"query": repo_query},
        )
        response.raise_for_status()
        data: list[dict] = response.json()["data"]["apps_v1"]

        service_component_map = {}
        for component in data:
            component_name: str = component["name"]
            subcomponent_data: defaultdict[str, dict[str, str]] = defaultdict(dict[str, str])

            # Handle case when key is present but value is None
            quay_repos_data: tuple[dict, ...] = component.get("quayRepos", ()) or ()
            for org_repos in quay_repos_data:
                # Ignore container images in other registries since we assume all images are
                # in Quay.io.
                if org_repos["org"]["instance"]["url"] != "quay.io":
                    raise ValueError(
                        f"Found non-quay.io container images for {component_name} in "
                        f"app-interface: {org_repos['org']['instance']}"
                    )
                for repo in org_repos["items"]:
                    repo_name = f"{org_repos['org']['name']}/{repo['name']}"
                    subcomponent_data[repo["name"]]["quay_repo_name"] = repo_name

            # Handle case when key is present but value is None
            source_repos_data = component.get("codeComponents") or ()
            for source_repo in source_repos_data:
                subcomponent_data[source_repo["name"]]["git_repo_url"] = source_repo["url"]

            # Convert data indexed by subcomponent name to list that contains component names and
            # their Git/Quay repo data. Set this as a list of subcomponents for the processed
            # component.
            subcomponent_list = [{"name": k, **v} for k, v in subcomponent_data.items()]
            service_component_map[component_name] = subcomponent_list

        return cls.deduplicate_service_components(services, service_component_map)

    @classmethod
    def deduplicate_service_components(
        cls,
        services: tuple[tuple[str, list[dict[str, str | set[str]]]], ...],
        service_component_map: dict[str, list[dict]],
    ) -> dict[str, dict[str, str | set[str]]]:
        """After fetching all components from App-Interface, deduplicate and return
        only components used by a managed service in product-definitions"""
        # Different services can reuse the same component
        # but we only want to analyze each component once
        # then link its dependencies to all the services it belongs to

        service_metadata: dict[str, dict[str, str | set[str]]] = {}
        for service_name, service_components in services:
            for component in service_components:
                app_interface_name: str = component.get(  # type: ignore[assignment]
                    "app_interface_name", ""
                )
                component_name: str = component["name"]  # type: ignore[assignment]

                # If a component refers to app-interface data,
                # look up the related components from the mapping we built earlier
                if app_interface_name:
                    service_metadata = cls.load_app_interface_components(
                        service_metadata,
                        service_component_map,
                        service_name,
                        app_interface_name,
                        component,
                    )
                    continue

                # Else this prod-defs component hardcodes its Git repos and Quay images
                # Just add the service name, if the component already exists
                existing_component = service_metadata.get(component_name)
                if existing_component:
                    service_names: set[str] = existing_component[  # type: ignore[assignment]
                        "services"
                    ]
                    logger.info(
                        f"Prod-defs component {component} for service {service_name} is reused "
                        f"by other services: {service_names}"
                    )

                    existing_git_repo = existing_component.get("git_repo_url", "")
                    new_git_repo = component.get("git_repo_url", "")
                    if new_git_repo and new_git_repo != existing_git_repo:
                        logger.error(
                            f"Prod-defs component {component} for service {service_name} "
                            f"includes a different Git repo "
                            f"than the existing component: {existing_component}"
                        )
                        existing_component["git_repo_url"] = new_git_repo

                    existing_quay_repo = existing_component.get("quay_repo_name", "")
                    new_quay_repo = component.get("quay_repo_name", "")
                    if new_quay_repo and new_quay_repo != existing_quay_repo:
                        logger.error(
                            f"Prod-defs component {component} for service {service_name} "
                            f"includes a different Quay repo "
                            f"than the existing component: {existing_component}"
                        )
                        existing_component["quay_repo_name"] = new_quay_repo

                    service_names.add(service_name)
                    continue

                # Else this is the first time we're adding it
                component["services"] = {service_name}
                service_metadata[component_name] = component
        return service_metadata

    @staticmethod
    def load_app_interface_components(
        service_metadata: dict[str, dict[str, str | set[str]]],
        service_component_map: dict[str, list[dict]],
        service_name: str,
        app_interface_name: str,
        component: dict[str, str | set[str]],
    ) -> dict[str, dict[str, str | set[str]]]:
        """Add all app-interface components for a given name to the service metadata,
        after sanity-checking the data just in case"""
        # Log some edge-cases just in case
        if app_interface_name != component.get("name", ""):
            logger.error(
                f"Prod-defs component {component} for service {service_name} "
                "has a mismatched app-interface name!"
            )
        if "quay_repo_name" in component or "git_repo_url" in component:
            logger.error(
                f"Prod-defs component {component} for service {service_name} "
                f"includes extra data that will be ignored!"
            )

        app_interface_components = service_component_map.get(app_interface_name, ())
        if not app_interface_components:
            # TODO: Raise an error once prod-defs data is cleaned up
            logger.error(
                f"Prod-defs component {component} for service {service_name} "
                f"includes a component name that does not exist in app-interface!"
            )
            return service_metadata

        for app_interface_component in app_interface_components:
            # Just add the service name, if the component already exists
            existing_component = service_metadata.get(app_interface_component["name"])
            if existing_component:
                service_names: set[str] = existing_component["services"]  # type: ignore[assignment]
                logger.info(
                    f"App-Interface component {app_interface_component} for service {service_name} "
                    f"is reused by other services: {service_names}"
                )

                existing_git_repo = existing_component.get("git_repo_url", "")
                new_git_repo = app_interface_component.get("git_repo_url", "")
                if new_git_repo and new_git_repo != existing_git_repo:
                    logger.error(
                        f"App-Interface component {app_interface_component} for service "
                        f"{service_name} includes a different Git repo "
                        f"than the existing component: {existing_component}"
                    )
                    existing_component["git_repo_url"] = new_git_repo

                existing_quay_repo = existing_component.get("quay_repo_name", "")
                new_quay_repo = app_interface_component.get("quay_repo_name", "")
                if new_quay_repo and new_quay_repo != existing_quay_repo:
                    logger.error(
                        f"App-Interface component {app_interface_component} for service "
                        f"{service_name} includes a different Quay repo "
                        f"than the existing component: {existing_component}"
                    )
                    existing_component["quay_repo_name"] = new_quay_repo

                service_names.add(service_name)
                continue

            # Else this is the first time we're adding it
            app_interface_component["services"] = {service_name}
            service_metadata[app_interface_component["name"]] = app_interface_component
        return service_metadata
