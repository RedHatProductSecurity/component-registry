from django.db.models import Q

"""
    model constants
"""

CONTAINER_DIGEST_FORMATS = (
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
CONTAINER_REPOSITORY = "registry.redhat.io"
CORGI_PRODUCT_TAXONOMY_VERSION = "v1"
CORGI_COMPONENT_TAXONOMY_VERSION = "v1"

# Map MPTT node levels in our product taxonomy to model names as defined in models.py
NODE_LEVEL_MODEL_MAPPING = {
    0: "product",
    1: "product_version",
    2: "Product_stream",
    3: "product_variant",
    4: "channel",
}

# Map model names as defined in models.py to MPTT node levels in our product taxonomy
# "product_version" -> "ProductVersion"
MODEL_NODE_LEVEL_MAPPING = {
    value.title().replace("_", ""): key for key, value in NODE_LEVEL_MODEL_MAPPING.items()
}

# Filter on "root components": SRPMs, modules, or index container images
SRPM_CONDITION = Q(type="RPM", arch="src")
MODULE_CONDITION = Q(type="RPMMOD")
INDEX_CONTAINER_CONDITION = Q(type="OCI", arch="noarch")
ROOT_COMPONENTS_CONDITION = SRPM_CONDITION | MODULE_CONDITION | INDEX_CONTAINER_CONDITION
