from django.db import migrations
from django.db.models import F, Value, functions

# Must use non-historical model which supports cnodes
from corgi.core.models import Component

NEVRA_FIELD = F("nevra")
NOARCH_VALUE = Value(".noarch")

PURL_FIELD = F("purl")
PURL_PREFIX = "pkg:rpm/"
RED_HAT_PURL_PREFIX = f"{PURL_PREFIX}redhat/"


def find_duplicate_rpms(apps, schema_editor) -> None:
    """Find duplicate RPMs / nodes with bad purls, NEVRAs, arches, or namespaces"""
    arches = (
        "ia64",
        "ppc64le",
        "src",
        "noarch",
        "s390x",
        "ppc64",
        "i686",
        "s390",
        "ppc",
        "x86_64",
        "i386",
        "aarch64",
        "",
    )

    # We could do this without the for loop, but checking type / arch pairs individually
    # means the query hits an index and should be faster
    for arch in arches:
        rpms_for_arch = Component.objects.filter(type="RPM", arch=arch)

        # Fix binary RPM arch / nevra values when the RPM doesn't have arch set at all
        if arch == "":
            arch = "noarch"
            # Changing an empty "" string arch to "noarch" might fail
            # due to the type + NVRA constraint, so we handle the "" arch last
            for bad_rpm in rpms_for_arch.iterator():
                # Fixing each RPM individually is less risky than fixing all of them at once
                # Using .filter().update() means this is still atomic
                Component.objects.filter(purl=bad_rpm.purl).update(
                    arch=arch, nevra=functions.Concat(NEVRA_FIELD, NOARCH_VALUE)
                )

        # Fix binary RPM purls which are missing an arch value
        for bad_rpm in rpms_for_arch.exclude(purl__contains="arch=").iterator():
            # PackageURL library alphabetizes qualifiers (by key only), so just put arch key first
            if "?" in bad_rpm.purl:
                good_purl = bad_rpm.purl.replace("?", f"?arch={arch}&", 1)
            else:
                good_purl = f"{bad_rpm.purl}?arch={arch}"
            clean_duplicate_rpms(good_purl, bad_rpm)

        # Fix binary RPM purls which are missing a namespace value
        for bad_rpm in rpms_for_arch.exclude(purl__startswith=RED_HAT_PURL_PREFIX).iterator():
            good_purl = bad_rpm.purl.replace(PURL_PREFIX, RED_HAT_PURL_PREFIX, 1)
            clean_duplicate_rpms(good_purl, bad_rpm)


def clean_duplicate_rpms(good_purl: str, bad_rpm: Component):
    """Clean up binary RPM purls, NEVRAs, nodes, etc. that may or may not have duplicates"""
    # We don't reuse this code in other migrations because we have to make assumptions
    # about what to check and which fields / values to update, based on component type
    good_rpm = Component.objects.filter(purl=good_purl).first()
    # .delete() and .update() functions on querysets are atomic, even outside transactions
    bad_rpm_qs = Component.objects.filter(purl=bad_rpm.purl)

    # No duplicate RPM is present
    # So we can just fix the bad RPM / node purls
    if not good_rpm:
        bad_rpm_qs.update(purl=good_purl)
        bad_rpm.cnodes.update(purl=good_purl)
        return

    # Else both RPMs exist, we assume the old / bad one should just be deleted
    # There's no easy way to know which nodes we should compare / are in the same tree
    # and no easy way to know which build ID should be reprocessed / is the SRPM
    # or if the SRPM even exists at all, since this may be a binary RPM in container trees only
    # So just delete this node without checking descendants or reprocessing
    # There shouldn't be too much data on the old / bad node
    # Which is lost / not present on the duplicate / good node
    bad_rpm_qs.delete()


class Migration(migrations.Migration):
    # Code above should be safe to run outside a transaction / already atomic
    # and will take forever, so we need to pick up where we left off after timeouts
    atomic = False
    dependencies = [
        ("core", "0099_add_type_arch_stored_proc_filter"),
    ]

    operations = [
        migrations.RunPython(find_duplicate_rpms),
    ]
