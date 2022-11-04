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
