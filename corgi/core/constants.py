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

# Map model names as defined in models.py to MPTT node levels in our product taxonomy
MODEL_NODE_LEVEL_MAPPING = {
    "Product": 0,
    "ProductVersion": 1,
    "ProductStream": 2,
    "ProductVariant": 3,
    "Channel": 4,
}

# Filter on "root components": SRPMs, modules, or index container images
SRPM_CONDITION = Q(type="RPM", arch="src")
MODULE_CONDITION = Q(type="RPMMOD")
INDEX_CONTAINER_CONDITION = Q(type="OCI", arch="noarch")
ROOT_COMPONENTS_CONDITION = SRPM_CONDITION | MODULE_CONDITION | INDEX_CONTAINER_CONDITION

# The ratio of build types to total in the relation table
CDN_RELATIONS_RATIO = 0.8
BREW_RELATIONS_RATIO = 0.15
YUM_RELATIONS_RATIO = 0.05
