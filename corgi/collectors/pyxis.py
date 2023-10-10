import logging
from collections.abc import Mapping
from string import Template
from typing import Any

import requests
import urllib3.util.retry
from django.conf import settings

logger = logging.getLogger(__name__)

session = requests.Session()
retries = urllib3.util.retry.Retry(
    total=10,
    backoff_factor=1.0,
    status_forcelist=(408, 500, 502, 503, 504),
    # Don't raise a MaxRetryError for codes in status_forcelist.
    # This allows for more graceful exception handling using
    # Response.raise_for_status.
    raise_on_status=False,
)
adapter = requests.adapters.HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)


query = """{
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


def get_manifest_data(manifest_id: str, page_size: int = 50) -> Mapping[str, Any]:
    """Pull a manifest from pyxis"""

    url = settings.PYXIS_GRAPHQL_URL

    if not url:
        raise ValueError("Set CORGI_PYXIS_GRAPHQL_URL to get manifests from pyxis")

    if not settings.PYXIS_CERT or not settings.PYXIS_KEY:
        raise ValueError("Set CORGI_PYXIS_CERT and CORGI_PYXIS_KEY to get manifests from pyxis")
    cert = (settings.PYXIS_CERT, settings.PYXIS_KEY)

    logger.info(f"Retrieving manifest data for {manifest_id} from {url}")

    timeout = 10
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    has_more = True
    page = 0
    components: list[dict] = []
    manifest = {}
    while has_more:
        variables = {"manifest_id": manifest_id, "page": page, "page_size": page_size}
        body = {"query": Template(query).substitute(**variables)}
        response = session.post(url, json=body, headers=headers, cert=cert, timeout=timeout)
        if not bool(response):
            logger.error(f"Failed request to {url} with {response} had text body: {response.text}")
        response.raise_for_status()
        data = response.json()
        manifest = data["data"]["get_content_manifest"]["data"]

        components_batch = manifest["edges"]["components"]["data"] or ()
        components.extend(components_batch)
        has_more = len(components_batch) == page_size
        page += 1

    manifest["edges"]["components"]["data"] = components
    return manifest
