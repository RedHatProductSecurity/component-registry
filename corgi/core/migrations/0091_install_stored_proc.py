# flake8: noqa W291 E501
from django.db import migrations

# NOTE - we are using Postgres functions instead of pure stored procedures

# NOTE - these constants should escape \d sequences to ensure migration is applied
#        correctly. Manual insertion of these functions will need to unescape.
#
RPMVERCMP_STOREDPROC_SQL = """
CREATE OR REPLACE FUNCTION rpmvercmp(a varchar, b varchar)
    RETURNS integer AS $$
DECLARE
    a_segments varchar[];
    b_segments varchar[];
    a_len integer;
    b_len integer;
    a_seg varchar;
    b_seg varchar;
BEGIN
    IF a = b THEN RETURN 0; END IF;
    a_segments := array(SELECT (regexp_matches(a, '(\\d+|[a-zA-Z]+|[~^])', 'g'))[1]);
    b_segments := array(SELECT (regexp_matches(b, '(\\d+|[a-zA-Z]+|[~^])', 'g'))[1]);
    a_len := array_length(a_segments, 1);
    b_len := array_length(b_segments, 1);
    FOR i IN 1..coalesce(least(a_len, b_len) + 1, 0) LOOP
        a_seg = a_segments[i];
        b_seg = b_segments[i];
        IF a_seg ~ '^\\d' THEN
            IF b_seg ~ '^\\d' THEN
                a_seg := ltrim(a_seg, '0');
                b_seg := ltrim(b_seg, '0');
                CASE
                    WHEN length(a_seg) > length(b_seg) THEN RETURN 1;
                    WHEN length(a_seg) < length(b_seg) THEN RETURN -1;
                    ELSE NULL; -- equality -> fallthrough to string comparison
                END CASE;
            ELSE
                RETURN 1;
            END IF;
        ELSIF b_seg ~ '^\\d' THEN
            RETURN -1;
        ELSIF a_seg = '~' THEN
            IF b_seg != '~' THEN
                RETURN -1;
            END IF;
        ELSIF b_seg = '~' THEN
            RETURN 1;
        ELSIF a_seg = '^' THEN
            IF b_seg != '^' THEN
                RETURN 1;
            END IF;
        ELSIF b_seg = '^' THEN
            RETURN -1;
        END IF;
        IF a_seg != b_seg THEN
            IF a_seg < b_seg THEN RETURN -1; ELSE RETURN 1; END IF;
        END IF;
    END LOOP;
    IF b_segments[a_len + 1] = '~' THEN RETURN 1; END IF;
    IF a_segments[b_len + 1] = '~' THEN RETURN -1; END IF;
    IF b_segments[a_len + 1] = '^' THEN RETURN -1; END IF;
    IF a_segments[b_len + 1] = '^' THEN RETURN 1; END IF;
    IF a_len > b_len THEN RETURN 1; END IF;
    IF a_len < b_len THEN RETURN -1; END IF;
    RETURN 0;
END $$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;
"""

RPMVERCMP_EPOCH_STOREDPROC_SQL = """
CREATE OR REPLACE FUNCTION rpmvercmp_epoch(epoch1 integer, version1 varchar,release1 varchar,
                                         epoch2 integer, version2 varchar,release2 varchar)
    RETURNS integer AS $$
DECLARE
    vercmp_result integer;
BEGIN
    epoch1 := COALESCE(epoch1, 0);
    epoch2 := COALESCE(epoch2, 0);
    IF epoch1 < epoch2 THEN RETURN -1; END IF;
    IF epoch1 > epoch2 THEN RETURN 1; END IF;
    vercmp_result := rpmvercmp(version1, version2);
    IF vercmp_result != 0 THEN RETURN vercmp_result; END IF;
    RETURN rpmvercmp(release1, release2);
END $$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;
"""  # noqa: E501

GET_LATEST_COMPONENT_STOREDPROC_SQL = """
CREATE OR REPLACE FUNCTION get_latest_component( model_type text, ps_ofuri text, component_ns text, component_name text, include_inactive_streams boolean) RETURNS uuid AS $$  
  DECLARE
    COMPONENT RECORD;
    latest_uuid text;
    latest_epoch int;
    latest_version text;
    latest_release text;
    compare_versions int;
  BEGIN
    IF model_type = 'ProductVariant' THEN
          FOR COMPONENT IN 
            SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_productvariants"
            ON ("core_component"."uuid" = "core_component_productvariants"."component_id")
            INNER JOIN "core_productvariant"
            ON ("core_component_productvariants"."productvariant_id" = "core_productvariant"."uuid")
            WHERE core_component.name=component_name 
            AND core_component.namespace=component_ns 
            AND (ps_ofuri IS NOT NULL AND core_productvariant.ofuri = ps_ofuri)
            AND ((core_component.type='RPM' and core_component.arch='src') 
              OR ( core_component.type='RPMMOD')
              OR (core_component.type='OCI' and core_component.arch='noarch') 
              OR ("core_component"."arch" = 'noarch' AND "core_component"."type" = 'GITHUB'))
         LOOP
            IF rpmvercmp_epoch(COMPONENT.epoch,COMPONENT.version,COMPONENT.release,latest_epoch,latest_version,latest_release) >= 0 THEN
                latest_uuid := COMPONENT.uuid;
                latest_epoch := COMPONENT.epoch;
                latest_version := COMPONENT.version;
                latest_release := COMPONENT.release;
            END IF;
         END LOOP;
    ELSIF model_type = 'ProductVersion' THEN
          FOR COMPONENT IN 
            SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_productversions"
            ON ("core_component"."uuid" = "core_component_productversions"."component_id")
            INNER JOIN "core_productversion"
            ON ("core_component_productversions"."productversion_id" = "core_productversion"."uuid")
            WHERE core_component.name=component_name 
            AND core_component.namespace=component_ns 
            AND (ps_ofuri IS NOT NULL AND core_productversion.ofuri = ps_ofuri)
            AND ((core_component.type='RPM' and core_component.arch='src') 
              OR ( core_component.type='RPMMOD')
              OR (core_component.type='OCI' and core_component.arch='noarch') 
              OR ("core_component"."arch" = 'noarch' AND "core_component"."type" = 'GITHUB'))
         LOOP
            IF rpmvercmp_epoch(COMPONENT.epoch,COMPONENT.version,COMPONENT.release,latest_epoch,latest_version,latest_release) >= 0 THEN
                latest_uuid := COMPONENT.uuid;
                latest_epoch := COMPONENT.epoch;
                latest_version := COMPONENT.version;
                latest_release := COMPONENT.release;
            END IF;
         END LOOP;
    ELSIF model_type = 'Product' THEN
          FOR COMPONENT IN 
            SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_products"
            ON ("core_component"."uuid" = "core_component_products"."component_id")
            INNER JOIN "core_product"
            ON ("core_component_products"."product_id" = "core_product"."uuid")
            WHERE core_component.name=component_name 
            AND core_component.namespace=component_ns 
            AND (ps_ofuri IS NOT NULL AND core_product.ofuri = ps_ofuri)
            AND ((core_component.type='RPM' and core_component.arch='src') 
              OR ( core_component.type='RPMMOD')
              OR (core_component.type='OCI' and core_component.arch='noarch') 
              OR ("core_component"."arch" = 'noarch' AND "core_component"."type" = 'GITHUB'))
         LOOP
            IF rpmvercmp_epoch(COMPONENT.epoch,COMPONENT.version,COMPONENT.release,latest_epoch,latest_version,latest_release) >= 0 THEN
                latest_uuid := COMPONENT.uuid;
                latest_epoch := COMPONENT.epoch;
                latest_version := COMPONENT.version;
                latest_release := COMPONENT.release;
            END IF;
         END LOOP;
    ELSE 
          FOR COMPONENT IN 
              SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
              INNER JOIN "core_component_productstreams"
              ON ("core_component"."uuid" = "core_component_productstreams"."component_id")
              INNER JOIN "core_productstream"
              ON ("core_component_productstreams"."productstream_id" = "core_productstream"."uuid")
              WHERE core_component.name=component_name 
              AND core_component.namespace=component_ns 
              AND (not(include_inactive_streams) AND core_productstream.active)
              AND (ps_ofuri IS NOT NULL AND core_productstream.ofuri = ps_ofuri)
              AND ((core_component.type='RPM' and core_component.arch='src') 
                OR ( core_component.type='RPMMOD')
                OR (core_component.type='OCI' and core_component.arch='noarch') 
                OR ("core_component"."arch" = 'noarch' AND "core_component"."type" = 'GITHUB'))
         LOOP
            IF rpmvercmp_epoch(COMPONENT.epoch,COMPONENT.version,COMPONENT.release,latest_epoch,latest_version,latest_release) >= 0 THEN
                latest_uuid := COMPONENT.uuid;
                latest_epoch := COMPONENT.epoch;
                latest_version := COMPONENT.version;
                latest_release := COMPONENT.release;
            END IF;
         END LOOP;
    END IF;
    RETURN latest_uuid;
  END;
  $$ LANGUAGE plpgsql;  
"""  # noqa: E501


class Migration(migrations.Migration):
    dependencies = (("core", "0090_auto_20230926_1727"),)

    operations = (
        # Below are stored procs to be used for latest filtering of components
        migrations.RunSQL(RPMVERCMP_STOREDPROC_SQL),
        migrations.RunSQL(RPMVERCMP_EPOCH_STOREDPROC_SQL),
        migrations.RunSQL(GET_LATEST_COMPONENT_STOREDPROC_SQL),
    )
