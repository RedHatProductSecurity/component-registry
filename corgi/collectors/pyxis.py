import logging
from string import Template

from django.conf import settings
from django.utils import dateparse
from django.utils.timezone import make_aware
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from corgi.collectors.models import CollectorPyxisImage, CollectorPyxisImageRepository

logger = logging.getLogger(__name__)

session = Session()
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


def get_manifest_data(manifest_id: str, page_size: int = 50) -> dict:
    """Pull a manifest from pyxis"""

    url = settings.PYXIS_GRAPHQL_URL

    if not url:
        raise ValueError("Set CORGI_PYXIS_GRAPHQL_URL to get manifests from pyxis")

    if not settings.PYXIS_CERT or not settings.PYXIS_KEY:
        raise ValueError("Set CORGI_UMB_CERT and CORGI_UMB_KEY to get manifests from pyxis")
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
        # If there are fewer components on this page than the page size,
        # this page must be the last page
        has_more = len(components_batch) == page_size
        page += 1

    manifest["edges"]["components"]["data"] = components
    return manifest


def get_repo_for_label(label: str) -> str:
    repo_names = (
        CollectorPyxisImageRepository.objects.filter(images__name_label=label)
        .values_list("name", flat=True)
        .distinct()
    )

    repo_name = repo_names.first()
    if not repo_name:
        return ""
    if repo_names.count() > 1:
        # I'm not sure if this is the correct logic, perhaps it's better to return both
        # repositories and create a distinct root component for each repo, both linked to a single
        # build object
        raise ValueError(
            f"Pyxis images for label: {label} don't have a single distinct repository_name."
            f"They have {list(repo_names)}"
        )
    return repo_name


def get_repo_by_nvr(nvr: str) -> str:
    """Look up a Pyxis image by NVR, save it to a Pyxis cache in the collector models and return
    the image's repo name"""
    url = f"{settings.PYXIS_REST_API_URL}/v1/images/nvr/{nvr}"
    response = session.get(url)
    response.raise_for_status()
    data = response.json()

    repository_names_from_images = set()

    for pyxis_image in data["data"]:
        pyxis_id = pyxis_image.pop("_id")
        arch = pyxis_image.pop("architecture", "")
        image_id = pyxis_image.pop("image_id")

        parsed_creation_date = dateparse.parse_datetime(
            pyxis_image.get("creation_date").split(".")[0]
        )
        if parsed_creation_date:
            creation_date = make_aware(parsed_creation_date)
        else:
            creation_date = None

        name_label = ""
        for pyxis_label in pyxis_image["parsed_data"].get("labels", []):
            if pyxis_label["name"] == "name":
                name_label = pyxis_label["value"]

        pyxis_image_repos = set()
        for pyxis_repo in pyxis_image.pop("repositories"):
            name = pyxis_repo.pop("repository")
            registry = pyxis_repo.pop("registry")
            manifest_list_digest = pyxis_repo.pop("manifest_list_digest")
            tags = [tag["name"] for tag in pyxis_repo.get("tags", [])]
            image_advisory_id = pyxis_repo.pop("image_advisory_id", "")
            collector_pyxis_repo, created = CollectorPyxisImageRepository.objects.update_or_create(
                name=name,
                registry=registry,
                manifest_list_digest=manifest_list_digest,
                defaults={
                    "image_advisory_id": image_advisory_id,
                    "tags": tags,
                    "meta_attr": pyxis_repo,
                },
            )
            if created:
                logger.info(f"Created Collector Pyxis Repo {collector_pyxis_repo}")
            # Can't add to the CollectorPyxisImage yet because it's not saved
            pyxis_image_repos.add(collector_pyxis_repo)
            repository_names_from_images.add(name)

        # Now create the CollectorPyxisImage object
        collector_pyxis_image, created = CollectorPyxisImage.objects.update_or_create(
            pyxis_id=pyxis_id,
            defaults={
                "arch": arch,
                "creation_date": creation_date,
                "name_label": name_label,
                "nvr": nvr,
                "image_id": image_id,
                "meta_attr": pyxis_image,
            },
        )
        if created:
            logger.info(f"Created Collector Pyxis image with id nvr and arch {nvr}, {arch}")

        # Update the repos linked to the image
        collector_pyxis_image.repos.clear()
        for repo in pyxis_image_repos:
            collector_pyxis_image.repos.add(repo)
    return _extract_repo_and_image_name(nvr, repository_names_from_images)


def _extract_repo_and_image_name(nvr, repository_names_from_images: set[str]) -> str:
    """Check the repository_names_from_images is a single result. If it is split the name from the
    repository name. If there are no repo_names in repository_names_from_images, return None"""
    # I'm not sure if this is the correct logic, perhaps it's better to return both repositories and
    # create a distinct root component for each repo, both linked to a single build object
    if len(repository_names_from_images) > 1:
        raise ValueError(
            f"Pyxis images for NVR: {nvr} don't have a single distinct repository_name."
            f"They have {repository_names_from_images}"
        )

    repository_name_from_images = next(iter(repository_names_from_images), "")
    if repository_name_from_images:
        return repository_name_from_images
    return ""
