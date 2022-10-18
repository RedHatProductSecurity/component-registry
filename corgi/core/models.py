import logging
import re
import uuid as uuid
from collections import defaultdict

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres import fields
from django.db import models
from django.db.models import F, OuterRef, Q, QuerySet, Subquery
from mptt.models import MPTTModel, TreeForeignKey
from packageurl import PackageURL

from corgi.core.constants import CONTAINER_DIGEST_FORMATS
from corgi.core.files import ComponentManifestFile, ProductManifestFile
from corgi.core.mixins import TimeStampedModel

logger = logging.getLogger(__name__)


class ProductNode(MPTTModel, TimeStampedModel):
    """Product taxonomy node."""

    parent = TreeForeignKey("self", on_delete=models.CASCADE, null=True, related_name="children")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    obj = GenericForeignKey(
        "content_type",
        "object_id",
    )

    class MPTTMeta:
        level_attr = "level"
        root_node_ordering = False

    class Meta:
        constraints = [
            # Add unique constraint + index so get_or_create behaves atomically
            # Otherwise duplicate rows may be inserted into DB
            # Second constraint needed for case when parent is NULL
            # https://dba.stackexchange.com/a/9760
            models.UniqueConstraint(
                name="unique_pnode_get_or_create",
                fields=("object_id", "parent"),
                condition=models.Q(parent__isnull=False),
            ),
            models.UniqueConstraint(
                name="unique_pnode_get_or_create_for_null_parent",
                fields=("object_id",),
                condition=models.Q(parent__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=("object_id", "parent")),
            # Add index on foreign-key fields here, to speed up iterating over pnodes
            # GenericForeignKey doesn't get these by default, only ForeignKey
            models.Index(fields=("content_type", "object_id")),
        ]

    # Tree-traversal methods for product-related models below.
    #
    # - Each of these methods accepts a single tree node and traverses its ascendants and
    #   descendants looking for a specific type of model. Since our hierarchy is strict (i.e. a
    #   Product will always be an ascendant of a Product Version), we can generalize the MPTT
    #   filter to get_family() and not have to write two different queries for fetching one
    #   node type from two different levels (e.g. get me all ProductVersions that are descendants
    #   of a Product, but also all ProductVersions that are ascendants of a ProductVariant).
    #
    # - Each method spits out all unique "name" attributes of all found node objects.
    #
    # - Each method assumes that an object model will always link to a single ProductNode (thus
    #   the use of `pnodes.first()`).
    #
    # - The `values_list()` query relies on the GenericRelation of each model's pnodes
    #   attribute's related query name.

    @staticmethod
    def get_products(node_model):
        return list(
            node_model.pnodes.first()
            .get_family()
            .filter(content_type=ContentType.objects.get_for_model(Product))
            .values_list("product__name", flat=True)
            .distinct()
        )

    @staticmethod
    def get_product_versions(node_model):
        return list(
            node_model.pnodes.first()
            .get_family()
            .filter(content_type=ContentType.objects.get_for_model(ProductVersion))
            .values_list("product_version__name", flat=True)
            .distinct()
        )

    @staticmethod
    def get_product_streams(node_model):
        return list(
            node_model.pnodes.first()
            .get_family()
            .filter(content_type=ContentType.objects.get_for_model(ProductStream))
            .values_list("product_stream__name", flat=True)
            .distinct()
        )

    @staticmethod
    def get_product_variants(node_model):
        return list(
            node_model.pnodes.first()
            .get_family()
            .filter(content_type=ContentType.objects.get_for_model(ProductVariant))
            .values_list("product_variant__name", flat=True)
            .distinct()
        )

    @staticmethod
    def get_channels(node_model):
        return list(
            node_model.pnodes.first()
            .get_family()
            .filter(content_type=ContentType.objects.get_for_model(Channel))
            .values_list("channel__name", flat=True)
            .distinct()
        )


class ComponentNode(MPTTModel, TimeStampedModel):
    """Component taxonomy node."""

    class ComponentNodeType(models.TextChoices):
        SOURCE = "SOURCE"
        REQUIRES = "REQUIRES"
        PROVIDES = "PROVIDES"  # including bundled provides
        # eg. dev dependencies from Cachito builds
        # https://github.com/containerbuildsystem/cachito/#feature-definitions
        PROVIDES_DEV = "PROVIDES_DEV"

    parent = TreeForeignKey("self", on_delete=models.CASCADE, null=True, related_name="children")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    obj = GenericForeignKey(
        "content_type",
        "object_id",
    )
    # TODO: This shadows built-in name "type" and creates a warning when updating openapi.yml
    type = models.CharField(
        choices=ComponentNodeType.choices, default=ComponentNodeType.SOURCE, max_length=20
    )
    # Saves an expensive django dereference into node object
    purl = models.CharField(max_length=1024, default="")

    class MPTTMeta:
        level_attr = "level"
        root_node_ordering = False

    class Meta:
        constraints = [
            # Add unique constraint + index so get_or_create behaves atomically
            # Otherwise duplicate rows may be inserted into DB
            # Second constraint needed for case when parent is NULL
            # https://dba.stackexchange.com/a/9760
            models.UniqueConstraint(
                name="unique_cnode_get_or_create",
                fields=("type", "parent", "purl"),
                condition=models.Q(parent__isnull=False),
            ),
            models.UniqueConstraint(
                name="unique_cnode_get_or_create_for_null_parent",
                fields=("type", "purl"),
                condition=models.Q(parent__isnull=True),
            ),
        ]
        # 0037_custom_indexes.py contains the following custom performance indexes
        #    core_componentnode_tree_parent_lft_idx
        #    core_cn_tree_lft_purl_parent_idx
        #    core_cn_lft_tree_idx
        #    core_cn_lft_rght_tree_idx
        indexes = [
            models.Index(fields=("type", "parent", "purl")),
            models.Index(fields=["type"]),
            models.Index(fields=["parent"]),
            models.Index(fields=["purl"]),
            # Add index on foreign-key fields here, to speed up iterating over cnodes
            # GenericForeignKey doesn't get these by default, only ForeignKey
            models.Index(fields=("content_type", "object_id")),
        ]

    @property
    def name(self):
        return self.obj.name

    @property
    def desc(self):
        return self.desc.name

    def save(self, *args, **kwargs):
        self.purl = self.obj.purl
        super().save(*args, **kwargs)


class Tag(models.Model):
    name = models.SlugField(
        max_length=200
    )  # Must not be empty; enforced by check constrain in child models.
    value = models.CharField(max_length=1024, default="")

    class Meta:
        abstract = True

    def __str__(self):
        if self.value:
            return f"{self.name}={self.value}"
        return self.name


class SoftwareBuild(TimeStampedModel):
    """Software build model

    This model represents metadata related to the process of building software artifacts
    (components) from a set of source code files.
    """

    class Type(models.TextChoices):
        BREW = "BREW"  # Red Hat Brew build system
        KOJI = "KOJI"  # Fedora's Koji build system, the upstream equivalent of Red Hat's Brew

    build_id = models.IntegerField(primary_key=True)
    type = models.CharField(choices=Type.choices, max_length=20)
    name = models.TextField()  # Arbitrary identifier for a build
    source = models.TextField()  # Source code reference for build
    completion_time = models.DateTimeField(null=True)  # meta_attr["completion_time"]
    # Store meta attributes relevant to different build system types.
    meta_attr = models.JSONField(default=dict)

    class Meta:
        ordering = ["-build_id"]

        indexes = [
            models.Index(fields=["completion_time"]),
        ]

    def save_datascore(self):
        for component in Component.objects.filter(software_build__build_id=self.build_id):
            component.save_datascore()
        return None

    def save_component_taxonomy(self):
        """Note: this function is no longer invoked and may be removed in the future.

        it is only possible to update ('materialize') component taxonomy when all
        components (from a build) have loaded"""
        for component in Component.objects.filter(software_build__build_id=self.build_id):
            for cnode in component.cnodes.get_queryset():
                for d in cnode.get_descendants(include_self=True):
                    d.obj.save_component_taxonomy()
        return None

    def save_product_taxonomy(self):
        """update ('materialize') product taxonomy on all build components"""
        variant_ids = list(
            ProductComponentRelation.objects.filter(build_id=self.build_id)
            .order_by("build_id")
            .filter(
                type__in=(
                    ProductComponentRelation.Type.CDN_REPO,
                    ProductComponentRelation.Type.ERRATA,
                )
            )
            .distinct()
            .values_list("product_ref", flat=True)
        )

        stream_ids = list(
            ProductComponentRelation.objects.filter(build_id=self.build_id)
            .order_by("build_id")
            .filter(
                type__in=(
                    ProductComponentRelation.Type.BREW_TAG,
                    ProductComponentRelation.Type.COMPOSE,
                    ProductComponentRelation.Type.YUM_REPO,
                )
            )
            .distinct()
            .values_list("product_ref", flat=True)
        )

        product_details = get_product_details(variant_ids, stream_ids)

        components = set()
        for component in Component.objects.filter(software_build__build_id=self.build_id):
            # This is needed for container image builds which pull in components not
            # built at Red Hat, and therefore not assigned a build_id
            for d in component.cnodes.get_queryset().get_descendants(include_self=True):
                if not d.obj:
                    continue
                components.add(d.obj)

        for component in list(components):
            for attr in ("products", "product_versions", "product_streams"):
                # Since we're only setting the product details for a specific build id we need
                # to ensure we are only updating, not replacing the existing product details.
                interim_set = set(getattr(component, attr))
                interim_set.update(product_details[attr])
                setattr(component, attr, list(interim_set))
            component.channels = component.get_channels()
            component.save()

        return None


class SoftwareBuildTag(Tag, TimeStampedModel):
    software_build = models.ForeignKey(SoftwareBuild, on_delete=models.CASCADE, related_name="tags")

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(
                name="unique_%(class)s", fields=("name", "value", "software_build")
            ),
        ]


class ProductModel(models.Model):
    """Abstract model that defines common fields for all product-related models."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(unique=True)
    description = models.TextField(default="")
    version = models.CharField(max_length=1024, default="")
    meta_attr = models.JSONField(default=dict)
    ofuri = models.CharField(max_length=1024, default="")
    lifecycle_url = models.CharField(max_length=1024, default="")

    # Override each of the below on that model, e.g. products = None on the Product model
    pnodes = GenericRelation(ProductNode)  # Needed to avoid a mypy warning
    products = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_versions = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_streams = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_variants = fields.ArrayField(models.CharField(max_length=200), default=list)
    channels = fields.ArrayField(models.CharField(max_length=200), default=list)

    @staticmethod
    def get_related_components(build_ids: QuerySet, sort_field: str) -> QuerySet["Component"]:
        """Returns unique components with matching build_id, ordered by sort_field"""
        if build_ids:
            query = Q()
            for build_id in build_ids:
                query |= Q(software_build=build_id)
            # TODO get component descendants as well
            return Component.objects.filter(query).distinct().order_by(sort_field)
        # Else self.builds is an empty QuerySet
        return Component.objects.none()

    @property
    def builds(self) -> QuerySet:
        """Returns unique productcomponentrelations with at least 1 matching variant or stream,
        ordered by build_id.
        """
        product_refs = [self.name]
        if isinstance(self, ProductStream):
            # we also want to include child product_variants of this product stream
            product_refs.extend(self.product_variants)
        elif isinstance(self, ProductVersion) or isinstance(self, Product):
            # we don't store product or product versions in the relations table therefore
            # we only want to include child product_streams and product_variants in the query
            product_refs = self.product_streams + self.product_variants
        # else it was a product variant, only look up by self.name

        if product_refs:
            return (
                ProductComponentRelation.objects.filter(product_ref__in=product_refs)
                .order_by("build_id")
                .values_list("build_id", flat=True)
                .distinct()
            )
        # Else no product_variants or product_streams
        return ProductComponentRelation.objects.none()

    @property
    def components(self) -> QuerySet["Component"]:
        """Return unique components with build_ids matching self.builds, ordered by purl"""
        # Return list of objs, not IDs like other props, so template can use obj props
        return self.get_related_components(self.builds, "purl")

    @property
    def coverage(self) -> int:
        if not self.pnodes.exists():
            return 0
        pnode_children = self.pnodes.first().get_children()
        if not pnode_children.exists():
            return 0
        has_build = 0
        for pn in pnode_children:
            if pn.obj.builds.exists():
                has_build += 1
        return round(has_build / pnode_children.count(), 2)

    def save_product_taxonomy(self):
        self.product_variants = ProductNode.get_product_variants(self)
        self.product_streams = ProductNode.get_product_streams(self)
        self.product_versions = ProductNode.get_product_versions(self)
        self.products = ProductNode.get_products(self)
        self.channels = ProductNode.get_channels(self)
        self.save()

    def save(self, *args, **kwargs):
        self.ofuri = self.get_ofuri()
        super().save(*args, **kwargs)

    class Meta:
        abstract = True
        ordering = ["name"]
        indexes = [
            models.Index(fields=["ofuri"]),
        ]

    def __str__(self) -> str:
        return str(self.name)


class Product(ProductModel, TimeStampedModel):

    # Inherit product_versions, product_streams, and product_variants from ProductModel
    # Override only products which doesn't make sense for this model
    products = None  # type: ignore
    pnodes = GenericRelation(ProductNode, related_query_name="product")

    def get_ofuri(self) -> str:
        """Return product URI

        Ex.: o:redhat:rhel
        """
        return f"o:redhat:{self.name}"

    @property
    def cpes(self):
        cpes = []
        for p in self.pnodes.get_queryset().get_descendants():
            if hasattr(p.obj, "cpe"):
                cpes.append(p.obj.cpe)
        return list(set(cpes))


class ProductTag(Tag):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="tags")

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(name="unique_%(class)s", fields=("name", "value", "product")),
        ]


class ProductVersion(ProductModel, TimeStampedModel):

    product_versions = None  # type: ignore
    pnodes = GenericRelation(ProductNode, related_query_name="product_version")

    def get_ofuri(self) -> str:
        """Return product version URI.

        Ex.: o:redhat:rhel:8
        """
        version_name = re.sub(r"(-|_|)" + self.version + "$", "", self.name)
        return f"o:redhat:{version_name}:{self.version}"

    @property
    def cpes(self):
        cpes = []
        for p in self.pnodes.get_queryset().get_descendants():
            if hasattr(p.obj, "cpe"):
                cpes.append(p.obj.cpe)
        return list(set(cpes))


class ProductVersionTag(Tag):
    product_version = models.ForeignKey(
        ProductVersion, on_delete=models.CASCADE, related_name="tags"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(
                name="unique_%(class)s", fields=("name", "value", "product_version")
            ),
        ]


class ProductStream(ProductModel, TimeStampedModel):

    cpe = models.CharField(max_length=1000, default="")

    # NOTE brew_tags and yum_repositories values shouldn't be exposed outside of Red Hat
    brew_tags = models.JSONField(default=dict)
    yum_repositories = fields.ArrayField(models.CharField(max_length=200), default=list)

    composes = models.JSONField(default=dict)
    active = models.BooleanField(default=False)
    et_product_versions = fields.ArrayField(models.CharField(max_length=200), default=list)

    # redefined from parent class
    product_streams = None  # type: ignore
    pnodes = GenericRelation(ProductNode, related_query_name="product_stream")

    @property
    def cpes(self) -> list[str]:
        return [self.cpe]

    def get_ofuri(self) -> str:
        """Return product stream URI

        Ex.: o:redhat:rhel:8.2.eus

        TODO: name embeds more then version ... need discussion
        """
        stream_name = re.sub(r"(-|_|)" + self.version + "$", "", self.name)
        return f"o:redhat:{stream_name}:{self.version}"

    @property
    def manifest(self) -> str:
        """Return an SPDX-style manifest in JSON format."""
        return ProductManifestFile(self).render_content()

    def get_latest_components(self):
        """Return root components from latest builds."""
        root_components = (
            Q(type=Component.Type.RPM, arch="src")
            | Q(type=Component.Type.CONTAINER_IMAGE, arch="noarch")
            | Q(type=Component.Type.RPMMOD)
        )
        return (
            Component.objects.filter(
                root_components,
                product_streams__overlap=[self.ofuri],
            )
            .annotate(
                latest=Subquery(
                    Component.objects.filter(
                        root_components,
                        name=OuterRef("name"),
                        product_streams__overlap=[self.ofuri],
                    )
                    .order_by("-software_build__completion_time")
                    .values("uuid")[:1]
                )
            )
            .filter(
                uuid=F("latest"),
            )
        )


class ProductStreamTag(Tag):
    product_stream = models.ForeignKey(ProductStream, on_delete=models.CASCADE, related_name="tags")

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(
                name="unique_%(class)s", fields=("name", "value", "product_stream")
            ),
        ]


class ProductVariant(ProductModel, TimeStampedModel):
    """Product Variant model

    This directly relates to Errata Tool Variants which are mapped then mapped to CDN
    repositories for content that is shipped as RPMs.
    """

    cpe = models.CharField(max_length=1000, default="")

    # redefined from parent class
    product_variants = None  # type: ignore
    pnodes = GenericRelation(ProductNode, related_query_name="product_variant")

    @property
    def cpes(self) -> list[str]:
        return [self.cpe]

    def get_ofuri(self) -> str:
        """Return product variant URI

        Ex.: o:redhat:rhel:8.6.0.z:baseos-8.6.0.z.main.eus
        """
        product_stream = f"o:redhat::{self.name.lower()}"

        first_pnode = self.pnodes.first()
        if not first_pnode:
            return product_stream

        product_stream_node = (
            first_pnode.get_ancestors()
            .filter(content_type=ContentType.objects.get_for_model(ProductStream))
            .first()
        )
        if not product_stream_node:
            return product_stream
        else:
            product_stream = f"{product_stream_node.obj.ofuri}:{self.name.lower()}"
        return product_stream


class ProductVariantTag(Tag):
    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="tags"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(
                name="unique_%(class)s", fields=("name", "value", "product_variant")
            ),
        ]


class Channel(TimeStampedModel):
    """A model that represents a specific type of delivery channel.

    A Channel is essentially the "location" of where a specific artifact is available from to
    customers.
    """

    class Type(models.TextChoices):
        CDN_REPO = "CDN_REPO"  # Main delivery channel for RPMs
        CONTAINER_REGISTRY = "CONTAINER_REGISTRY"  # Registries, e.g.: registry.redhat.io, quay.io

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(unique=True)
    relative_url = models.CharField(max_length=200, default="")
    type = models.CharField(choices=Type.choices, max_length=50)
    description = models.TextField(default="")
    meta_attr = models.JSONField(default=dict)
    pnodes = GenericRelation(ProductNode, related_query_name="channel")
    products = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_versions = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_streams = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_variants = fields.ArrayField(models.CharField(max_length=200), default=list)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return str(self.name)

    def save_product_taxonomy(self):
        # TODO: Below doesn't match ProductModel's save_product_taxonomy
        # We use ofuri here to identify the taxonomy
        # But ProductModel subclasses all use name to identify the taxonomy
        # And in Component, we use ofuri to identify the taxonomy
        # But the Component taxonomy properties don't seem to be set anywhere
        # and all Components in stage have an empty [] list of product_variants
        product_set = set()
        version_set = set()
        stream_set = set()
        variant_set = set()
        for n in self.pnodes.first().get_ancestors():
            if isinstance(n.obj, Product):
                product_set.add(n.obj.ofuri)
            elif isinstance(n.obj, ProductVersion):
                version_set.add(n.obj.ofuri)
            elif isinstance(n.obj, ProductStream):
                stream_set.add(n.obj.ofuri)
            elif isinstance(n.obj, ProductVariant):
                variant_set.add(n.obj.ofuri)
            else:
                raise ValueError(
                    f"Unknown type {type(n.obj)} for {n.obj.ofuri}"
                    f"when saving product taxonomy for channel {self.name}"
                )

        self.products = list(product_set)
        self.product_versions = list(version_set)
        self.product_streams = list(stream_set)
        self.product_variants = list(variant_set)
        self.save()


class ProductComponentRelation(TimeStampedModel):
    """class to be used for linking taxonomies"""

    class Type(models.TextChoices):
        ERRATA = "ERRATA"
        COMPOSE = "COMPOSE"
        BREW_TAG = "BREW_TAG"
        CDN_REPO = "CDN_REPO"
        YUM_REPO = "YUM_REPO"

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(choices=Type.choices, max_length=50)
    meta_attr = models.JSONField(default=dict)

    # Ex. if Errata Tool this would be errata_id
    external_system_id = models.CharField(max_length=200, default="")
    # E.g. product_variant for ERRATA, or product_stream for COMPOSE
    product_ref = models.CharField(max_length=200, default="")
    build_id = models.CharField(max_length=200, default="")

    class Meta:
        ordering = ["external_system_id"]
        constraints = [
            models.UniqueConstraint(
                name="unique_productcomponentrelation",
                fields=("external_system_id", "product_ref", "build_id"),
            ),
        ]
        indexes = [
            models.Index(fields=("external_system_id", "product_ref", "build_id")),
            models.Index(fields=("type", "build_id")),
            models.Index(fields=["external_system_id"]),
            models.Index(fields=["product_ref"]),
            models.Index(fields=["build_id"]),
            models.Index(fields=["type"]),
            models.Index(fields=("product_ref", "type")),
        ]


def get_product_streams_from_variants(variant_ids: list[str]):
    product_variants = ProductVariant.objects.filter(name__in=variant_ids)
    product_streams = []
    for pv in product_variants:
        product_streams.extend(ProductNode.get_product_streams(pv))
    return list(set(product_streams))


def get_product_details(variant_ids: list[str], stream_ids: list[str]) -> dict[str, set[str]]:
    if variant_ids:
        stream_ids.extend(get_product_streams_from_variants(variant_ids))
    product_streams = ProductStream.objects.filter(name__in=stream_ids)
    product_details = defaultdict(set)
    for product_stream in product_streams:
        for ancestor in product_stream.pnodes.get_queryset().get_ancestors(include_self=True):
            if type(ancestor.obj) is Product:
                product_details["products"].add(ancestor.obj.ofuri)
            if type(ancestor.obj) is ProductVersion:
                product_details["product_versions"].add(ancestor.obj.ofuri)
            if type(ancestor.obj) is ProductStream:
                product_details["product_streams"].add(ancestor.obj.ofuri)
    return product_details


class ComponentQuerySet(models.QuerySet):
    def srpms(self) -> models.QuerySet["Component"]:
        return self.filter(Q(type=Component.Type.RPM) & Q(arch="src"))

    def root_components(self) -> models.QuerySet["Component"]:
        return self.filter(
            Q(Q(type=Component.Type.RPM) & Q(arch="src"))
            | Q(type=Component.Type.RPMMOD)
            | Q(Q(type=Component.Type.CONTAINER_IMAGE) & Q(arch="noarch"))
        )


class Component(TimeStampedModel):
    class Type(models.TextChoices):
        CARGO = "CARGO"  # Rust packages
        CONTAINER_IMAGE = "OCI"  # Container images and other OCI artifacts
        GEM = "GEM"  # Rubygem packages
        GENERIC = "GENERIC"  # Fallback if no other type can be used
        GITHUB = "GITHUB"
        GOLANG = "GOLANG"
        MAVEN = "MAVEN"
        NPM = "NPM"
        RPMMOD = "RPMMOD"  # RHEL/Fedora modules; not an actual purl type, see CORGI-226
        RPM = "RPM"  # Includes SRPMs, which can be identified with arch=src; see also is_srpm().
        PYPI = "PYPI"

    class Namespace(models.TextChoices):
        UPSTREAM = "UPSTREAM"
        REDHAT = "REDHAT"

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    description = models.TextField()
    meta_attr = models.JSONField(default=dict)

    type = models.CharField(choices=Type.choices, max_length=20)
    namespace = models.CharField(choices=Namespace.choices, max_length=20)
    version = models.CharField(max_length=1024)
    release = models.CharField(max_length=1024, default="")
    arch = models.CharField(max_length=1024, default="")

    purl = models.CharField(max_length=1024, default="")
    nvr = models.CharField(max_length=1024, default="")
    nevra = models.CharField(max_length=1024, default="")

    products = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_versions = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_streams = fields.ArrayField(models.CharField(max_length=200), default=list)
    product_variants = fields.ArrayField(models.CharField(max_length=200), default=list)
    channels = fields.ArrayField(models.CharField(max_length=200), default=list)
    provides = fields.ArrayField(models.CharField(max_length=1024), default=list)
    sources = fields.ArrayField(models.CharField(max_length=1024), default=list)
    upstreams = fields.ArrayField(models.CharField(max_length=1024), default=list)

    cnodes = GenericRelation(ComponentNode)
    software_build = models.ForeignKey(
        SoftwareBuild, on_delete=models.CASCADE, null=True, related_name="components"
    )

    # Copyright text as it appears in the component source code, from an OpenLCS scan
    copyright_text = models.TextField(default="")
    # License information as it appears in the component source code, from an OpenLCS scan
    # Use the license_concluded or license_concluded_list properties, not this field directly
    license_concluded_raw = models.TextField(default="")
    # The raw "summary license string" without any transformations
    # This information comes from the build system via an RPM spec file, Maven POM file, etc.
    # Use the license_declared or license_declared_list properties, not this field directly
    license_declared_raw = models.TextField(default="")
    # URL with more information about the scan that was performed
    openlcs_scan_url = models.TextField(default="")
    # Version of scanner used to perform this analysis
    openlcs_scan_version = models.TextField(default="")

    # The filename of the source rpm package, or similar, from the meta_attr / build system
    filename = models.TextField(default="")

    related_url = models.CharField(max_length=1024, default="")

    data_score = models.IntegerField(default=0)
    data_report = fields.ArrayField(models.CharField(max_length=200), default=list)

    objects = ComponentQuerySet.as_manager()

    class Meta:
        ordering = (
            "type",
            "name",
        )
        constraints = [
            models.UniqueConstraint(
                name="unique_components",
                fields=("name", "type", "arch", "version", "release"),
            ),
        ]
        indexes = [
            models.Index(fields=("name", "type", "arch", "version", "release")),
            models.Index(fields=("type", "name")),
            models.Index(fields=["name"]),
            models.Index(fields=["type"]),
            models.Index(fields=["nvr"]),
            models.Index(fields=["purl"]),
            models.Index(fields=["product_streams"]),
            models.Index(fields=["product_variants"]),
            models.Index(fields=("type", "product_streams")),
            models.Index(
                fields=("type", "name", "arch"),
                name="compon_latest_name_type_idx",
                condition=Q(
                    Q(Q(type="RPM") & Q(arch="src"))
                    | Q(type="RPMMOD")
                    | Q(Q(type="OCI") & Q(arch="noarch"))
                ),
            ),
            models.Index(
                fields=("name", "type", "arch"),
                name="compon_latest_type_name_idx",
                condition=Q(
                    Q(Q(type="RPM") & Q(arch="src"))
                    | Q(type="RPMMOD")
                    | Q(Q(type="OCI") & Q(arch="noarch"))
                ),
            ),
            models.Index(
                fields=("uuid", "software_build_id", "type", "name", "arch", "product_streams"),
                name="compon_latest_idx",
                condition=Q(
                    Q(Q(type="RPM") & Q(arch="src"))
                    | Q(type="RPMMOD")
                    | Q(Q(type="OCI") & Q(arch="noarch"))
                ),
            ),
        ]

    def __str__(self) -> str:
        """return name"""
        return str(self.name)

    def get_purl(self) -> PackageURL:
        if self.type == Component.Type.RPM:
            qualifiers = {
                "arch": self.arch,
            }
            if self.epoch:
                qualifiers["epoch"] = str(self.epoch)
            purl_data = dict(
                name=self.name,
                version=f"{self.version}-{self.release}",
                qualifiers=qualifiers,
            )
        elif self.type == Component.Type.RPMMOD:
            # Break down RHEL module version into its specific parts:
            # NSVC = Name, Stream, Version, Context
            version, _, context = self.release.partition(".")
            stream = self.version
            purl_data = dict(
                name=self.name,
                version=f"{stream}:{version}:{context}",
            )
        elif self.type == Component.Type.CONTAINER_IMAGE:
            digest = ""
            if self.meta_attr.get("digests"):
                for digest_fmt in CONTAINER_DIGEST_FORMATS:
                    digest = self.meta_attr["digests"].get(digest_fmt)
                    if digest:
                        break
            purl_name = self.name
            name_from_label = self.meta_attr.get("name_from_label")
            if name_from_label:
                purl_name = name_from_label
            qualifiers = {
                "tag": f"{self.version}-{self.release}",
            }
            if self.arch != "noarch":
                qualifiers["arch"] = self.arch
            repository_url = self.meta_attr.get("repository_url")
            if repository_url:
                qualifiers["repository_url"] = repository_url
            purl_data = dict(
                name=purl_name,
                version=digest,
                qualifiers=qualifiers,
            )
        else:
            version = self.version
            if self.release:
                version = f"{version}-{self.release}"
            purl_data = dict(
                name=self.name,
                version=version,
            )

        # Red Hat components should be namespaced, everything else is assumed to be upstream.
        if self.namespace == Component.Namespace.REDHAT:
            purl_data["namespace"] = str(self.namespace).lower()

        purl = PackageURL(type=str(self.type).lower(), **purl_data)
        return purl

    def save_component_taxonomy(self):
        self.upstreams = self.get_upstreams()
        self.provides = list(self.get_provides_purls())
        self.sources = self.get_source()
        self.save()

    def is_srpm(self):
        return self.type == Component.Type.RPM and self.arch == "src"

    def get_nvr(self) -> str:
        return f"{self.name}-{self.version}-{self.release}"

    def get_nevra(self) -> str:
        return (
            f"{self.name}{f':{self.epoch}' if self.epoch else ''}"
            f"-{self.version}-{self.release}.{self.arch}"
        )

    def save(self, *args, **kwargs):
        self.nvr = self.get_nvr()
        self.nevra = self.get_nevra()
        purl = self.get_purl()
        self.purl = purl.to_string()
        super().save(*args, **kwargs)

    @property
    def get_roots(self) -> list[ComponentNode]:
        """Return component root entities."""
        roots: list[ComponentNode] = []
        # If a component does not have a softwarebuild that means it's not built at Red Hat
        # therefore it doesn't need its upstreams listed. If we start using the get_roots property
        # for functions other than get_upstreams we might need to revisit this check
        if not self.software_build:
            return roots
        for cnode in self.cnodes.get_queryset():
            root = cnode.get_root()
            if root.obj.type == Component.Type.CONTAINER_IMAGE:
                # TODO if we change the CONTAINER->RPM ComponentNode.type to something besides
                # 'PROVIDES' we would check for that type here to prevent 'hardcoding' the
                # container -> rpm relationship here.
                # RPMs are included as children of Containers as well as SRPMs
                # We don't want to include Containers in the RPMs roots.
                # Partly because RPMs in containers can have unprocessed SRPMs
                # And partly because we use roots to find upstream components,
                # and it's not true to say that rpms share upstreams with containers
                rpm_descendant = False
                for ancestor in cnode.get_ancestors(include_self=True):
                    if ancestor.obj.type == Component.Type.RPM:
                        rpm_descendant = True
                        break
                if not rpm_descendant:
                    roots.append(root)
            else:
                roots.append(root)
        return list(set(roots))

    @property
    def build_meta(self):
        return self.software_build.meta_attr

    @property
    def errata(self) -> list[str]:
        """Return errata that contain component."""
        if not self.software_build:
            return []
        errata_qs = (
            ProductComponentRelation.objects.filter(
                type=ProductComponentRelation.Type.ERRATA,
                build_id=self.software_build.build_id,
            )
            .order_by("external_system_id")
            .values_list("external_system_id", flat=True)
            .distinct()
        )
        return list(erratum for erratum in errata_qs if erratum)

    @property
    def license_concluded(self) -> str:
        """Return the OLCS scan results formatted as an SPDX license expression
        This is almost the same as above, but operators (AND, OR) + license IDs are uppercased"""
        return self.license_concluded_raw.upper()

    @property
    def license_declared(self) -> str:
        """Return the "summary license string" formatted as an SPDX license expression
        This is almost the same as above, but operators (AND, OR) + license IDs are uppercased"""
        return self.license_declared_raw.upper()

    @staticmethod
    def license_list(license_expression: str) -> list[str]:
        """Return a list of any possibly-relevant licenses. No information is given about which apply
        To see if all apply or if you may choose between them, parse the license expression above"""
        # "words".split("not present in string") will return ["words"]
        # AKA below will always add one level of nesting to the array
        license_parts = license_expression.split(" AND ")
        # Flatten it back to a list[str] in one line to fix mypy errors
        license_parts = [nested for part in license_parts for nested in part.split(" OR ")]

        return [part.strip("()") for part in license_parts]

    @property
    def license_concluded_list(self) -> list[str]:
        return self.license_list(self.license_concluded)

    @property
    def license_declared_list(self) -> list[str]:
        return self.license_list(self.license_declared)

    @property
    def manifest(self) -> str:
        """Return an SPDX-style manifest in JSON format."""
        return ComponentManifestFile(self).render_content()

    @property
    def epoch(self) -> str:
        return self.meta_attr.get("epoch", "")

    def get_channels(self):
        variant_ids = self.product_variants
        if not variant_ids:
            return []
        query = Q()
        for variant_id in variant_ids:
            query = query | Q(name__contains=variant_id)
        product_variants = ProductVariant.objects.filter(query)
        channels = []
        for product_variant in product_variants:
            for descendant in product_variant.pnodes.get_queryset().get_descendants():
                if isinstance(descendant.obj, Channel):
                    channels.append(descendant.obj.name)
        return list(set(channels))

    def get_provides_nodes(self, include_dev: bool = True) -> QuerySet[ComponentNode]:
        """return a QuerySet of descendants with PROVIDES ComponentNode type"""
        # Used in manifests. Returns whole objects to access their properties
        type_list = [ComponentNode.ComponentNodeType.PROVIDES]
        if include_dev:
            type_list.append(ComponentNode.ComponentNodeType.PROVIDES_DEV)
        return self.cnodes.get_queryset().get_descendants().filter(type__in=type_list)

    def get_provides_purls(self, include_dev: bool = True) -> QuerySet:
        """return a QuerySet of unique descendant PURLs with PROVIDES ComponentNode type"""
        # Used in serializers / taxonomies. Returns identifiers (purls) to track relationships
        return (
            # No need for .order_by() to prevent duplicate values in list
            # ComponentNode.Meta has no ordering, so .distinct() works automatically
            self.get_provides_nodes(include_dev=include_dev)
            .values_list("purl", flat=True)
            .distinct()
        )

    def get_source(self) -> list:
        """return all root nodes"""
        purl_cn = ComponentNode.objects.filter(purl=self.purl).get_ancestors(  # type: ignore
            include_self=False
        )
        return list(purl_cn.filter(parent=None).values_list("purl", flat=True).distinct())

    def get_upstreams(self):
        """return upstreams component ancestors in family trees"""
        roots = self.get_roots
        if not roots:
            return []
        upstreams = set()
        for root in roots:
            # For SRRPM/RPMS, and noarch containers, these are the cnodes we want.
            source_children = [
                c for c in root.get_children().filter(type=ComponentNode.ComponentNodeType.SOURCE)
            ]
            # Cachito builds nest components under the relevant source component for that
            # container build, eg. buildID=1911112. In that case we need to walk up the
            # tree from the current node to find its relevant source
            if (
                root.obj.type == Component.Type.CONTAINER_IMAGE
                and root.obj.arch == "noarch"
                and len(source_children) > 1
            ):
                upstreams.update(
                    [
                        a.purl
                        for a in self.cnodes.get_queryset().first().get_ancestors(include_self=True)
                        if a.type == ComponentNode.ComponentNodeType.SOURCE
                        and a.obj.namespace == Component.Namespace.UPSTREAM
                    ]
                )
            else:
                upstreams.update(c.purl for c in source_children)
        return list(upstreams)

    def save_datascore(self):
        score = 0
        report = set()
        if self.namespace == Component.Namespace.UPSTREAM and not self.related_url:
            score += 20
            report.add("upstream has no related_url")
        if not self.description:
            score += 10
            report.add("no description")
        if not self.version:
            score += 10
            report.add("no version")
        if not self.license_declared_raw:
            score += 10
            report.add("no license declared")
        if not self.products:
            score += 10
            report.add("no products")
        if not self.product_versions:
            score += 10
            report.add("no product versions")
        if not self.product_streams:
            score += 10
            report.add("no product streams")
        if not self.product_variants:
            score += 20
            report.add("no product variants")
        if not self.sources:
            score += 10
            report.add("no sources")
        if not self.provides:
            score += 10
            report.add("no provides")
        if not self.upstreams:
            score += 10
            report.add("no upstream")
        if not self.software_build:
            score += 10
            report.add("no software build")
        self.data_score = score
        self.data_report = list(report)
        self.save()


class ComponentTag(Tag):
    component = models.ForeignKey(Component, on_delete=models.CASCADE, related_name="tags")

    class Meta:
        constraints = [
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(name="unique_%(class)s", fields=("name", "value", "component")),
        ]


class AppStreamLifeCycle(TimeStampedModel):
    """LifeCycle model based on lifecycle-defs repo in CEE Gitlab"""

    class LifeCycleType(models.TextChoices):
        MODULE = "module"
        PACKAGE = "package"
        SCL = "scl"

    class LifeCycleSource(models.TextChoices):
        DEFAULT = "default"
        PREVIOUS_RELEASE = "previous_release"
        PRP = "prp"
        CONFIRMED = "confirmed"
        OVERRIDE = "override"

    # Name could be a module, a package, or a collection (as seen in the compose)
    name = models.TextField()
    type = models.CharField(
        choices=LifeCycleType.choices, default=LifeCycleType.PACKAGE, max_length=50
    )
    lifecycle = models.IntegerField()
    # Application Compatibility Guide level (1-4)
    # See docs in the lifecycle-defs repo
    acg = models.IntegerField()
    start_date = models.DateField(null=True)
    end_date = models.DateField(null=True)
    # FIXME: ideally 'product', 'initial_product_version', 'stream' should be
    # foreignkey to corresponding models, however we don't have enough data yet.
    # Data records from `lifecycle-defs` repo could not be used to populate
    # product/version/stream as the repo is not a reliable source: there
    # are inconsistent entries which would result in dirty data if relations
    # are created based on the data there.
    # product = models.ForeignKey('Product', on_delete=models.CASCADE)
    # initial_product_version = models.ForeignKey('ProductVersion', on_delete=models.CASCADE)
    # stream = models.ForeignKey('ProductStream', on_delete=models.CASCADE)
    product = models.TextField()
    initial_product_version = models.TextField()
    stream = models.TextField()
    source = models.CharField(choices=LifeCycleSource.choices, max_length=50)
    # True if the resulting definition should be omitted from public view
    private = models.BooleanField()
    meta_attr = models.JSONField(default=dict)

    def is_rolling_appstream(self):
        return self.lifecycle == 0

    def is_dependent_appstream(self):
        return self.lifecycle == -1

    def __str__(self) -> str:
        return f"{self.name}@{self.initial_product_version}.{self.stream}"

    class Meta:
        constraints = [
            # FIXME: value range is what the 'lifecycle-defs' repo suggests, but there are
            # exceptional cases in the yaml which fail the range constraint.
            # models.CheckConstraint(
            #     check=models.Q(acg__gte=1) & models.Q(acg__lte=4),
            #     name="An acg value is valid between 1 and 4",
            # ),
            # models.CheckConstraint(
            #     check=models.Q(lifecycle__gte=-1) & models.Q(lifecycle__lte=10),
            #     name="A lifecycle value is valid between -1 and 10",
            # ),
            models.UniqueConstraint(
                # assuming below fields are 'unique_together'.
                fields=["name", "type", "product", "initial_product_version", "stream"],
                name="unique_lifecycle_entity",
            )
        ]
        ordering = ["name"]
