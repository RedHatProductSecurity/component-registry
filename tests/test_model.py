import pytest
from django.apps import apps
from django.db.utils import IntegrityError, ProgrammingError
from packageurl import PackageURL

from corgi.core.constants import CONTAINER_DIGEST_FORMATS
from corgi.core.models import (
    Component,
    ComponentNode,
    ComponentQuerySet,
    Product,
    ProductComponentRelation,
    ProductNode,
    ProductStream,
    ProductVersion,
    SoftwareBuild,
)

from .factories import (
    ComponentFactory,
    ContainerImageComponentFactory,
    ProductComponentRelationFactory,
    ProductFactory,
    ProductStreamFactory,
    ProductVariantFactory,
    ProductVersionFactory,
    SoftwareBuildFactory,
    SrpmComponentFactory,
)

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


def test_product_model():
    p1 = ProductFactory(name="RHEL")
    assert Product.objects.get(name="RHEL") == p1
    assert p1.ofuri == "o:redhat:RHEL"


def test_productversion_model():
    p1 = ProductVersionFactory(name="RHEL")
    assert ProductVersion.objects.get(name="RHEL") == p1
    assert p1.ofuri == "o:redhat:RHEL:8"


def test_productstream_model():
    p1 = ProductStreamFactory(name="RHEL")
    assert ProductStream.objects.get(name="RHEL") == p1
    assert p1.ofuri == "o:redhat:RHEL:8.2.0.z"
    assert p1.cpe == "cpe:/o:redhat:enterprise_linux:8"


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_cpes():
    p1 = ProductFactory(name="RHEL")
    pv1 = ProductVersionFactory(name="RHEL-7", version="7", products=p1)
    pv2 = ProductVersionFactory(name="RHEL-8", version="8", products=p1)
    ps1 = ProductStreamFactory(name="Ansercanagicus", products=p1, productversions=pv1)
    ps2 = ProductStreamFactory(name="Ansercaerulescens", products=p1, productversions=pv2)
    ps3 = ProductStreamFactory(
        name="Brantabernicla",
        cpe="cpe:/o:redhat:Brantabernicla:8",
        description="The brant or brent goose is a small goose of the genus Branta.",
        products=p1,
        productversions=pv2,
    )

    p1node = ProductNode.objects.create(parent=None, obj=p1)
    assert p1node
    pv1node = ProductNode.objects.create(parent=p1node, obj=pv1)
    assert pv1node
    pv2node = ProductNode.objects.create(parent=p1node, obj=pv2)
    assert pv2node
    ps1node = ProductNode.objects.create(parent=pv1node, obj=ps1)
    assert ps1node
    ps2node = ProductNode.objects.create(parent=pv2node, obj=ps2)
    assert ps2node
    ps3node = ProductNode.objects.create(parent=pv2node, obj=ps3)
    assert ps3node

    assert ps3node.name == ps3.name
    assert ps3node.desc == ps3.description

    assert pv1.cpes == ("cpe:/o:redhat:enterprise_linux:8",)
    assert sorted(p1.cpes) == [
        "cpe:/o:redhat:Brantabernicla:8",
        "cpe:/o:redhat:enterprise_linux:8",
    ]


def test_nevra():
    for component_type in Component.Type.values:
        no_release = ComponentFactory(
            name=component_type, release="", version="1", type=component_type
        )
        assert not no_release.nvr.endswith("-")
        package_url = PackageURL.from_string(no_release.purl)
        # container images get their purl version from the digest value, not version & release
        if component_type == Component.Type.CONTAINER_IMAGE:
            assert not package_url.qualifiers["tag"].endswith("-")
        else:
            assert not package_url.version.endswith("-")
        # epoch is a property of Component which retrieves the value for meta_attr
        no_epoch_or_arch = ComponentFactory(name=component_type, arch="", type=component_type)
        assert ":" not in no_epoch_or_arch.nevra
        assert not no_epoch_or_arch.nevra.endswith("-")


def test_product_taxonomic_queries():
    rhel, rhel_7, _, rhel_8, _, rhel_8_2, _ = create_product_hierarchy()

    assert sorted(rhel.productstreams.values_list("name", flat=True)) == [
        "rhel-7.1",
        "rhel-8.1",
        "rhel-8.2",
    ]
    assert sorted(rhel_8.productstreams.values_list("name", flat=True)) == ["rhel-8.1", "rhel-8.2"]
    assert rhel_7.products.name == rhel_8_2.products.name == "RHEL"


def create_product_hierarchy():
    # TODO: Factory should probably create nodes for model
    #  Move these somewhere for reuse - common.py helper method, or fixtures??
    rhel = ProductFactory(name="RHEL")
    rhel_node = ProductNode.objects.create(parent=None, obj=rhel)

    rhel_7 = ProductVersionFactory(name="rhel-7", version="7", products=rhel)
    rhel_7_node = ProductNode.objects.create(parent=rhel_node, obj=rhel_7)

    rhel_7_1 = ProductStreamFactory(name="rhel-7.1", products=rhel, productversions=rhel_7)
    ProductNode.objects.create(parent=rhel_7_node, obj=rhel_7_1)

    rhel_8 = ProductVersionFactory(name="rhel-8", version="8", products=rhel)
    rhel_8_node = ProductNode.objects.create(parent=rhel_node, obj=rhel_8)

    rhel_8_1 = ProductStreamFactory(name="rhel-8.1", products=rhel, productversions=rhel_8)
    ProductNode.objects.create(parent=rhel_8_node, obj=rhel_8_1)

    rhel_8_2 = ProductStreamFactory(
        name="rhel-8.2", cpe="cpe:/o:redhat:8.2", products=rhel, productversions=rhel_8
    )
    rhel_8_2_node = ProductNode.objects.create(parent=rhel_8_node, obj=rhel_8_2)

    rhel_8_2_base = ProductVariantFactory(
        name="Base8-test", products=rhel, productversions=rhel_8, productstreams=rhel_8_2
    )
    ProductNode.objects.create(parent=rhel_8_2_node, obj=rhel_8_2_base)

    return rhel, rhel_7, rhel_7_1, rhel_8, rhel_8_1, rhel_8_2, rhel_8_2_base


def test_component_model():
    c1 = SrpmComponentFactory(name="curl")
    assert Component.objects.get(name="curl") == c1


def test_container_purl():
    # TODO: Failed due to "assert arch not in purl"
    container = ContainerImageComponentFactory()
    # When a container doesn't get a digest meta_attr
    assert "@" not in container.purl
    example_digest = "sha256:blah"
    container.meta_attr = {"digests": {CONTAINER_DIGEST_FORMATS[0]: example_digest}}
    container.save()
    assert example_digest in container.purl
    assert "arch" not in container.purl
    assert container.name in container.purl
    example_digest = "sha256:blah"
    repo_name = "node-exporter-rhel8"
    repository_url = f"registry.redhat.io/rhacm2/{repo_name}"
    container.meta_attr = {
        "digests": {CONTAINER_DIGEST_FORMATS[0]: example_digest},
        "repository_url": repository_url,
        "name_from_label": repo_name,
    }
    container.arch = "x86_64"
    container.save()
    assert example_digest in container.purl
    assert "x86_64" in container.purl
    assert repo_name in container.purl.split("?")[0]
    assert repository_url in container.purl


def test_component_provides():
    upstream = ComponentFactory(name="upstream", namespace=Component.Namespace.UPSTREAM)
    upstream_node, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=upstream.purl,
        defaults={"obj": upstream},
    )
    dev_comp = ComponentFactory(name="dev", type=Component.Type.NPM)
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES_DEV,
        parent=upstream_node,
        purl=dev_comp.purl,
        defaults={"obj": dev_comp},
    )
    # provides is inverse of sources
    # so calling save_component_taxonomy on either dev_comp or upstream
    # works the same way - the two components will be linked together
    dev_comp.save_component_taxonomy()
    assert upstream.provides.filter(purl=dev_comp.purl).exists()


@pytest.mark.parametrize("build_type", SoftwareBuild.Type.values)
def test_software_build_model(build_type):
    sb1 = SoftwareBuildFactory(
        build_type=build_type,
        meta_attr={"build_id": 9999, "a": 1, "b": 2, "brew_tags": ["RHSA-123-123"]},
    )
    assert SoftwareBuild.objects.get(build_id=sb1.build_id, build_type=build_type) == sb1
    c1 = ComponentFactory(type=Component.Type.RPM, name="curl", software_build=sb1)
    assert Component.objects.get(name="curl") == c1


def test_get_roots():
    srpm = SrpmComponentFactory(name="srpm")
    srpm_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=srpm.purl,
        defaults={"obj": srpm},
    )
    rpm = ComponentFactory(name="rpm", type=Component.Type.RPM)
    rpm_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=srpm_cnode,
        purl=rpm.purl,
        defaults={"obj": rpm},
    )
    assert rpm.get_roots(using="default") == [srpm_cnode]
    assert srpm.get_roots(using="default") == [srpm_cnode]

    nested = ComponentFactory(name="nested")
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=rpm_cnode,
        purl=nested.purl,
        defaults={"obj": nested},
    )
    assert nested.get_roots(using="default") == [srpm_cnode]

    container = ContainerImageComponentFactory(name="container")
    container_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=container.purl,
        defaults={"obj": container},
    )
    container_rpm = ComponentFactory(name="container_rpm", type=Component.Type.RPM)
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_cnode,
        purl=container_rpm.purl,
        defaults={"obj": container_rpm},
    )
    assert not container_rpm.get_roots(using="default")
    assert container.get_roots(using="default") == [container_cnode]

    container_source = ComponentFactory(
        name="container_source", namespace=Component.Namespace.UPSTREAM, type=Component.Type.GITHUB
    )
    container_source_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=container_cnode,
        purl=container_source.purl,
        defaults={"obj": container_source},
    )
    assert container_source.get_roots(using="default") == [container_cnode]
    container_nested = ComponentFactory(name="container_nested", type=Component.Type.NPM)
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_source_cnode,
        purl=container_nested.purl,
        defaults={"obj": container_nested},
    )
    assert container_nested.get_roots(using="default") == [container_cnode]


def test_product_component_relations():
    build_id = 1754635
    sb = SoftwareBuildFactory(build_id=build_id)
    _, _, rhel_7_1, _, _, _, _ = create_product_hierarchy()
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.COMPOSE,
        product_ref=rhel_7_1.name,
        build_id=build_id,
        build_type=sb.build_type,
    )
    srpm = SrpmComponentFactory(software_build=sb)
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=srpm.purl, obj=srpm
    )
    sb.save_product_taxonomy()
    c = Component.objects.get(uuid=srpm.uuid)
    assert rhel_7_1 in c.productstreams.get_queryset()


def test_product_component_relations_errata():
    build_id = 1754635
    sb = SoftwareBuildFactory(build_id=build_id)
    _, _, _, _, _, rhel_8_2, rhel_8_2_base = create_product_hierarchy()
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.ERRATA,
        product_ref=rhel_8_2_base.name,
        build_id=build_id,
        build_type=sb.build_type,
    )
    srpm = SrpmComponentFactory(software_build=sb)
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=srpm.purl, obj=srpm
    )
    sb.save_product_taxonomy()
    c = Component.objects.get(uuid=srpm.uuid)
    assert rhel_8_2 in c.productstreams.get_queryset()


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_product_stream_builds():
    rhel_8_2_build = SoftwareBuildFactory()
    rhel_8_2_base_build = SoftwareBuildFactory()
    rhel_7_1_build = SoftwareBuildFactory()
    rhel, rhel_7, rhel_7_1, _, rhel_8_1, rhel_8_2, rhel_8_2_base = create_product_hierarchy()
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.COMPOSE,
        # This is a product stream ref
        product_ref=rhel_8_2.name,
        build_id=rhel_8_2_build.build_id,
    )
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.CDN_REPO,
        # This is a product variant ref, and also a child of rhel_8_2 stream
        product_ref=rhel_8_2_base.name,
        build_id=rhel_8_2_base_build.build_id,
    )
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.BREW_TAG,
        # This is a product stream ref only
        product_ref=rhel_7_1.name,
        build_id=rhel_7_1_build.build_id,
    )
    # Test we can find product variant builds
    rhel_8_2_builds = [b_id for b_id, _ in rhel_8_2.builds]
    assert rhel_8_2_build.build_id in rhel_8_2_builds
    # Test we can find both product variant and product stream builds when they are ancestors
    assert rhel_8_2_base_build.build_id in rhel_8_2_builds
    # Test we can find product stream builds
    assert rhel_7_1_build.build_id in [b_id for b_id, _ in rhel_7_1.builds]
    # Test we can find builds from product stream children of product version
    assert rhel_7_1_build.build_id in [b_id for b_id, _ in rhel_7.builds]
    # Test products have all builds
    rhel_builds = [b_id for b_id, _ in rhel.builds]
    assert rhel_8_2_build.build_id in rhel_builds
    assert rhel_7_1_build.build_id in rhel_builds
    assert rhel_8_2_base_build.build_id in rhel_builds
    # Test that builds from another stream don't get included
    assert rhel_8_2_build.build_id not in [b for b in rhel_8_1.builds]


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_component_errata():
    sb = SoftwareBuildFactory()
    c = ComponentFactory(software_build=sb)
    ProductComponentRelationFactory(
        build_id=sb.build_id,
        build_type=sb.build_type,
        external_system_id="RHSA-1",
        type=ProductComponentRelation.Type.ERRATA,
    )
    assert "RHSA-1" in c.errata


def test_get_upstream():
    srpm = SrpmComponentFactory(name="srpm")
    srpm_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=srpm.purl,
        defaults={"obj": srpm},
    )
    rpm = ComponentFactory(name="rpm", type=Component.Type.RPM)
    rpm_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=srpm_cnode,
        purl=rpm.purl,
        defaults={"obj": rpm},
    )
    srpm_upstream = ComponentFactory(name="srpm_upstream", namespace=Component.Namespace.UPSTREAM)
    srpm_upstream_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=srpm_cnode,
        purl=srpm_upstream.purl,
        defaults={"obj": srpm_upstream},
    )
    assert sorted(rpm.get_upstreams_purls(using="default")) == [srpm_upstream.purl]


def test_get_upstream_container():
    container = ContainerImageComponentFactory(name="container")
    container_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=container.purl,
        defaults={"obj": container},
    )
    container_rpm = ComponentFactory(name="container_rpm", type=Component.Type.RPM)
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_cnode,
        purl=container_rpm.purl,
        defaults={"obj": container_rpm},
    )
    assert container_rpm.get_upstreams_purls(using="default") == set()

    container_source = ContainerImageComponentFactory(name="container_source")
    container_source_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=container_cnode,
        purl=container_source.purl,
        defaults={"obj": container_source},
    )

    container_nested = ComponentFactory(name="container_nested", type=Component.Type.NPM)
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_source_cnode,
        purl=container_source.purl,
        defaults={"obj": container_nested},
    )
    assert sorted(container_nested.get_upstreams_purls(using="default")) == [container_source.purl]

    container_o_source = ComponentFactory(
        name="contain_upstream_other",
        type=Component.Type.PYPI,
        namespace=Component.Namespace.UPSTREAM,
    )
    container_o_source_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=container_cnode,
        purl=container_o_source.purl,
        defaults={"obj": container_o_source},
    )

    container_other_nested = ComponentFactory(
        name="container_nested_other", type=Component.Type.NPM
    )
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_o_source_cnode,
        purl=container_other_nested.purl,
        defaults={"obj": container_other_nested},
    )
    assert sorted(container_other_nested.get_upstreams_purls(using="default")) == [
        container_o_source.purl
    ]


def test_purl2url():
    release = "Must_be_removed_from_every_purl_before_building_URL"
    component = ComponentFactory(type=Component.Type.RPM)
    assert component.download_url == Component.RPM_PACKAGE_BROWSER

    component = ComponentFactory(type=Component.Type.CONTAINER_IMAGE)
    assert component.download_url == Component.CONTAINER_CATALOG_SEARCH
    assert not component.related_url
    component.related_url = "registry.redhat.io/openshift3/grafana"
    component.save()
    assert (
        component.download_url == f"{component.related_url}:{component.version}-{component.release}"
    )

    component = ComponentFactory(
        namespace=Component.Namespace.REDHAT, type=Component.Type.GEM, release=release
    )
    assert component.download_url == (
        f"https://rubygems.org/downloads/{component.name}-{component.version}.gem"
    )
    assert component.related_url == (
        f"https://rubygems.org/gems/{component.name}/versions/{component.version}"
    )

    component = ComponentFactory(
        namespace=Component.Namespace.REDHAT,
        name="user/repo",
        type=Component.Type.GENERIC,
        release=release,
    )
    assert component.download_url == ""
    assert component.related_url == ""
    orig_name = component.name

    component.name = f"github.com/{orig_name}"
    component.save()
    assert component.download_url == f"https://{component.name}/archive/{component.version}.zip"
    assert component.related_url == f"https://{component.name}/tree/{component.version}"

    component.name = f"git@github.com:{orig_name}"
    component.save()
    assert (
        component.download_url == f"https://github.com/{orig_name}/archive/{component.version}.zip"
    )
    assert component.related_url == f"https://github.com/{orig_name}/tree/{component.version}"

    component = ComponentFactory(
        namespace=Component.Namespace.REDHAT,
        name="USER/REPO",
        type=Component.Type.GITHUB,
        release=release,
    )
    assert (
        component.download_url
        == f"https://github.com/{component.name.lower()}/archive/{component.version}.zip"
    )
    assert (
        component.related_url
        == f"https://github.com/{component.name.lower()}/tree/{component.version}"
    )

    # semantic version
    # name with namespace component
    component = ComponentFactory(
        type=Component.Type.GOLANG,
        namespace=Component.Namespace.REDHAT,
        name="4d63.com/gochecknoglobals",
        version="v3.0.0",
        release=release,
    )
    assert (
        component.download_url
        == f"https://proxy.golang.org/{component.name}/@v/{component.version}.zip"
    )
    assert component.related_url == f"https://pkg.go.dev/{component.name}@{component.version}"

    # no namespace
    component = ComponentFactory(
        type=Component.Type.GOLANG,
        namespace=Component.Namespace.REDHAT,
        name="gochecknoglobals",
        version="v3.0.0",
        release=release,
    )
    assert component.download_url == ""
    assert component.related_url == ""

    # pseudo version
    component = ComponentFactory(
        type=Component.Type.GOLANG,
        namespace=Component.Namespace.REDHAT,
        name="github.com/14rcole/gopopulate",
        version="v0.0.0-20180821133914-b175b219e774",
        release=release,
    )
    assert (
        component.download_url == "https://github.com/14rcole/gopopulate/archive/b175b219e774.zip"
    )
    assert component.related_url == "https://github.com/14rcole/gopopulate/tree/b175b219e774"

    # pseudo version with namespace length of 3
    component = ComponentFactory(
        type=Component.Type.GOLANG,
        namespace=Component.Namespace.REDHAT,
        name="github.com/3scale/3scale-operator/controllers/capabilities",
        version="v0.10.1-0.20221206164259-31a0ef8b04df",
        release=release,
    )
    assert (
        component.download_url
        == "https://github.com/3scale/3scale-operator/archive/31a0ef8b04df.zip"
    )
    assert (
        component.related_url
        == f"https://github.com/3scale/3scale-operator/tree/{component.version.split('-')[-1]}"
    )

    # semantic version
    component = ComponentFactory(
        type=Component.Type.GOLANG,
        namespace=Component.Namespace.REDHAT,
        version="1.18.0",
        name="github.com/18F/hmacauth",
        release=release,
    )
    assert (
        component.download_url
        == f"https://{component.name.lower()}/archive/{component.version}.zip"
    )
    assert component.related_url == f"https://{component.name.lower()}/tree/{component.version}"

    # +incompatible in version
    component = ComponentFactory(
        type=Component.Type.GOLANG,
        namespace=Component.Namespace.REDHAT,
        name="github.com/Azure/azure-sdk-for-go/services/dns/mgmt/2016-04-01/dns",
        version="v51.2.0+incompatible",
        release=release,
    )
    assert component.download_url == "https://github.com/azure/azure-sdk-for-go/archive/v51.2.0.zip"
    assert component.related_url == "https://github.com/azure/azure-sdk-for-go/tree/v51.2.0"

    component = ComponentFactory(
        type=Component.Type.MAVEN,
        namespace=Component.Namespace.REDHAT,
        name="io.vertx/vertx-grpc",
        version="4.3.7.redhat-00002",
        release=release,
    )
    assert (
        component.download_url
        == f"https://maven.repository.redhat.com/ga/io/vertx/vertx-grpc/{component.version}"
    )
    assert (
        component.related_url
        == f"https://mvnrepository.com/artifact/{component.name}/{component.version}"
    )

    # maven with group_id
    component = ComponentFactory(
        type=Component.Type.MAVEN,
        namespace=Component.Namespace.REDHAT,
        meta_attr={"group_id": "io.prestosql.benchto"},
        name="benchto-driver",
        version="0.7",
        release=release,
    )
    assert (
        component.download_url == "https://repo1.maven.org/maven2/io/prestosql/benchto/"
        "benchto-driver/0.7"
    )
    assert (
        component.related_url == "https://mvnrepository.com/artifact/io.prestosql.benchto/"
        "benchto-driver/0.7"
    )

    # maven with classifier and type
    component = ComponentFactory(
        type=Component.Type.MAVEN,
        namespace=Component.Namespace.REDHAT,
        meta_attr={"classifier": "noapt", "type": "jar", "group_id": "io.dekorate"},
        name="knative-annotations",
        version="2.11.3.redhat-00001",
        release=release,
    )

    assert (
        component.download_url == "https://maven.repository.redhat.com/ga/"
        f"{component.meta_attr['group_id'].replace('.', '/')}/"
        f"{component.name}/{component.version}/"
        f"{component.name}-{component.version}"
        f"-{component.meta_attr['classifier']}.{component.meta_attr['type']}"
    )
    assert (
        component.related_url == f"https://mvnrepository.com/artifact/"
        f"{component.meta_attr['group_id']}/"
        f"{component.name}/{component.version}"
    )

    # empty version
    component = ComponentFactory(
        namespace=Component.Namespace.REDHAT,
        type=Component.Type.MAVEN,
        name="test",
        version="",
        release=release,
    )
    assert component.download_url == ""
    assert component.related_url == ""

    # pypi component
    component = ComponentFactory(
        namespace=Component.Namespace.REDHAT,
        type=Component.Type.PYPI,
        name="aiohttp",
        version="3.6.2",
        release=release,
    )

    assert (
        component.download_url == "https://pypi.io/packages/source/a/aiohttp/"
        "aiohttp-3.6.2.tar.gz"
    )
    assert component.related_url == "https://pypi.org/project/aiohttp/3.6.2/"


def test_duplicate_insert_fails():
    """Test that DB constraints block inserting nodes with same (type, parent, purl)"""
    component = ComponentFactory()
    root = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl="root", obj=component
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=root, purl="child", obj=component
    )
    with pytest.raises(IntegrityError):
        # Inserting the same node a second time should fail with an IntegrityError
        ComponentNode.objects.create(
            type=ComponentNode.ComponentNodeType.SOURCE, parent=root, purl="child", obj=component
        )


def test_duplicate_insert_fails_for_null_parent():
    """Test that DB constraints block inserting nodes with same (type, parent=None, purl)"""
    component = ComponentFactory()
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl="root", obj=component
    )
    with pytest.raises(IntegrityError):
        # Inserting the same node a second time should fail with an IntegrityError
        ComponentNode.objects.create(
            type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl="root", obj=component
        )


def test_querysets_are_unordered_by_default():
    """For all models, assert that the model Meta doesn't define an ordering
    Also assert that the base .objects queryset does not contain SQL to sort the objects
    """
    for model in apps.get_app_config("core").get_models():
        assert model._meta.ordering == []
        assert "Sort " not in model.objects.get_queryset().explain()


def test_mptt_properties_are_unordered_by_default():
    """Assert that MPTT model properties (pnodes, cnodes) don't define an ordering"""

    c = ComponentFactory()
    cnode = ComponentNode(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=c.purl, obj=c
    )
    cnode.save()
    assert "Sort " not in c.cnodes.explain()
    assert cnode._meta.ordering == []
    assert c._meta.ordering == []

    p = ProductFactory()
    pnode = ProductNode(parent=None, obj=p)
    pnode.save()
    assert "Sort " not in p.pnodes.explain()
    assert pnode._meta.ordering == []
    assert p._meta.ordering == []


def test_queryset_ordering_succeeds():
    """Test that .distinct() without field name or order_by() works correctly
    Also test that .distinct() with field name or order_by() works correctly
    """
    # Ordered by version: (a, a), (c, c), (first_b, b), (last_b, b)
    # Ordered by description: (a, a), (first_b, b), (last_b, b), (c, c)
    ProductFactory(name="a", version="a", description="a")
    ProductFactory(name="b", version="first_b", description="b")
    ProductFactory(name="c", version="last_b", description="b")
    ProductFactory(name="d", version="c", description="c")

    # .values_list("description", flat=True).distinct() will succeed, with any of:
    # nothing, .order_by(), or .order_by("description") in front of .values_list

    # no order_by at all inherits any ordering fields the model Meta defines (None)
    unordered_products = Product.objects.get_queryset()
    assert len(unordered_products.values_list("description", flat=True).distinct()) == 3

    # .order_by() removes any ordering field from the result queryset
    ordered_products = unordered_products.order_by()
    assert len(ordered_products.values_list("description", flat=True).distinct()) == 3

    # .order_by("description") adds "description" ordering field to the result queryset
    ordered_products = unordered_products.order_by("description")
    assert len(ordered_products.values_list("description", flat=True).distinct()) == 3


def test_queryset_ordering_fails():
    """Test that .distinct() with non-matching ordering fails the way we expect
    Also test that .distinct("field") with non-matching ordering fails the way we expect
    """
    ProductFactory(name="a", version="a", description="a")
    ProductFactory(name="b", version="first_b", description="b")
    ProductFactory(name="c", version="last_b", description="b")
    ProductFactory(name="d", version="c", description="c")

    # .order_by("version").values_list("description", flat=True).distinct() will fail
    # because order_by("version") adds a hidden field to the result queryset we're SELECTing
    # .distinct() looks at (order_by field, values_list field) together when checking duplicates
    unordered_products = Product.objects.get_queryset()

    # .distinct() with no field name looks at both "description" field from values_list
    # and "version" field from order_by, even though only "description" is in the final result
    misordered_products = unordered_products.order_by("version")
    assert len(misordered_products.values_list("description", flat=True).distinct()) == 4

    # SELECT DISTINCT ON expressions must match initial ORDER BY expressions
    with pytest.raises(ProgrammingError):
        len(misordered_products.distinct("description"))


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_filter_latest_nevra_by_distinct_component():
    ps = ProductStreamFactory(name="rhel-7.9.z")
    srpm_with_el = SrpmComponentFactory(name="sdb", version="1.2.1", release="21.el7")
    srpm_with_el.productstreams.add(ps)
    srpm = SrpmComponentFactory(name="sdb", version="1.2.1", release="3")
    srpm.productstreams.add(ps)
    assert (
        ps.components.filter_latest_nevra_by_distinct_component(
            srpm_with_el.namespace, srpm_with_el.name, srpm_with_el.arch
        )
        == srpm_with_el.nevra
    )

    # test no result
    ps = ProductStreamFactory(name="rhel-7.7.z")
    assert not ps.components.filter_latest_nevra_by_distinct_component(
        srpm_with_el.namespace, srpm_with_el.name, srpm_with_el.arch
    )


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_filter_latest_nevra_by_distinct_component_modular():
    ps = ProductStreamFactory(name="certificate_system-10.2.z")
    modular_rpm_1 = SrpmComponentFactory(
        name="idm-console-framework", version="1.2.0", release="3.module+el8pki+7130+225b0dd0"
    )
    modular_rpm_1.productstreams.add(ps)
    modular_rpm_2 = SrpmComponentFactory(
        name="idm-console-framework", version="1.2.0", release="3.module+el8pki+8580+f0d97d6d"
    )
    modular_rpm_2.productstreams.add(ps)
    assert (
        ps.components.filter_latest_nevra_by_distinct_component(
            modular_rpm_2.namespace, modular_rpm_2.name, modular_rpm_2.arch
        )
        == modular_rpm_2.nevra
    )


def test_ensure_epoch():
    component_qs = ComponentQuerySet()
    nevra = (
        "",
        None,
        "1.2.0",
        "3.module+el8pki+7130+225b0dd0",
    )
    result = component_qs._ensure_epoch(nevra)
    assert result[0] == "0"
    assert result[1] == "1.2.0"
    assert result[2] == "3.module+el8pki+7130+225b0dd0"

    nevra = (
        "",
        "",
        "",
        "",
    )
    result = component_qs._ensure_epoch(nevra)
    assert result[0] == "0"

    nevra = (
        "",
        "1",
        "",
        "",
    )
    result = component_qs._ensure_epoch(nevra)
    assert result[0] == "1"

    nevra = (
        "",
        0,
        "",
        "",
    )
    result = component_qs._ensure_epoch(nevra)
    assert result[0] == "0"

    nevra = (
        "",
        1,
        "",
        "",
    )
    result = component_qs._ensure_epoch(nevra)
    assert result[0] == "1"


def test_version_release_arr_el_match():
    c = ComponentFactory(
        arch="src", type=Component.Type.RPM, release="2.Final_redhat_2.1.ep6.el7", version="1.2_3"
    )
    assert c.release_arr == ["2", "Final", "redhat", "2", "1", "ep6", "el7"]
    assert c.version_arr == ["1", "2", "3"]
    assert c.el_match == ["7"]
    c = ComponentFactory(
        arch="src",
        type=Component.Type.RPM,
        release="2.module+el8.2.0+4938+c0cffa5b",
        version="1-2.3",
    )
    assert c.release_arr == ["2", "module", "el8", "2", "0", "4938", "c0cffa5b"]
    assert c.version_arr == ["1", "2", "3"]
    assert c.el_match == ["8", "2", "0", "+4938+c0cffa5b"]
    c = ComponentFactory(
        arch="noarch", type=Component.Type.CONTAINER_IMAGE, release="2.4.4.1.el5_10", version="2"
    )
    assert c.release_arr == ["2", "4", "4", "1", "el5", "10"]
    assert c.version_arr == ["2"]
    assert c.el_match == ["5", "10"]

    c = ComponentFactory(
        arch="src",
        type=Component.Type.RPM,
        release="14.module+el9.1.0+16330+91eb0817",
        version="blue_elephant",
    )
    assert c.release_arr == ["14", "module", "el9", "1", "0", "16330", "91eb0817"]
    assert c.version_arr == ["blue", "elephant"]
    assert c.el_match == ["9", "1", "0", "+16330+91eb0817"]

    # negative test eg. only noarch OCI or src RPM get this treatment
    c = ComponentFactory(type=Component.Type.GOLANG, release="2.ep6.el7", version="1.2_3")
    assert c.release_arr == []
    assert c.version_arr == []
    assert c.el_match == []
