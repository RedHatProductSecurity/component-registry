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
MODULAR_SRPM_CONDITION = SRPM_CONDITION & Q(release__contains=".module")
# Only root components should be linked to software builds
# This lets us distinguish between Red Hat Maven components which are / are not roots
# Using some combination of type, namespace, and arch will not work
# since some Red Hat Maven components are roots (e.g. quarkus-bom)
# but others are provided by / children of these roots (e.g. agroal-api)
ROOT_COMPONENTS_CONDITION = Q(software_build_id__isnull=False)
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
LATEST_FILTER_DEFINITION = (
    "get_latest_component( "
    "model_type text, ps_ofuri text, component_type text, component_ns text, "
    "component_name text, component_arch text, include_inactive_streams boolean) "
    "RETURNS uuid AS $$"
)
LATEST_FILTER_FIELDS = (
    "core_component.uuid, core_component.epoch, "
    "core_component.version, core_component.release from core_component"
)
LATEST_FILTER_WHERE = (
    "WHERE core_component.name=component_name "
    "AND core_component.namespace=component_ns "
    "AND core_component.type=component_type "
    "AND core_component.arch=component_arch "
    f"AND ({ROOT_COMPONENTS_SQL})"
)

LATEST_FILTER_INTO = (
    "INTO component_uuid, component_epoch, component_version, component_release; "
    "EXIT WHEN NOT FOUND; "
    "IF rpmvercmp_epoch(component_epoch, component_version, component_release, "
    "latest_epoch, latest_version, latest_release) >= 0 THEN "
    "latest_uuid := component_uuid; "
    "latest_epoch := component_epoch; "
    "latest_version := component_version; "
    "latest_release := component_release; "
    "END IF;"
)

# We are using Postgres functions instead of pure stored procedures
# These constants should escape \d sequences to ensure migration is applied
# correctly. Manual insertion of these functions will need to unescape.
GET_LATEST_COMPONENT_STOREDPROC_SQL = f"""
CREATE OR REPLACE FUNCTION {LATEST_FILTER_DEFINITION}
  DECLARE
    product_component_cursor CURSOR FOR
        SELECT {LATEST_FILTER_FIELDS}
            INNER JOIN "core_component_products"
            ON ("core_component"."uuid" = "core_component_products"."component_id")
            INNER JOIN "core_product"
            ON ("core_component_products"."product_id" = "core_product"."uuid")
            {LATEST_FILTER_WHERE}
            AND (ps_ofuri IS NOT NULL AND core_product.ofuri = ps_ofuri);

    product_version_component_cursor CURSOR FOR
        SELECT {LATEST_FILTER_FIELDS}
            INNER JOIN "core_component_productversions"
            ON ("core_component"."uuid" = "core_component_productversions"."component_id")
            INNER JOIN "core_productversion"
            ON ("core_component_productversions"."productversion_id" = "core_productversion"."uuid")
            {LATEST_FILTER_WHERE}
            AND (ps_ofuri IS NOT NULL AND core_productversion.ofuri = ps_ofuri);

    product_stream_component_cursor CURSOR FOR
        SELECT {LATEST_FILTER_FIELDS}
            INNER JOIN "core_component_productstreams"
            ON ("core_component"."uuid" = "core_component_productstreams"."component_id")
            INNER JOIN "core_productstream"
            ON ("core_component_productstreams"."productstream_id" = "core_productstream"."uuid")
            {LATEST_FILTER_WHERE}
            AND (include_inactive_streams OR core_productstream.active)
            AND (ps_ofuri IS NOT NULL AND core_productstream.ofuri = ps_ofuri);

    product_variant_component_cursor CURSOR FOR
        SELECT {LATEST_FILTER_FIELDS}
            INNER JOIN "core_component_productvariants"
            ON ("core_component"."uuid" = "core_component_productvariants"."component_id")
            INNER JOIN "core_productvariant"
            ON ("core_component_productvariants"."productvariant_id" = "core_productvariant"."uuid")
            {LATEST_FILTER_WHERE}
            AND (ps_ofuri IS NOT NULL AND core_productvariant.ofuri = ps_ofuri);

    component_uuid text;
    component_epoch int;
    component_version text;
    component_release text;
    latest_uuid text;
    latest_epoch int;
    latest_version text;
    latest_release text;
  BEGIN
    IF model_type = 'ProductVariant' THEN
        OPEN product_variant_component_cursor;
        LOOP
            FETCH NEXT FROM product_variant_component_cursor {LATEST_FILTER_INTO}
        END LOOP;
        CLOSE product_variant_component_cursor;
    ELSIF model_type = 'ProductVersion' THEN
        OPEN product_version_component_cursor;
        LOOP
            FETCH NEXT FROM product_version_component_cursor {LATEST_FILTER_INTO}
        END LOOP;
        CLOSE product_version_component_cursor;
    ELSIF model_type = 'Product' THEN
        OPEN product_component_cursor;
        LOOP
            FETCH NEXT FROM product_component_cursor {LATEST_FILTER_INTO}
        END LOOP;
        CLOSE product_component_cursor;
    ELSE
        OPEN product_stream_component_cursor;
        LOOP
            FETCH NEXT FROM product_stream_component_cursor {LATEST_FILTER_INTO}
        END LOOP;
        CLOSE product_stream_component_cursor;
    END IF;
    RETURN latest_uuid;
  END;
  $$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;
"""

# Note- both get_latest_component and get_latest_components function will be
# deprecated during graphdb refactor
GET_LATEST_COMPONENTS_STOREDPROC_SQL = """
CREATE OR REPLACE FUNCTION get_latest_components( model_type text, ofuris text[], component_type text, component_ns text, component_name text, component_arch text, include_inactive_streams boolean) RETURNS SETOF uuid AS $$
  DECLARE
    p_ofuri text;
    component_uuid text;
    component_epoch int;
    component_version text;
    component_release text;
    latest_uuid text;
    latest_epoch int;
    latest_version text;
    latest_release text;

    product_component_cursor CURSOR FOR
        SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_products"
            ON ("core_component"."uuid" = "core_component_products"."component_id")
            INNER JOIN "core_product"
            ON ("core_component_products"."product_id" = "core_product"."uuid")
            WHERE core_component.name=component_name AND core_component.namespace=component_ns AND core_component.type=component_type AND core_component.arch=component_arch AND (core_component.software_build_uuid IS NOT NULL AND NOT (core_component.arch = 'src' AND core_component.release LIKE '%.module%' AND core_component.type = 'RPM'))
            AND core_product.ofuri = p_ofuri;

    product_version_component_cursor CURSOR FOR
        SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_productversions"
            ON ("core_component"."uuid" = "core_component_productversions"."component_id")
            INNER JOIN "core_productversion"
            ON ("core_component_productversions"."productversion_id" = "core_productversion"."uuid")
            WHERE core_component.name=component_name AND core_component.namespace=component_ns AND core_component.type=component_type AND core_component.arch=component_arch AND (core_component.software_build_uuid IS NOT NULL AND NOT (core_component.arch = 'src' AND core_component.release LIKE '%.module%' AND core_component.type = 'RPM'))
            AND core_productversion.ofuri = p_ofuri;

    product_stream_component_cursor CURSOR FOR
        SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_productstreams"
            ON ("core_component"."uuid" = "core_component_productstreams"."component_id")
            INNER JOIN "core_productstream"
            ON ("core_component_productstreams"."productstream_id" = "core_productstream"."uuid")
            WHERE core_component.name=component_name AND core_component.namespace=component_ns AND core_component.type=component_type AND core_component.arch=component_arch AND (core_component.software_build_uuid IS NOT NULL AND NOT (core_component.arch = 'src' AND core_component.release LIKE '%.module%' AND core_component.type = 'RPM'))
            AND (include_inactive_streams OR core_productstream.active)
            AND core_productstream.ofuri = p_ofuri;

    product_variant_component_cursor CURSOR FOR
        SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_productvariants"
            ON ("core_component"."uuid" = "core_component_productvariants"."component_id")
            INNER JOIN "core_productvariant"
            ON ("core_component_productvariants"."productvariant_id" = "core_productvariant"."uuid")
            WHERE core_component.name=component_name AND core_component.namespace=component_ns AND core_component.type=component_type AND core_component.arch=component_arch AND (core_component.software_build_uuid IS NOT NULL AND NOT (core_component.arch = 'src' AND core_component.release LIKE '%.module%' AND core_component.type = 'RPM'))
            AND core_productvariant.ofuri = p_ofuri;

  BEGIN
    FOREACH p_ofuri IN ARRAY ofuris
    LOOP

    latest_uuid := NULL;
    latest_epoch := 0;
    latest_version := '';
    latest_release := '';

    IF model_type = 'ProductVariant' THEN
        OPEN product_variant_component_cursor;
        LOOP
            FETCH NEXT FROM product_variant_component_cursor INTO component_uuid, component_epoch, component_version, component_release; EXIT WHEN NOT FOUND; IF rpmvercmp_epoch(component_epoch, component_version, component_release, latest_epoch, latest_version, latest_release) >= 0 THEN latest_uuid := component_uuid; latest_epoch := component_epoch; latest_version := component_version; latest_release := component_release; END IF;
        END LOOP;
        CLOSE product_variant_component_cursor;
    ELSIF model_type = 'ProductVersion' THEN
        OPEN product_version_component_cursor;
        LOOP
            FETCH NEXT FROM product_version_component_cursor INTO component_uuid, component_epoch, component_version, component_release; EXIT WHEN NOT FOUND; IF rpmvercmp_epoch(component_epoch, component_version, component_release, latest_epoch, latest_version, latest_release) >= 0 THEN latest_uuid := component_uuid; latest_epoch := component_epoch; latest_version := component_version; latest_release := component_release; END IF;
        END LOOP;
        CLOSE product_version_component_cursor;
    ELSIF model_type = 'Product' THEN
        OPEN product_component_cursor;
        LOOP
            FETCH NEXT FROM product_component_cursor INTO component_uuid, component_epoch, component_version, component_release; EXIT WHEN NOT FOUND; IF rpmvercmp_epoch(component_epoch, component_version, component_release, latest_epoch, latest_version, latest_release) >= 0 THEN latest_uuid := component_uuid; latest_epoch := component_epoch; latest_version := component_version; latest_release := component_release; END IF;
        END LOOP;
        CLOSE product_component_cursor;
    ELSE
        OPEN product_stream_component_cursor;
        LOOP
            FETCH NEXT FROM product_stream_component_cursor INTO component_uuid, component_epoch, component_version, component_release; EXIT WHEN NOT FOUND; IF rpmvercmp_epoch(component_epoch, component_version, component_release, latest_epoch, latest_version, latest_release) >= 0 THEN latest_uuid := component_uuid; latest_epoch := component_epoch; latest_version := component_version; latest_release := component_release; END IF;
        END LOOP;
        CLOSE product_stream_component_cursor;
    END IF;
    RETURN NEXT latest_uuid;
    END LOOP;
    RETURN;
  END;
  $$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;
"""  # noqa
