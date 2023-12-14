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
RED_HAT_MAVEN_REPOSITORY = "https://maven.repository.redhat.com/ga/"

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

# Map node levels defined above to component many-to-many attribute names as defined in models.py
# "product_version" -> "productversions"
NODE_LEVEL_ATTRIBUTE_MAPPING = {
    key: value.replace("_", "") + "s" for key, value in NODE_LEVEL_MODEL_MAPPING.items()
}

# Take a model name like ProductVariant, make it lowercase
# then add an underscore to match the product_variants= filter in the API
MODEL_FILTER_NAME_MAPPING = {
    "Product": "products",
    "ProductVersion": "product_versions",
    "ProductStream": "product_streams",
    "ProductVariant": "product_variants",
}

# Filter on "root components":
# SRPMs, modules, index container images, or Github repos for managed services
SRPM_CONDITION = Q(type="RPM", arch="src")
# Only root components should be linked to software builds
# This lets us distinguish between Red Hat Maven components which are / are not roots
# Using some combination of type, namespace, and arch will not work
# since some Red Hat Maven components are roots (e.g. quarkus-bom)
# but others are provided by / children of these roots (e.g. agroal-api)
ROOT_COMPONENTS_CONDITION = Q(software_build_id__isnull=False)
MODULAR_SRPM_CONDITION = Q(type="RPM", arch="src", release__contains=".module")
# If you change above, fix below to match
# then deploy the updated GET_LATEST_COMPONENT_STOREDPROC_SQL in a new migration
ROOT_COMPONENTS_SQL = (
    "core_component.software_build_uuid IS NOT NULL AND "
    "NOT (core_component.arch = 'src' AND "
    "core_component.release LIKE '%.module%' AND "
    "core_component.type = 'RPM')"
)


# Regex for generating el_match field
EL_MATCH_RE = re.compile(r".*el(\d+)?[._-]?(\d+)?[._-]?(\d+)?(.*)")

# List of products and releases which publish SBOMer manifests and should
# process shipped errata accordingly
SBOMER_PRODUCT_MAP = {
    "RHBQ": [
        "Red Hat build of Quarkus Middleware",
    ],
}

# Strings inside tuples are automatically joined together onto one line
# So the SQL syntax will be correct in the final stored procedure code
# We are using Postgres functions instead of pure stored procedures
# These constants should escape \d sequences to ensure migration is applied
# correctly. Manual insertion of these functions will need to unescape.
GET_LATEST_COMPONENT_STOREDPROC_SQL = f"""
CREATE OR REPLACE FUNCTION 
    get_latest_component(component_type text, component_ns text, component_name text,
     component_arch text) RETURNS uuid AS $$
  DECLARE
    component_cursor CURSOR FOR
        SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
                WHERE core_component.name=component_name 
                AND core_component.namespace=component_ns 
                AND core_component.type=component_type 
                AND core_component.arch=component_arch;

    component_uuid text;
    component_epoch int;
    component_version text;
    component_release text;
    latest_uuid text;
    latest_epoch int;
    latest_version text;
    latest_release text;
  BEGIN
       OPEN component_cursor;
       LOOP
           FETCH NEXT FROM component_cursor 
                INTO component_uuid, component_epoch, component_version, component_release; 
                EXIT WHEN NOT FOUND; 
                IF rpmvercmp_epoch(component_epoch, component_version, component_release, 
                latest_epoch, latest_version, latest_release) >= 0 THEN 
                latest_uuid := component_uuid; 
                latest_epoch := component_epoch; 
                latest_version := component_version; 
                latest_release := component_release; 
                END IF;
       END LOOP;
       CLOSE component_cursor;
    RETURN latest_uuid;
  END;
  $$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;
"""
