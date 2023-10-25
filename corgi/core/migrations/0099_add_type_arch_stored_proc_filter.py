# flake8: noqa W291 E501
from django.db import migrations

# NOTE - we are using Postgres functions instead of pure stored procedures

# NOTE - these constants should escape \d sequences to ensure migration is applied
#        correctly. Manual insertion of these functions will need to unescape.
#
GET_LATEST_COMPONENT_STOREDPROC_SQL = """
CREATE OR REPLACE FUNCTION get_latest_component( model_type text, ps_ofuri text, component_type text, component_ns text, component_name text, component_arch text, include_inactive_streams boolean) RETURNS uuid AS $$  
  DECLARE
    product_component_cursor CURSOR FOR
        SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_products"
            ON ("core_component"."uuid" = "core_component_products"."component_id")
            INNER JOIN "core_product"
            ON ("core_component_products"."product_id" = "core_product"."uuid")
            WHERE core_component.name=component_name 
            AND core_component.namespace=component_ns 
            AND core_component.type=component_type 
            AND core_component.arch=component_arch
            AND (include_inactive_streams OR core_productstream.active)
            AND (ps_ofuri IS NOT NULL AND core_product.ofuri = ps_ofuri)
            AND ((core_component.type='RPM' and core_component.arch='src') 
              OR ( core_component.type='RPMMOD')
              OR (core_component.type='OCI' and core_component.arch='noarch') 
              OR ("core_component"."arch" = 'noarch' AND "core_component"."namespace" = 'REDHAT' AND "core_component"."type" = 'GITHUB'));
                      
    product_version_component_cursor CURSOR FOR
        SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_productversions"
            ON ("core_component"."uuid" = "core_component_productversions"."component_id")
            INNER JOIN "core_productversion"
            ON ("core_component_productversions"."productversion_id" = "core_productversion"."uuid")
            WHERE core_component.name=component_name 
            AND core_component.namespace=component_ns 
            AND core_component.type=component_type 
            AND core_component.arch=component_arch
            AND (include_inactive_streams OR core_productstream.active)
            AND (ps_ofuri IS NOT NULL AND core_productversion.ofuri = ps_ofuri)
            AND ((core_component.type='RPM' and core_component.arch='src') 
              OR ( core_component.type='RPMMOD')
              OR (core_component.type='OCI' and core_component.arch='noarch') 
              OR ("core_component"."arch" = 'noarch' AND "core_component"."namespace" = 'REDHAT' AND "core_component"."type" = 'GITHUB'));
              
    product_stream_component_cursor CURSOR FOR
        SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_productstreams"
            ON ("core_component"."uuid" = "core_component_productstreams"."component_id")
            INNER JOIN "core_productstream"
            ON ("core_component_productstreams"."productstream_id" = "core_productstream"."uuid")
            WHERE core_component.name=component_name 
            AND core_component.namespace=component_ns 
            AND core_component.type=component_type 
            AND core_component.arch=component_arch
            AND (include_inactive_streams OR core_productstream.active)
            AND (ps_ofuri IS NOT NULL AND core_productstream.ofuri = ps_ofuri)
            AND ((core_component.type='RPM' and core_component.arch='src') 
                OR ( core_component.type='RPMMOD')
                OR (core_component.type='OCI' and core_component.arch='noarch') 
                OR ("core_component"."arch" = 'noarch' AND "core_component"."namespace" = 'REDHAT' AND "core_component"."type" = 'GITHUB'));

    product_variant_component_cursor CURSOR FOR
        SELECT core_component.uuid, core_component.epoch, core_component.version, core_component.release from core_component
            INNER JOIN "core_component_productvariants"
            ON ("core_component"."uuid" = "core_component_productvariants"."component_id")
            INNER JOIN "core_productvariant"
            ON ("core_component_productvariants"."productvariant_id" = "core_productvariant"."uuid")
            WHERE core_component.name=component_name 
            AND core_component.namespace=component_ns 
            AND core_component.type=component_type 
            AND core_component.arch=component_arch
            AND (include_inactive_streams OR core_productstream.active)
            AND (ps_ofuri IS NOT NULL AND core_productvariant.ofuri = ps_ofuri)
            AND ((core_component.type='RPM' and core_component.arch='src') 
              OR ( core_component.type='RPMMOD')
              OR (core_component.type='OCI' and core_component.arch='noarch') 
              OR ("core_component"."arch" = 'noarch' AND "core_component"."namespace" = 'REDHAT' AND "core_component"."type" = 'GITHUB'));

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
            FETCH NEXT FROM product_variant_component_cursor INTO component_uuid,component_epoch,component_version,component_release;
            EXIT WHEN NOT FOUND;    
            IF rpmvercmp_epoch(component_epoch,component_version,component_release,latest_epoch,latest_version,latest_release) >= 0 THEN
                latest_uuid := component_uuid;
                latest_epoch := component_epoch;
                latest_version := component_version;
                latest_release := component_release;
            END IF;
        END LOOP;    
        CLOSE product_variant_component_cursor;
    ELSIF model_type = 'ProductVersion' THEN
        OPEN product_version_component_cursor;    
        LOOP
            FETCH NEXT FROM product_version_component_cursor INTO component_uuid,component_epoch,component_version,component_release;
            EXIT WHEN NOT FOUND;    
            IF rpmvercmp_epoch(component_epoch,component_version,component_release,latest_epoch,latest_version,latest_release) >= 0 THEN
                latest_uuid := component_uuid;
                latest_epoch := component_epoch;
                latest_version := component_version;
                latest_release := component_release;
            END IF;
        END LOOP;    
        CLOSE product_version_component_cursor;
    ELSIF model_type = 'Product' THEN
        OPEN product_component_cursor;    
        LOOP
            FETCH NEXT FROM product_component_cursor INTO component_uuid,component_epoch,component_version,component_release;
            EXIT WHEN NOT FOUND;    
            IF rpmvercmp_epoch(component_epoch,component_version,component_release,latest_epoch,latest_version,latest_release) >= 0 THEN
                latest_uuid := component_uuid;
                latest_epoch := component_epoch;
                latest_version := component_version;
                latest_release := component_release;
            END IF;
        END LOOP;    
        CLOSE product_component_cursor;
    ELSE 
        OPEN product_stream_component_cursor;    
        LOOP
            FETCH NEXT FROM product_stream_component_cursor INTO component_uuid,component_epoch,component_version,component_release;
            EXIT WHEN NOT FOUND;    
            IF rpmvercmp_epoch(component_epoch,component_version,component_release,latest_epoch,latest_version,latest_release) >= 0 THEN
                latest_uuid := component_uuid;
                latest_epoch := component_epoch;
                latest_version := component_version;
                latest_release := component_release;
            END IF;
        END LOOP;    
        CLOSE product_stream_component_cursor;
    END IF;    
    RETURN latest_uuid;
  END;
  $$ LANGUAGE plpgsql;    
"""  # noqa: E501


class Migration(migrations.Migration):
    dependencies = (("core", "0098_fix_duplicate_containers"),)

    operations = (migrations.RunSQL(GET_LATEST_COMPONENT_STOREDPROC_SQL),)
