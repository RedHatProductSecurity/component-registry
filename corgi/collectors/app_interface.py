import logging
from collections import defaultdict

import requests
from django.conf import settings

from corgi.core.models import ProductStream

logger = logging.getLogger(__name__)


class AppInterface:
    @classmethod
    def fetch_service_metadata(cls) -> dict[ProductStream, list[dict]]:
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
        # TODO: move this query to the periodic Celery task that will initial a refresh of
        #  manifests for all ProductStreams, fetch the manifests using Syft, and store them.
        services = ProductStream.objects.filter(meta_attr__managed_service_components__isnull=False)

        response = requests.post(
            f"{settings.APP_INTERFACE_URL}/graphql",
            json={"query": repo_query},
        )
        response.raise_for_status()
        data = response.json()["data"]["apps_v1"]

        service_component_map = {}
        for component in data:
            component_name = component["name"]
            subcomponent_data: defaultdict = defaultdict(dict)

            quay_repos_data = component.get("quayRepos")
            if quay_repos_data:
                for org_repos in quay_repos_data:
                    # Ignore container images in other registries since we assume all images are
                    # in Quay.io.
                    if org_repos["org"]["instance"]["url"] != "quay.io":
                        logger.info(
                            f"Found non-quay.io container images for {component_name} in "
                            f"app-interface: {org_repos['org']['instance']}"
                        )
                        continue
                    for repo in org_repos["items"]:
                        repo_name = f"{org_repos['org']['name']}/{repo['name']}"
                        subcomponent_data[repo["name"]]["quay_repo_name"] = repo_name

            source_repos_data = component.get("codeComponents")
            if source_repos_data:
                for source_repo in source_repos_data:
                    subcomponent_data[source_repo["name"]]["git_repo_url"] = source_repo["url"]

            # Convert data indexed by subcombonent name to list that contains component names and
            # their Git/Quay repo data. Set this as a list of subcomponents for the processed
            # component.
            subcomponent_list = [{"name": k, **v} for k, v in subcomponent_data.items()]
            service_component_map[component_name] = subcomponent_list

        service_metadata = defaultdict(list)
        for service in services:
            components = []
            for component in service.meta_attr["managed_service_components"]:
                app_interface_component = component.get("app_interface_name")
                if app_interface_component:
                    if app_interface_component not in service_component_map:
                        logger.error(
                            f"Prod-def component {component} for service {service.name} includes a "
                            "component name that does not exist in app-interface!"
                        )
                        continue
                    components.extend(service_component_map[app_interface_component])
                else:
                    components.append(component)

            service_metadata[service] = components

        return service_metadata
