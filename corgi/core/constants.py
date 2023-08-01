"""
    model constants
"""
import re

from django.db.models import Q

CONTAINER_DIGEST_FORMATS = (
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
CONTAINER_REPOSITORY = "registry.redhat.io"
CORGI_PRODUCT_TAXONOMY_VERSION = "v1"
CORGI_COMPONENT_TAXONOMY_VERSION = "v1"

# Assume Red Hat Maven components are always located in the GA repo
# There's also an Early Access repo: https://maven.repository.redhat.com/earlyaccess/all
# But anything in there should become GA eventually
# and we don't want the purls to change over time / we don't know when GA will happen
RED_HAT_MAVEN_REPOSITORY = "https://maven.repository.redhat.com/ga"

# Map MPTT node levels in our product taxonomy to model names as defined in models.py
NODE_LEVEL_MODEL_MAPPING = {
    0: "product",
    1: "product_version",
    2: "product_stream",
    3: "product_variant",
    4: "channel",
}

# Map model names as defined in models.py to MPTT node levels in our product taxonomy
# "product_version" -> "ProductVersion"
MODEL_NODE_LEVEL_MAPPING = {
    value.title().replace("_", ""): key for key, value in NODE_LEVEL_MODEL_MAPPING.items()
}

# Take a model name like ProductVariant, make it lowercase
# then add an underscore to match the product_variants= filter in the API
MODEL_FILTER_NAME_MAPPING = {
    "Product": "products",
    "ProductVersion": "product_versions",
    "ProductStream": "product_streams",
    "ProductVariant": "product_variants",
}

# Filter on "root components": SRPMs, index container images, or Github repos for managed services
SRPM_CONDITION = Q(type="RPM", arch="src")
INDEX_CONTAINER_CONDITION = Q(type="OCI", arch="noarch")
SERVICE_REPO_CONDITION = Q(type="GITHUB", namespace="REDHAT", arch="noarch")
ROOT_COMPONENTS_CONDITION = SRPM_CONDITION | INDEX_CONTAINER_CONDITION | SERVICE_REPO_CONDITION

# Regex for generating el_match field
EL_MATCH_RE = re.compile(r".*el(\d+)?[._-]?(\d+)?[._-]?(\d+)?(.*)")
