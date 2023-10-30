import logging
from string import Template

from django.conf import settings
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class Pyxis:
    PYXIS_GRAPHQL_URL = settings.PYXIS_GRAPHQL_URL
    PYXIS_KEY_PAIR = (settings.PYXIS_CERT, settings.PYXIS_KEY)
    PYXIS_TIMEOUT = 10
    PYXIS_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}

    def __init__(self):
        self.session = Session()
        retries = Retry(
            total=10,
            backoff_factor=1.0,
            status_forcelist=(408, 500, 502, 503, 504),
            # Don't raise a MaxRetryError for codes in status_forcelist.
            # This allows for more graceful exception handling using
            # Response.raise_for_status.
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)

    MANIFEST_QUERY = """{
     get_content_manifest(id: "$manifest_id"){
      data {
       _id
       image {
        _id
        repositories {
         repository
         registry
         published
         manifest_list_digest
         manifest_schema2_digest
        }
       }
       incompleteness_reasons {
        type
        description
       }
       org_id
       creation_date
       edges {
        components(page: $page, page_size: $page_size) {
         data {
          name
                bom_ref
                supplier {
                  name
                  url
                  contact {
                    name
                    email
                  }
                }
                mime_type
                author
                publisher
                group
                version
                description
                scope
                hashes {
                  alg
                  content
                }
                licenses {
                    license {
                        id
                    }
                }
                copyright
                purl
                swid {
                    tag_id
                    name
                }
                external_references {
                    url
                    type
                    comment
                }
                release_notes {
                    type
                    title
                    description
                }
                build_dependency
                properties {
                    name
                    value
                }
                cpe
         }
        }
       }
      }
     }
    }
    """

    IMAGES_BY_NVR_QUERY = """{
      find_images_by_nvr(nvr: "$nvr", page: $page, page_size: $page_size) {
        error {
          detail
          status
        }
        page_size
        page

        data {
          _id
          architecture
          parsed_data {
            labels {
              name
              value
            }
          }
          repositories {
            image_advisory_id
            registry
            repository
            signatures{
              key_long_id
            }
            tags {
              name
            }
          }
        }

      }
    }"""

    def get_manifest_data(self, manifest_id: str, page_size: int = 50) -> dict:
        """Pull a manifest from pyxis"""

        logger.info(f"Retrieving manifest data for {manifest_id} from {self.PYXIS_GRAPHQL_URL}")

        has_more = True
        page = 0
        components: list[dict] = []
        manifest = {}
        while has_more:
            variables = {"manifest_id": manifest_id, "page": page, "page_size": page_size}
            data = self.do_post(variables, self.MANIFEST_QUERY)
            manifest = data["data"]["get_content_manifest"]["data"]

            components_batch = manifest["edges"]["components"]["data"] or ()
            components.extend(components_batch)
            # If there are fewer components on this page than the page size,
            # this page must be the last page
            has_more = len(components_batch) == page_size
            page += 1

        manifest["edges"]["components"]["data"] = components
        return manifest

    def get_image_by_nvr(self, nvr: str, page_size: int = 50) -> dict:
        """Get image data by build NVR"""

        logger.info(f"Retrieving image data for {nvr} from {self.PYXIS_GRAPHQL_URL}")

        variables = {"nvr": nvr, "page": 0, "page_size": page_size}
        data = self.do_post(variables, self.IMAGES_BY_NVR_QUERY)
        images = data["data"]["find_images_by_nvr"]["data"]
        # There is only 1 result for each arch per repository, so this should never be larger than
        # page_size, but if it is raise an error
        if len(images) == page_size:
            raise ValueError(
                f"Found more than 1 page of results for get_images_by_nvr with nvr: {nvr}"
            )
        return images

    def do_post(self, variables: dict, query: str) -> dict:
        body = {"query": Template(query).substitute(**variables)}
        response = self.session.post(
            self.PYXIS_GRAPHQL_URL,
            json=body,
            headers=self.PYXIS_HEADERS,
            cert=self.PYXIS_KEY_PAIR,
            timeout=self.PYXIS_TIMEOUT,
        )
        if not bool(response):
            logger.error(
                f"Failed request to {self.PYXIS_GRAPHQL_URL} with {response} had text body:"
                f" {response.text}"
            )
        response.raise_for_status()
        return response.json()
