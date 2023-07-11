# flake8: noqa W291 E501
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = (("core", "0074_remove_cdn_repo_relations"),)

    operations = (
        # Below are stored procs to be used for latest filtering of components
        migrations.RunSQL("DROP FUNCTION if exists rpmvercmp;"),
        migrations.RunSQL(
            """
            CREATE OR REPLACE FUNCTION rpmvercmp(a text, b text) RETURNS int LANGUAGE PLPGSQL AS $$
DECLARE
    oldch1 CHAR(1);
    oldch2 CHAR(1);
    abuf VARCHAR(32767); -- will tighten these
    bbuf VARCHAR(32767); -- will tighten these
    str1 VARCHAR(32767); -- will tighten these
    str2 VARCHAR(32767); -- will tighten these
    one VARCHAR(32767);  -- will tighten these
    two VARCHAR(32767);  -- will tighten these
    rc int;
    isnum int;
BEGIN
    -- rough port of rpmvercmp.c into plsql stored proc

    -- check if the same
    IF a = b THEN
        RETURN 0;
    END IF;

    -- init vars
    abuf := a;
    bbuf := b;
    one := abuf;
    two := bbuf;

    -- loop through each version segment and compare
    WHILE (one IS NOT NULL OR two IS NOT NULL) LOOP

        -- handle persnickity tilde
        WHILE (one IS NOT NULL AND REGEXP_REPLACE(one,
'[[:alnum:]~^]', '') IS NULL) LOOP
            one := SUBSTR(one, INSTR(one, REGEXP_REPLACE(one,
'[[:alnum:]~^]', ''), 1) + 1);
        END LOOP;

        WHILE (two IS NOT NULL AND REGEXP_REPLACE(two,
'[[:alnum:]~^]', '') IS NULL) LOOP
            two := SUBSTR(two, INSTR(two, REGEXP_REPLACE(two,
'[[:alnum:]~^]', ''), 1) + 1);
        END LOOP;

        IF one = '~' OR two = '~' THEN
            IF one <> '~' THEN
                RETURN 1;
            END IF;

            IF two <> '~' THEN
                RETURN -1;
            END IF;

            one := SUBSTR(one, 2);
            two := SUBSTR(two, 2);
            CONTINUE;
        END IF;

        -- handle all fun variations of the caret separator
        IF one = '^' OR two = '^' THEN
            IF one IS NULL THEN
                RETURN -1;
            END IF;

            IF two IS NULL THEN
                RETURN 1;
            END IF;

            IF one <> '^' THEN
                RETURN 1;
            END IF;

            IF two <> '^' THEN
                RETURN -1;
            END IF;

            one := SUBSTR(one, 2);
            two := SUBSTR(two, 2);
            CONTINUE;
        END IF;

        IF one IS NULL OR two IS NULL THEN
            EXIT;
        END IF;

        str1 := one;
        str2 := two;

        IF REGEXP_REPLACE(str1, '[[:digit:]]', '') IS NULL THEN
            WHILE (str1 IS NOT NULL AND REGEXP_REPLACE(str1,
'[[:digit:]]', '') IS NULL) LOOP
                str1 := SUBSTR(str1, INSTR(str1, REGEXP_REPLACE(str1,
'[[:digit:]]', ''), 1) + 1);
            END LOOP;
            WHILE (str2 IS NOT NULL AND REGEXP_REPLACE(str2,
'[[:digit:]]', '') IS NULL) LOOP
                str2 := SUBSTR(str2, INSTR(str2, REGEXP_REPLACE(str2,
'[[:digit:]]', ''), 1) + 1);
            END LOOP;
            isnum := 1;
        ELSE
            WHILE (str1 IS NOT NULL AND REGEXP_REPLACE(str1,
'[[:alpha:]]', '') IS NULL) LOOP
                str1 := SUBSTR(str1, INSTR(str1, REGEXP_REPLACE(str1,
'[[:alpha:]]', ''), 1) + 1);
            END LOOP;
            WHILE (str2 IS NOT NULL AND REGEXP_REPLACE(str2,
'[[:alpha:]]', '') IS NULL) LOOP
                str2 := SUBSTR(str2, INSTR(str2, REGEXP_REPLACE(str2,
'[[:alpha:]]', ''), 1) + 1);
            END LOOP;
            isnum := 0;
        END IF;

        oldch1 := SUBSTR(str1, LENGTH(str1));
        str1 := SUBSTR(str1, 1, LENGTH(str1) - 1);
        oldch2 := SUBSTR(str2, LENGTH(str2));
        str2 := SUBSTR(str2, 1, LENGTH(str2) - 1);

        IF two IS NULL THEN
            RETURN (isnum + 1) / 2;
        END IF;

        IF one = str1 THEN
            RETURN (isnum - 1) / 2;
        END IF;

        IF isnum = 1 THEN
            IF LENGTH(one) > LENGTH(str2) THEN
                RETURN 1;
            END IF;

            IF LENGTH(str2) > LENGTH(one) THEN
                RETURN -1;
            END IF;
        END IF;

        rc := CASE WHEN one < str2 THEN -1 ELSE 1 END;

        IF rc <> 0 THEN
            RETURN rc;
        END IF;

        one := str1 || oldch1;
        two := str2 || oldch2;
    END LOOP;

    -- identical though segment seperating was different
    IF one IS NULL AND two IS NULL THEN
        RETURN 0;
    END IF;

    -- whichever version still has characters left is deemed the winner
    IF one IS NULL THEN
        RETURN -1;
    ELSE
        RETURN 1;
    END IF;
END;
 $$
            """
        ),  # noqa: E501
        migrations.RunSQL("DROP FUNCTION if exists get_latest_component;"),
        migrations.RunSQL(
            """
            CREATE FUNCTION get_latest_component(model_type text, ps_ofuri text, component_ns text, component_name text) RETURNS text LANGUAGE PLPGSQL AS $$  
  DECLARE
    COMPONENT RECORD;
    latest_uuid text;
    latest_version text;
    compare_versions int;
  BEGIN
    IF model_type = 'ProductStream' THEN
      FOR COMPONENT IN select *, row_number() over () from core_component
           INNER JOIN "core_component_productstreams"
           ON ("core_component"."uuid" = "core_component_productstreams"."component_id")
           INNER JOIN "core_productstream"
           ON ("core_component_productstreams"."productstream_id" = "core_productstream"."uuid")
           where core_component.namespace=component_ns and core_component.name=component_name and ( (core_component.type='RPM' and core_component.arch='src') or (core_component.type='OCI' and core_component.arch='noarch'))
           AND core_productstream.ofuri = ps_ofuri
         LOOP
             IF COMPONENT.row_number = 1 THEN
                latest_uuid := COMPONENT.uuid;
                latest_version := COMPONENT.version;
             ELSE
             compare_versions = rpmvercmp(COMPONENT.version, latest_version);
                IF compare_versions > 0 THEN
                    latest_uuid := COMPONENT.uuid;
                    latest_version := COMPONENT.version;
                END IF;
             END IF;
         END LOOP;
    ELSIF model_type = 'ProductVariant' THEN
      FOR COMPONENT IN select *, row_number() over () from core_component
           INNER JOIN "core_component_productvariants"
           ON ("core_component"."uuid" = "core_component_productvariants"."component_id")
           INNER JOIN "core_productvariant"
           ON ("core_component_productvariants"."productvariant_id" = "core_productvariant"."uuid")
           where core_component.namespace=component_ns and core_component.name=component_name and ( (core_component.type='RPM' and core_component.arch='src') or (core_component.type='OCI' and core_component.arch='noarch'))
           AND core_productvariant.ofuri = ps_ofuri
         LOOP
             IF COMPONENT.row_number = 1 THEN
                latest_uuid := COMPONENT.uuid;
                latest_version := COMPONENT.version;
             ELSE
             compare_versions = rpmvercmp(COMPONENT.version, latest_version);
                IF compare_versions > 0 THEN
                    latest_uuid := COMPONENT.uuid;
                    latest_version := COMPONENT.version;
                END IF;
             END IF;
         END LOOP;    
    ELSIF model_type = 'ProductVersion' THEN
      FOR COMPONENT IN select *, row_number() over () from core_component
           where core_component.namespace=component_ns and core_component.name=component_name and ( (core_component.type='RPM' and core_component.arch='src') or (core_component.type='OCI' and core_component.arch='noarch'))
         LOOP
             IF COMPONENT.row_number = 1 THEN
                latest_uuid := COMPONENT.uuid;
                latest_version := COMPONENT.version;
             ELSE
             compare_versions = rpmvercmp(COMPONENT.version, latest_version);
                IF compare_versions > 0 THEN
                    latest_uuid := COMPONENT.uuid;
                    latest_version := COMPONENT.version;
                END IF;
             END IF;
         END LOOP;    
    ELSIF model_type = 'Product' THEN
      FOR COMPONENT IN select *, row_number() over () from core_component
           INNER JOIN "core_component_products"
           ON ("core_component"."uuid" = "core_component_products"."component_id")
           INNER JOIN "core_product"
           ON ("core_component_products"."product_id" = "core_product"."uuid")
           where core_component.namespace=component_ns and core_component.name=component_name and ( (core_component.type='RPM' and core_component.arch='src') or (core_component.type='OCI' and core_component.arch='noarch'))
           AND core_product.ofuri = ps_ofuri
         LOOP
             IF COMPONENT.row_number = 1 THEN
                latest_uuid := COMPONENT.uuid;
                latest_version := COMPONENT.version;
             ELSE
             compare_versions = rpmvercmp(COMPONENT.version, latest_version);
                IF compare_versions > 0 THEN
                    latest_uuid := COMPONENT.uuid;
                    latest_version := COMPONENT.version;
                END IF;
             END IF;
         END LOOP;
    END IF;
    
    RETURN latest_uuid;
  END;
  $$
            """
        ),  # noqa: E501
    )
