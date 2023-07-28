# This is a temporary mapping of Product Streams, for which we can generate and publish SBOMs.
# This data should be moved to an appropriate object in product definitions as soon as possible.

supported_stream_cpes = {
    # certificate_system should be resolved by product-definitions/-/merge_requests/2204
    "certificate_system_10.2.z": [
        "cpe:/a:redhat:certificate_system_eus:10.2::el8",
        "cpe:/a:redhat:certificate_system:10.2::el8",
    ],
    "certificate_system_10.4.z": ["cpe:/a:redhat:certificate_system:10.4::el8"],
    # convert2rhel should be resolved by product-definitions/-/merge_requests/2204
    "convert2rhel-7": ["cpe:/a:redhat:convert2rhel::el7"],
    "convert2rhel-8": ["cpe:/a:redhat:convert2rhel::el8"],
    # directory_server sshould be resolved by product-definitions/-/merge_requests/2204
    "directory_server_11.5": ["cpe:/a:redhat:directory_server:11.5::el8"],
    "directory_server_11.6": ["cpe:/a:redhat:directory_server:11.6::el8"],
    "directory_server_12.0": [
        "cpe:/a:redhat:directory_server_eus:12::el9",
        "cpe:/a:redhat:directory_server:12::el9",
    ],
    "directory_server_12.1": ["cpe:/a:redhat:directory_server:12.1::el9"],
    # DTS shares a variant with rhscl-3 so brew_tag variant linking is skipped in prod_defs task
    "dts-11.1.z": ["cpe:/a:redhat:rhel_software_collections:3::el7"],
    "dts-12.1.z": ["cpe:/a:redhat:rhel_software_collections:3::el7"],
    # Need to support ET releases in brew_tag matching, see CORGI-737
    "rhes-3.5": [
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
    ],
    # variant with cpe 'cpe:/a:redhat:rhosemc:1.0::el7' is matched with brew_tags
    "openshift-enterprise-3.11.z": ["cpe:/a:redhat:openshift:3.11::el7"],
    # brew tag matching against 'rhaos-4.12-rhel-8' gives us variants with cpes:
    # 'cpe:/a:redhat:openshift_security_profiles_operator_stable:::el8'
    # 'cpe:/a:redhat:openshift_file_integrity_operator:1.0::el8'
    # 'cpe:/a:redhat:openshift_compliance_operator:1::el8'
    "openshift-4.12.z": [
        "cpe:/a:redhat:openshift:4.12::el8",
        "cpe:/a:redhat:openshift:4.12::el9",
        "cpe:/a:redhat:openshift_ironic:4.12::el9",
    ],
    # doesn't match with pattern because stream has no version
    "openstack-13-optools": ["cpe:/a:redhat:openstack-optools:13::el7"],
    # All these streams use a single variant, see PROJQUAY-5312
    "quay-3.6": ["cpe:/a:redhat:quay:3::el8"],
    "quay-3.7": ["cpe:/a:redhat:quay:3::el8"],
    "quay-3.8": ["cpe:/a:redhat:quay:3::el8"],
    # No way to match cpes for composes
    "rhel-8.8.0": [
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
    ],
    # No way to match cpes for composes
    "rhel-9.2.0": [
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
    ],
    # Not getting match with pattern matching, excluded from brew_tag matching because it shadows
    # dts-11.0.z
    "rhscl-3.8.z": [
        "cpe:/a:redhat:rhel_software_collections:3::el6",
        "cpe:/a:redhat:rhel_software_collections:3::el7",
    ],
}


def cpe_lookup(product_stream_name: str) -> set[str]:
    """Manual CPE overrides for streams which cannot be matched with variants"""
    return set(supported_stream_cpes.get(product_stream_name, []))
