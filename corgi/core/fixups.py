# This is a temporary mapping of Product Streams, for which we can generate and publish SBOMs.
# This data should be moved to an appropriate object in product definitions as soon as possible.
import re

supported_stream_cpes = {
    # Need to support ET releases in brew_tag matching, see CORGI-737
    "rhes-3.5": {
        "cpe:/a:redhat:storage:3.5:nfs:el7",
        "cpe:/a:redhat:storage:3.5:na:el7",
        "cpe:/a:redhat:storage:3.5:samba:el7",
        "cpe:/a:redhat:storage:3.5:server:el7",
        "cpe:/a:redhat:storage:3.5:wa:el7",
        "cpe:/a:redhat:storage:3.5:nfs:el8",
        "cpe:/a:redhat:storage:3.5:na:el8",
        "cpe:/a:redhat:storage:3.5:samba:el8",
        "cpe:/a:redhat:storage:3.5:server:el8",
        "cpe:/a:redhat:storage:3.5:wa:el8",
    },
    # variant with cpe 'cpe:/a:redhat:rhosemc:1.0::el7' is matched with brew_tags
    "openshift-enterprise-3.11.z": {"cpe:/a:redhat:openshift:3.11::el7"},
    # brew tag matching against 'rhaos-4.12-rhel-8' gives us variants with cpes:
    # 'cpe:/a:redhat:openshift_security_profiles_operator_stable:::el8'
    # 'cpe:/a:redhat:openshift_file_integrity_operator:1.0::el8'
    # 'cpe:/a:redhat:openshift_compliance_operator:1::el8'
    "openshift-4.12.z": {
        "cpe:/a:redhat:openshift:4.12::el8",
        "cpe:/a:redhat:openshift:4.12::el9",
        "cpe:/a:redhat:openshift_ironic:4.12::el9",
    },
    # doesn't match with pattern because stream has no version
    "openstack-13-optools": {"cpe:/a:redhat:openstack-optools:13::el7"},
    # All these streams use a single variant, see PROJQUAY-5312
    "quay-3.6": {"cpe:/a:redhat:quay:3::el8"},
    "quay-3.7": {"cpe:/a:redhat:quay:3::el8"},
    "quay-3.8": {"cpe:/a:redhat:quay:3::el8"},
}

supported_stream_pattern_cpes = (
    # No way to match cpes for composes
    (
        r"^rhel-8[\d\.]+[^z]$",
        {
            "cpe:/a:redhat:enterprise_linux:8::appstream",
            "cpe:/a:redhat:enterprise_linux:8::crb",
            "cpe:/a:redhat:enterprise_linux:8::highavailability",
            "cpe:/a:redhat:enterprise_linux:8::nfv",
            "cpe:/a:redhat:enterprise_linux:8::realtime",
            "cpe:/a:redhat:enterprise_linux:8::resilientstorage",
            "cpe:/a:redhat:enterprise_linux:8::sap",
            "cpe:/a:redhat:enterprise_linux:8::sap_hana",
            "cpe:/a:redhat:enterprise_linux:8::supplementary",
            "cpe:/o:redhat:enterprise_linux:8::baseos",
            "cpe:/o:redhat:enterprise_linux:8::fastdatapath",
            "cpe:/o:redhat:enterprise_linux:8::hypervisor",
        },
    ),
    (
        r"^rhel-9[\d\.]+[^z]$",
        {
            "cpe:/a:redhat:enterprise_linux:9::appstream",
            "cpe:/a:redhat:enterprise_linux:9::crb",
            "cpe:/a:redhat:enterprise_linux:9::highavailability",
            "cpe:/a:redhat:enterprise_linux:9::nfv",
            "cpe:/a:redhat:enterprise_linux:9::realtime",
            "cpe:/a:redhat:enterprise_linux:9::resilientstorage",
            "cpe:/a:redhat:enterprise_linux:9::sap",
            "cpe:/a:redhat:enterprise_linux:9::sap_hana",
            "cpe:/a:redhat:enterprise_linux:9::supplementary",
            "cpe:/o:redhat:enterprise_linux:9::baseos",
            "cpe:/o:redhat:enterprise_linux:9::fastdatapath",
            "cpe:/o:redhat:enterprise_linux:9::hypervisor",
        },
    ),
)


def cpe_lookup(product_stream_name: str) -> set[str]:
    """Manual CPE overrides for streams which cannot be matched with variants"""
    if product_stream_name in supported_stream_cpes:
        return supported_stream_cpes[product_stream_name]
    for pattern, cpes in supported_stream_pattern_cpes:
        if re.search(pattern, product_stream_name):
            return cpes
    return set()


external_names = {
    # Can't parse the stream version properly due to the version not being after the last dash
    "openstack-13-els": "RHEL-7-OS-13-ELS",
}


def external_name_lookup(product_stream_name: str) -> str:
    stream_prefixes = ("dts-", "rhel-br-", "mtr-", "quay-")
    # 'dts' streams share variants with 'rhscl', however the component sets are different
    # 'rhel-br' streams share variants with 'rhel' however the components sets are different
    # 'mtr-' streams share the 'RHEL-8-MTR-1' variant, but have distinct brew_tags
    # 'quay-' streams share the 'QUAY-3-RHEL-8' variant, but have distinct brew_tags
    for stream_prefix in stream_prefixes:
        if product_stream_name.lower().startswith(stream_prefix):
            return product_stream_name.upper()
    return external_names.get(product_stream_name, "")
