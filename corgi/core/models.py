import functools
import logging
import re
import uuid as uuid
from abc import abstractmethod
from typing import Any, Iterator, Union

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres import fields
from django.db import models
from django.db.models import Q, QuerySet
from mptt.managers import TreeManager
from mptt.models import MPTTModel, TreeForeignKey
from packageurl import PackageURL
from packageurl.contrib import purl2url
from rpm import labelCompare

from corgi.core.constants import (
    CONTAINER_DIGEST_FORMATS,
    EL_MATCH_RE,
    EPOCH_TYPE,
    MODEL_NODE_LEVEL_MAPPING,
    RELEASE_VERSION_DELIM_RE,
    ROOT_COMPONENTS_CONDITION,
    SRPM_CONDITION,
)
from corgi.core.files import ComponentManifestFile, ProductManifestFile
from corgi.core.mixins import TimeStampedModel

logger = logging.getLogger(__name__)


class NodeManager(TreeManager):
    """Custom manager to remove ordering from TreeQuerySets (cnodes and pnodes)
    to allow calling .distinct() without first calling .order_by() every time
    """

    def get_queryset(self, *args, **kwargs):
        return super().get_queryset(*args, **kwargs).order_by()


class NodeModel(MPTTModel, TimeStampedModel):
    """Generic model for component and product taxonomies
    that factors out some common behavior and adds some helper logic"""

    parent = TreeForeignKey("self", on_delete=models.CASCADE, null=True, related_name="children")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    obj = GenericForeignKey(
        "content_type",
        "object_id",
    )

    objects = NodeManager()

    class MPTTMeta:
        level_attr = "level"
        root_node_ordering = False

    class Meta:
        abstract = True
        indexes = (
            models.Index(fields=("object_id", "parent")),
            # Add index on foreign-key fields here, to speed up iterating over cnodes / pnodes
            # GenericForeignKey doesn't get these by default, only ForeignKey
            models.Index(fields=("content_type", "object_id")),
        )

    @property
    def name(self):
        return self.obj.name

    @property
    def desc(self):
        return self.obj.description


class ProductNode(NodeModel):
    """Product taxonomy node."""

    class Meta(NodeModel.Meta):
        constraints = (
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
        )

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
    #   the use of `pnodes.get()`).
    #
    # - The `values_list()` query relies on the GenericRelation of each model's pnodes
    #   attribute's related query name.

    @staticmethod
    def get_node_pks_for_type(
        qs: QuerySet["ProductNode"], mapping_model: type, lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        """For a given ProductNode queryset, find all nodes with the given type
        and return a lookup on their related objects (primary key by default)"""
        # "ProductVersion" for mapping, "productversion__pk" for field lookup
        mapping_key = mapping_model.__name__
        qs = qs.filter(level=MODEL_NODE_LEVEL_MAPPING[mapping_key])
        if lookup:
            target_model = mapping_key.lower()
            qs = qs.values_list(f"{target_model}{lookup}", flat=True)
        # else client code needs whole Product instances, not their PKs
        # No way to return "obj" / "productstream" field as a model instance here
        # ManyToManyFields can use PKs, but ForeignKeys require model instances
        return qs

    @staticmethod
    def get_node_names_for_type(product_model: "ProductModel", target_model: str) -> QuerySet:
        """For a given ProductModel / Channel, find all related nodes with the given type
        and return the names of their related objects"""
        # "product_version" -> "ProductVersion"
        mapping_model = target_model.title().replace("_", "")
        # No .distinct() since __name on all ProductModel subclasses + Channel is always unique
        return (
            product_model.pnodes.get()
            .get_family()
            .filter(level=MODEL_NODE_LEVEL_MAPPING[mapping_model])
            .values_list(f"{target_model}__name", flat=True)
        )

    @classmethod
    def get_products(
        cls, qs: QuerySet["ProductNode"], lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        return cls.get_node_pks_for_type(qs, Product, lookup=lookup)

    @classmethod
    def get_product_versions(
        cls, qs: QuerySet["ProductNode"], lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        return cls.get_node_pks_for_type(qs, ProductVersion, lookup=lookup)

    @classmethod
    def get_product_streams(
        cls, qs: QuerySet["ProductNode"], lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        return cls.get_node_pks_for_type(qs, ProductStream, lookup=lookup)

    @classmethod
    def get_product_variants(
        cls, qs: QuerySet["ProductNode"], lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        return cls.get_node_pks_for_type(qs, ProductVariant, lookup=lookup)

    @classmethod
    def get_channels(
        cls, qs: QuerySet["ProductNode"], lookup: str = "__pk"
    ) -> QuerySet["ProductNode"]:
        return cls.get_node_pks_for_type(qs, Channel, lookup=lookup)


class ComponentNode(NodeModel):
    """Component taxonomy node."""

    class ComponentNodeType(models.TextChoices):
        SOURCE = "SOURCE"
        REQUIRES = "REQUIRES"
        PROVIDES = "PROVIDES"  # including bundled provides
        # eg. dev dependencies from Cachito builds
        # https://github.com/containerbuildsystem/cachito/#feature-definitions
        PROVIDES_DEV = "PROVIDES_DEV"

    PROVIDES_NODE_TYPES = (ComponentNodeType.PROVIDES, ComponentNodeType.PROVIDES_DEV)

    # TODO: This shadows built-in name "type" and creates a warning when updating openapi.yml
    type = models.CharField(
        choices=ComponentNodeType.choices, default=ComponentNodeType.SOURCE, max_length=20
    )
    # Saves an expensive django dereference into node object
    purl = models.CharField(max_length=1024, default="")

    class Meta(NodeModel.Meta):
        constraints = (
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
        )
        # 0037_custom_indexes.py contains the following custom performance indexes
        #    core_componentnode_tree_parent_lft_idx
        #    core_cn_tree_lft_purl_parent_idx
        #    core_cn_lft_rght_tree_idx
        indexes = (  # type: ignore[assignment]
            models.Index(fields=("type", "parent", "purl")),
            models.Index(fields=("parent",)),
            *NodeModel.Meta.indexes,
        )

    def save(self, *args, **kwargs):
        self.purl = self.obj.purl
        super().save(*args, **kwargs)


class Tag(TimeStampedModel):
    name = models.SlugField(max_length=200)  # Must not be empty
    value = models.CharField(max_length=1024, default="")

    @property
    @abstractmethod
    def tagged_model(self):
        pass

    class Meta:
        abstract = True
        constraints = (
            models.CheckConstraint(name="%(class)s_name_required", check=~models.Q(name="")),
            models.UniqueConstraint(
                name="unique_%(class)s", fields=("name", "value", "tagged_model")
            ),
        )

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
        CENTOS = "CENTOS"  # Used by OpenStack RDO
        APP_INTERFACE = "APP_INTERFACE"  # Managed Services

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    build_id = models.CharField(max_length=200, default="")
    build_type = models.CharField(choices=Type.choices, max_length=20)
    name = models.TextField()  # Arbitrary identifier for a build
    source = models.TextField()  # Source code reference for build
    completion_time = models.DateTimeField(null=True)  # meta_attr["completion_time"]
    # Store meta attributes relevant to different build system types.
    meta_attr = models.JSONField(default=dict)
    # implicit field "components" from Component model's ForeignKey
    components: models.Manager["Component"]

    class Meta:
        indexes = (models.Index(fields=("build_id", "build_type")),)
        constraints = [
            models.UniqueConstraint(
                fields=["build_id", "build_type"], name="unique_build_id_by_type"
            ),
        ]

    def save_product_taxonomy(self):
        """update ('materialize') product taxonomy on all build components

        This method is defined on SoftwareBuild and not Component,
        because the ProductComponentRelation table refers to build IDs,
        which we use to look up which products a certain component should be linked to
        """
        variant_names = tuple(
            ProductComponentRelation.objects.filter(
                build_id=self.build_id,
                build_type=self.build_type,
                type__in=ProductComponentRelation.VARIANT_TYPES,
            )
            .values_list("product_ref", flat=True)
            .distinct()
        )

        stream_names = list(
            ProductComponentRelation.objects.filter(
                build_id=self.build_id,
                build_type=self.build_type,
                type__in=ProductComponentRelation.STREAM_TYPES,
            )
            .values_list("product_ref", flat=True)
            .distinct()
        )

        product_details = get_product_details(variant_names, stream_names)
        components = set()
        for component in self.components.get_queryset().iterator():
            # This is needed for container image builds which pull in components not
            # built at Red Hat, and therefore not assigned a build_id
            for cnode in component.cnodes.get_queryset().prefetch_related("obj").iterator():
                components.add(cnode.obj)
                for d in cnode.get_descendants().iterator():
                    components.add(d.obj)

        for component in components:
            component.save_product_taxonomy(product_details)

        return None


class SoftwareBuildTag(Tag):
    tagged_model = models.ForeignKey(
        SoftwareBuild, on_delete=models.CASCADE, related_name="tags", db_column="tagged_model_uuid"
    )


class ProductModel(TimeStampedModel):
    """Abstract model that defines common fields for all product-related models."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(unique=True)
    description = models.TextField(default="")
    version = models.CharField(max_length=1024, default="")
    meta_attr = models.JSONField(default=dict)
    ofuri = models.CharField(max_length=1024, default="")
    lifecycle_url = models.CharField(max_length=1024, default="")

    pnodes = GenericRelation(ProductNode, related_query_name="%(class)s")

    # below fields are added implicitly to all ProductModel subclasses
    # using Django reverse relations, but mypy needs an explicit type hint
    @property
    @abstractmethod
    def products(self) -> Union["Product", models.ForeignKey]:
        pass

    @property
    @abstractmethod
    def productversions(
        self,
    ) -> Union["ProductVersion", models.ForeignKey, models.Manager["ProductVersion"]]:
        pass

    @property
    @abstractmethod
    def productstreams(
        self,
    ) -> Union["ProductStream", models.ForeignKey, models.Manager["ProductStream"]]:
        pass

    @property
    @abstractmethod
    def productvariants(self) -> Union["ProductVariant", models.Manager["ProductVariant"]]:
        pass

    @property
    @abstractmethod
    def channels(self) -> models.Manager["Channel"]:
        pass

    @property
    @abstractmethod
    def components(self) -> "ComponentQuerySet":
        pass

    @property
    def builds(self) -> QuerySet:
        """Returns unique productcomponentrelations with at least 1 matching variant or stream,
        ordered by build_id.
        """
        # TODO: Can just return self.components.values_list("software_build", flat=True).distinct()
        product_refs = [self.name]
        if isinstance(self, ProductStream):
            # we also want to include child product variants of this product stream
            product_refs.extend(
                self.productvariants.values_list("name", flat=True).using("read_only")
            )
        elif isinstance(self, ProductVersion) or isinstance(self, Product):
            # we don't store products or product versions in the relations table therefore
            # we only want to include child product streams and product variants in the query
            product_refs.extend(
                self.productstreams.values_list("name", flat=True).using("read_only")
            )
            product_refs.extend(
                self.productvariants.values_list("name", flat=True).using("read_only")
            )
        # else it was a product variant, only look up by self.name

        if product_refs:
            return (
                ProductComponentRelation.objects.filter(product_ref__in=product_refs)
                .values_list("build_id", "build_type")
                .distinct()
                .using("read_only")
            )
        # Else no product variants or product streams - should never happen
        return ProductComponentRelation.objects.none()

    @property
    def cpes(self) -> tuple[str, ...]:
        """Return CPEs for child streams / variants."""
        # include_self=True so we don't have to override this property for streams
        descendants = (
            self.pnodes.get_queryset().get_descendants(include_self=True).using("read_only")
        )
        stream_cpes = (
            descendants.filter(level=MODEL_NODE_LEVEL_MAPPING["ProductStream"])
            .values_list("productstream__cpe", flat=True)
            .distinct()
        )
        variant_cpes = (
            descendants.filter(level=MODEL_NODE_LEVEL_MAPPING["ProductVariant"])
            .values_list("productvariant__cpe", flat=True)
            .distinct()
        )
        # Omit CPEs like "", but GenericForeignKeys only support .filter(), not .exclude()??
        return tuple(cpe for cpe in (*stream_cpes, *variant_cpes) if cpe)

    @abstractmethod
    def get_ofuri(self) -> str:
        pass

    def save_product_taxonomy(self):
        """Save links between related ProductModel subclasses"""
        family = self.pnodes.get().get_family()
        # Get obj from raw nodes - no way to return related __product obj in values_list()
        products = ProductNode.get_products(family, lookup="").first().obj
        productversions = tuple(
            node.obj for node in ProductNode.get_product_versions(family, lookup="")
        )
        productstreams = tuple(
            node.obj for node in ProductNode.get_product_streams(family, lookup="")
        )
        productvariants = tuple(
            node.obj for node in ProductNode.get_product_variants(family, lookup="")
        )
        channels = ProductNode.get_channels(family)

        # Avoid setting fields on models which don't have them
        # Also set fields correctly based on which side of the relationship we see
        # forward relationship like "versions -> products" assigns a single object
        # reverse relationship like "products -> versions" calls .set() with many objects
        if isinstance(self, Product):
            self.productversions.set(productversions)
            self.productstreams.set(productstreams)
            self.productvariants.set(productvariants)

        elif isinstance(self, ProductVersion):
            self.products = products  # Should be only one parent object
            self.productstreams.set(productstreams)
            self.productvariants.set(productvariants)
            self.save()

        elif isinstance(self, ProductStream):
            self.products = products
            self.productversions = productversions[0]
            self.productvariants.set(productvariants)
            self.save()

        elif isinstance(self, ProductVariant):
            self.products = products
            self.productversions = productversions[0]
            self.productstreams = productstreams[0]
            self.save()

        else:
            raise NotImplementedError(
                f"Add behavior for class {type(self)} in ProductModel.save_product_taxonomy()"
            )

        # All ProductModels have a set of descendant channels
        self.channels.set(channels)

    def save(self, *args, **kwargs):
        self.ofuri = self.get_ofuri()
        super().save(*args, **kwargs)

    class Meta:
        abstract = True
        indexes = (models.Index(fields=("ofuri",)),)

    def __str__(self) -> str:
        return str(self.name)


class Product(ProductModel):
    @property
    def products(self) -> "Product":
        return self

    # implicit "productversions" field on Product model
    # is created by products field on ProductVersion model
    productversions: models.Manager["ProductVersion"]

    # implicit "productstreams" field on Product model
    # is created by products field on ProductStream model
    productstreams: models.Manager["ProductStream"]

    # implicit "productvariants" field on Product model
    # is created by products field on ProductVariant model
    productvariants: models.Manager["ProductVariant"]

    # implicit "channels" field on Product model
    # is created by products field on Channel model
    channels: models.Manager["Channel"]

    # implicit "components" field on Product model
    # is created by products field on Component model
    components: "ComponentQuerySet"

    def get_ofuri(self) -> str:
        """Return product URI

        Ex.: o:redhat:rhel
        """
        return f"o:redhat:{self.name}"


class ProductTag(Tag):
    tagged_model = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="tags")


class ProductVersion(ProductModel):
    products = models.ForeignKey(
        "Product", on_delete=models.CASCADE, related_name="productversions"
    )

    @property
    def productversions(self) -> "ProductVersion":
        return self

    # implicit "productstreams" field on ProductVersion model
    # is created by productversions field on ProductStream model
    productstreams: models.Manager["ProductStream"]

    # implicit "productvariants" field on ProductVersion model
    # is created by productversions field on ProductVariant model
    productvariants: models.Manager["ProductVariant"]

    # implicit "channels" field on ProductVersion model
    # is created by productversions field on Channel model
    channels: models.Manager["Channel"]

    # implicit "components" field on ProductVersion model
    # is created by productversions field on Component model
    components: "ComponentQuerySet"

    def get_ofuri(self) -> str:
        """Return product version URI.

        Ex.: o:redhat:rhel:8
        """
        version_name = re.sub(r"(-|_|)" + self.version + "$", "", self.name)
        return f"o:redhat:{version_name}:{self.version}"


class ProductVersionTag(Tag):
    tagged_model = models.ForeignKey(ProductVersion, on_delete=models.CASCADE, related_name="tags")


class ProductStream(ProductModel):
    cpe = models.CharField(max_length=1000, default="")

    # NOTE brew_tags and yum_repositories values shouldn't be exposed outside of Red Hat
    brew_tags = models.JSONField(default=dict)
    yum_repositories = fields.ArrayField(models.CharField(max_length=200), default=list)

    composes = models.JSONField(default=dict)
    active = models.BooleanField(default=False)
    et_product_versions = fields.ArrayField(models.CharField(max_length=200), default=list)

    products = models.ForeignKey("Product", on_delete=models.CASCADE, related_name="productstreams")
    productversions = models.ForeignKey(
        "ProductVersion",
        on_delete=models.CASCADE,
        related_name="productstreams",
    )

    @property
    def productstreams(self) -> "ProductStream":
        return self

    # implicit "productvariants" field on ProductStream model
    # is created by productstreams field on ProductVariant model
    productvariants: models.Manager["ProductVariant"]

    # implicit "channels" field on ProductStream model
    # is created by productstreams field on Channel model
    channels: models.Manager["Channel"]

    # implicit "components" field on ProductStream model
    # is created by productstreams field on Component model
    components: "ComponentQuerySet"

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

    @property
    def provides_queryset(self, using: str = "read_only") -> QuerySet["Component"]:
        """Returns unique aggregate "provides" for the latest components in this stream,
        for use in templates"""
        unique_provides = (
            self.components.exclude(
                type=Component.Type.CONTAINER_IMAGE, name__endswith="-container-source"
            )
            .using(using)
            .root_components()
            .released_components()
            .latest_components()
            .values_list("provides__pk", flat=True)
            .distinct()
            .order_by("provides__pk")
            .iterator()
        )
        return (
            Component.objects.filter(pk__in=unique_provides)
            # Remove .exclude() below when CORGI-428 is resolved
            .exclude(type=Component.Type.GOLANG, name__contains="./").using(using)
        )


class ProductStreamTag(Tag):
    tagged_model = models.ForeignKey(ProductStream, on_delete=models.CASCADE, related_name="tags")


class ProductVariant(ProductModel):
    """Product Variant model

    This directly relates to Errata Tool Variants which are mapped then mapped to CDN
    repositories for content that is shipped as RPMs.
    """

    cpe = models.CharField(max_length=1000, default="")

    products = models.ForeignKey(
        "Product", on_delete=models.CASCADE, related_name="productvariants"
    )
    productversions = models.ForeignKey(
        "ProductVersion", on_delete=models.CASCADE, related_name="productvariants"
    )
    # Below creates implicit "productvariants" field on ProductStream
    productstreams = models.ForeignKey(
        ProductStream, on_delete=models.CASCADE, related_name="productvariants"
    )

    @property
    def productvariants(self) -> "ProductVariant":
        return self

    # implicit "channels" field on ProductVariant model
    # is created by productvariants field on Channel model
    channels: models.Manager["Channel"]

    # implicit "components" field on ProductVariant model
    # is created by productvariants field on Component model
    components: "ComponentQuerySet"

    @property
    def cpes(self) -> tuple[str]:
        # Split to fix warning that linter and IDE disagree about
        cpes = (self.cpe,)
        return cpes

    def get_ofuri(self) -> str:
        """Return product variant URI

        Ex.: o:redhat:rhel:8.6.0.z:baseos-8.6.0.z.main.eus
        """
        return f"{self.productstreams.ofuri}:{self.name.lower()}"


class ProductVariantTag(Tag):
    tagged_model = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="tags")


class ProductTaxonomyMixin(models.Model):
    """Add product taxonomy fields and a method to save them for Channels and Components."""

    # related_name like "channels" or "components" - first "s" is a format specifier
    products = models.ManyToManyField(Product, related_name="%(class)ss")
    productversions = models.ManyToManyField(ProductVersion, related_name="%(class)ss")
    productstreams = models.ManyToManyField(ProductStream, related_name="%(class)ss")
    productvariants = models.ManyToManyField(ProductVariant, related_name="%(class)ss")

    class Meta:
        abstract = True

    @abstractmethod
    def save_product_taxonomy(self) -> None:
        pass


class Channel(TimeStampedModel, ProductTaxonomyMixin):
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

    def __str__(self) -> str:
        return str(self.name)

    def save_product_taxonomy(self) -> None:
        """Save taxonomy fields using ancestors of this Channel"""
        ancestors = self.pnodes.get_queryset().get_ancestors()
        products = ProductNode.get_products(ancestors)
        versions = ProductNode.get_product_versions(ancestors)
        streams = ProductNode.get_product_streams(ancestors)
        variants = ProductNode.get_product_variants(ancestors)

        self.products.set(products)
        self.productversions.set(versions)
        self.productstreams.set(streams)
        self.productvariants.set(variants)


class ProductComponentRelation(TimeStampedModel):
    """class to be used for linking taxonomies"""

    class Type(models.TextChoices):
        ERRATA = "ERRATA"
        COMPOSE = "COMPOSE"
        BREW_TAG = "BREW_TAG"
        CDN_REPO = "CDN_REPO"
        YUM_REPO = "YUM_REPO"
        APP_INTERFACE = "APP_INTERFACE"

    # Below not defined in constants to avoid circular imports
    # ProductComponentRelation types which refer to ProductStreams
    STREAM_TYPES = (Type.BREW_TAG, Type.COMPOSE, Type.YUM_REPO, Type.APP_INTERFACE)

    # ProductComponentRelation types which refer to ProductVariants
    VARIANT_TYPES = (Type.CDN_REPO, Type.ERRATA)

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(choices=Type.choices, max_length=50)
    meta_attr = models.JSONField(default=dict)

    # Ex. if Errata Tool this would be errata_id
    external_system_id = models.CharField(max_length=200, default="")
    # E.g. product variant for ERRATA, or product stream for COMPOSE
    product_ref = models.CharField(max_length=200, default="")
    build_id = models.CharField(max_length=200, default="")
    build_type = models.CharField(choices=SoftwareBuild.Type.choices, max_length=20)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                name="unique_productcomponentrelation",
                fields=("external_system_id", "product_ref", "build_id", "build_type"),
            ),
        )
        indexes = (
            models.Index(
                fields=(
                    "external_system_id",
                    "product_ref",
                    "build_id",
                    "build_type",
                )
            ),
            models.Index(
                fields=(
                    "type",
                    "build_id",
                    "build_type",
                )
            ),
            models.Index(
                fields=(
                    "build_id",
                    "build_type",
                )
            ),
            models.Index(fields=("product_ref", "type")),
        )


def get_product_details(variant_names: tuple[str], stream_names: list[str]) -> dict[str, set[str]]:
    """
    Given stream / variant names from the PCR table, look up all related ProductModel subclasses

    In other words, builds relate to variants and streams through the PCR table
    Components relate to builds, and products / versions relate to variants / streams
    We want to link all build components to their related products, versions, streams, and variants
    """
    if variant_names:
        variant_streams = ProductVariant.objects.filter(name__in=variant_names).values_list(
            "productstreams__name", flat=True
        )
        stream_names.extend(variant_streams)

    product_details: dict[str, set[str]] = {
        "products": set(),
        "productversions": set(),
        "productstreams": set(),
        "productvariants": set(),
        "channels": set(),
    }

    for pnode in ProductNode.objects.filter(
        level=MODEL_NODE_LEVEL_MAPPING["ProductStream"], productstream__name__in=stream_names
    ):
        family = pnode.get_family()

        products = ProductNode.get_products(family)
        product_details["products"].update(products)

        product_versions = ProductNode.get_product_versions(family)
        product_details["productversions"].update(product_versions)

        product_streams = ProductNode.get_product_streams(family)
        product_details["productstreams"].update(product_streams)

        product_variants = ProductNode.get_product_variants(family)
        product_details["productvariants"].update(product_variants)

        channels = ProductNode.get_channels(family)
        product_details["channels"].update(channels)
    # For some build, return a mapping of ProductModel type to all related ProductModel UUIDs
    return product_details


class ComponentQuerySet(models.QuerySet):
    """Helper methods to filter QuerySets of Components"""

    def _compare_packages(
        self, nevr_1: tuple[str, EPOCH_TYPE, str, str], nevr_2: tuple[str, EPOCH_TYPE, str, str]
    ) -> int:
        """Use the rpm library to compare 2 epoch/version/release tuples"""
        evr_1 = self._ensure_epoch(nevr_1)
        evr_2 = self._ensure_epoch(nevr_2)
        return labelCompare(evr_1, evr_2)

    @staticmethod
    def _ensure_epoch(evr: tuple[str, EPOCH_TYPE, str, str]) -> tuple[str, str, str]:
        """Ensure the epoch value is set to '0' if unset and also drop the nevra at index 0"""
        # Test for None or 0 int value
        if not evr[1]:
            epoch = "0"
        # We store the meta_attr.epoch as an int, but labelCompare expects a str
        else:
            epoch = str(evr[1])
        return (
            epoch,
            evr[2],
            evr[3],
        )

    def filter_latest_nevra_by_distinct_component(
        self, namespace: str, name: str, arch: str
    ) -> str:
        """Find latest epoch / version / release for a distinct component namespace / name / arch
        and return this latest component's NEVRA"""
        nevras = self.filter(namespace=namespace, name=name, arch=arch).values_list(
            "nevra", "meta_attr__epoch", "version", "release"
        )
        if nevras:
            # Get the latest NEVRA using python. This only works for valid RPM epoch/version/release
            latest_result = sorted(nevras, key=functools.cmp_to_key(self._compare_packages))[-1]
            return latest_result[0]
        else:
            return ""

    def latest_components(
        self,
        include: bool = True,
    ) -> "ComponentQuerySet":
        """Return components from latest builds across all product streams."""
        distinct_components = (
            self.values_list("namespace", "name", "arch").order_by().distinct().iterator()
        )
        query = Q()
        for namespace, name, arch in distinct_components:
            latest_nevra = self.filter_latest_nevra_by_distinct_component(namespace, name, arch)
            if latest_nevra:
                query |= Q(nevra=latest_nevra)
        if include:
            # Show only the latest components
            if not query:
                # No latest components to show??
                # Probably a bug in filter_latest_nevra_by_distinct_component we're not handling
                return Component.objects.none()
            return self.filter(query)
        else:
            # Show only the older / non-latest components
            if not query:
                # No latest components to hide??
                # So show everything / return unfiltered queryset
                # Probably a bug in filter_latest_nevra_by_distinct_component we're not handling
                return self
            return self.exclude(query)

    def latest_components_by_streams(
        self,
        include: bool = True,
    ) -> "ComponentQuerySet":
        """Return only root components from latest builds for each product stream."""
        product_stream_uuids = (
            self.using("read_only")
            .root_components()
            .exclude(productstreams__isnull=True)
            .values_list("productstreams__uuid", flat=True)
            # Clear ordering inherited from parent Queryset, if any
            # So .distinct() works properly and doesn't have duplicates
            .order_by()
            .distinct()
        )
        query = Q()
        for ps_uuid in product_stream_uuids:
            if ps_uuid:
                distinct_components = (
                    self.using("read_only")
                    .root_components()
                    .prefetch_related("productstreams")
                    .filter(productstreams=ps_uuid)
                    .values_list("namespace", "name", "arch")
                    # Clear ordering inherited from parent Queryset, if any
                    # So .distinct() works properly and doesn't have duplicates
                    .order_by()
                    .distinct()
                    .iterator()
                )
                for namespace, name, arch in distinct_components:
                    latest_nevra = (
                        self.using("read_only")
                        .root_components()
                        .prefetch_related("productstreams")
                        # Because we filter by root components,
                        # namespace should always be REDHAT
                        # arch should always be "src" for SRPMs or "noarch" for index containers
                        .filter(name=name, productstreams=ps_uuid)
                        .filter_latest_nevra_by_distinct_component(namespace, name, arch)
                    )
                    if latest_nevra:
                        query |= Q(nevra=latest_nevra)
        if include:
            # Show only the latest components
            if not query:
                # No latest components to show??
                # Probably a bug in filter_latest_nevra_by_distinct_component we're not handling
                return Component.objects.none()
            return self.using("read_only").root_components().filter(query)
        else:
            # Show only the older / non-latest components
            if not query:
                # No latest components to hide??
                # So show everything / return unfiltered queryset
                # Probably a bug in filter_latest_nevra_by_distinct_component we're not handling
                return self
            return self.root_components().exclude(query)

    def released_components(self, include: bool = True) -> "ComponentQuerySet":
        """Show only released components by default, or unreleased components if include=False"""
        empty_released_errata = Q(software_build__meta_attr__released_errata_tags=())
        if include:
            # Truthy values return the excluded queryset (only released components)
            return self.exclude(empty_released_errata)
        # Falsey values return the filtered queryset (only unreleased components)
        return self.filter(empty_released_errata)

    def root_components(self, include: bool = True) -> "ComponentQuerySet":
        """Show only root components by default, or only non-root components if include=False"""
        if include:
            # Truthy values return the filtered queryset (only root components)
            return self.filter(ROOT_COMPONENTS_CONDITION)
        # Falsey values return the excluded queryset (only non-root components)
        return self.exclude(ROOT_COMPONENTS_CONDITION)

    def srpms(self, include: bool = True) -> models.QuerySet["Component"]:
        """Show only source RPMs by default, or only non-SRPMs if include=False"""
        if include:
            # Truthy values return the filtered queryset (only SRPM components)
            return self.filter(SRPM_CONDITION)
        # Falsey values return the excluded queryset (only non-SRPM components)
        return self.exclude(SRPM_CONDITION)


class Component(TimeStampedModel, ProductTaxonomyMixin):
    class Type(models.TextChoices):
        CARGO = "CARGO"  # Rust packages
        CONTAINER_IMAGE = "OCI"  # Container images and other OCI artifacts
        GEM = "GEM"  # Ruby gem packages
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

    RPM_PACKAGE_BROWSER = (
        "https://packages.fedoraproject.org/pkgs/rpm/rpm/"
        if settings.COMMUNITY_MODE_ENABLED
        else "https://access.redhat.com/downloads/content/package-browser"
    )
    CONTAINER_CATALOG_SEARCH = (
        "https://registry.fedoraproject.org/"
        if settings.COMMUNITY_MODE_ENABLED
        else "https://catalog.redhat.com/software/containers/search"
    )

    REMOTE_SOURCE_COMPONENT_TYPES = (
        Type.CARGO,
        Type.GEM,
        Type.GENERIC,
        Type.GITHUB,
        Type.GOLANG,
        Type.MAVEN,
        Type.NPM,
        Type.PYPI,
    )

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    description = models.TextField()
    meta_attr = models.JSONField(default=dict)

    type = models.CharField(choices=Type.choices, max_length=20)
    namespace = models.CharField(choices=Namespace.choices, max_length=20)
    version = models.CharField(max_length=1024)
    release = models.CharField(max_length=1024, default="")
    arch = models.CharField(max_length=1024, default="")

    purl = models.CharField(max_length=1024, default="", unique=True)
    nvr = models.CharField(max_length=1024, default="")
    nevra = models.CharField(max_length=1024, default="")

    # related_name defaults to modelname_set if not specified
    # e.g. an implicit component_set field is added on the Channel / Component model
    # for all channels / downstream components linked to this component
    channels = models.ManyToManyField(Channel)
    upstreams = models.ManyToManyField("Component")

    # sources is the inverse of provides. One container can provide many RPMs
    # and one RPM can have many different containers as a source (as well as modules and SRPMs)
    sources = models.ManyToManyField("Component", related_name="provides")
    provides: models.Manager["Component"]

    # Specify related_query_name to add e.g. component field
    # that can be used to filter from a cnode to its related component
    # TODO: Or just switch from GenericForeignKey to regular ForeignKey?
    cnodes = GenericRelation(ComponentNode, related_query_name="%(class)s")
    software_build = models.ForeignKey(
        SoftwareBuild,
        on_delete=models.CASCADE,
        null=True,
        related_name="components",
        db_column="software_build_uuid",
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

    # these are generated fields eg, we parse version and release on root components to
    # enhance performance of filters (ex. latest)
    version_arr = fields.ArrayField(models.CharField(max_length=200), default=list)
    release_arr = fields.ArrayField(models.CharField(max_length=200), default=list)
    el_match = fields.ArrayField(models.CharField(max_length=200), default=list)

    objects = ComponentQuerySet.as_manager()

    class Meta:
        constraints = (
            models.UniqueConstraint(
                name="unique_components",
                fields=("name", "type", "arch", "version", "release"),
            ),
        )
        indexes = (
            models.Index(fields=("name", "type", "arch", "version", "release")),
            models.Index(fields=("type", "name")),
            models.Index(fields=("nvr",)),
            models.Index(fields=("nevra",)),
            models.Index(fields=("purl",)),
            models.Index(
                fields=("type", "name", "arch"),
                name="compon_latest_name_type_idx",
                condition=ROOT_COMPONENTS_CONDITION,
            ),
            models.Index(
                fields=("name", "type", "arch"),
                name="compon_latest_type_name_idx",
                condition=ROOT_COMPONENTS_CONDITION,
            ),
            models.Index(
                fields=("uuid", "software_build_id", "type", "name", "arch"),
                name="compon_latest_idx",
                condition=ROOT_COMPONENTS_CONDITION,
            ),
        )

    def __str__(self) -> str:
        """return name"""
        return str(self.name)

    def get_purl(self) -> PackageURL:
        if self.type == Component.Type.RPM:
            purl_data = self._build_rpm_purl()
        elif self.type == Component.Type.RPMMOD:
            purl_data = self._build_module_purl()
        elif self.type == Component.Type.CONTAINER_IMAGE:
            purl_data = self._build_container_purl()
        elif self.type == Component.Type.MAVEN:
            purl_data = self._build_maven_purl()

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
            # Don't wipe out Maven / other purl namespaces if they already exist
            existing_namespace = purl_data.get("namespace", "")
            purl_data["namespace"] = f"{Component.Namespace.REDHAT.lower()}/{existing_namespace}"

        return PackageURL(type=str(self.type).lower(), **purl_data)

    def _build_maven_purl(self) -> dict[str, Union[str, dict[str, str]]]:
        qualifiers = {}
        classifier = self.meta_attr.get("classifier")
        if classifier:
            qualifiers["classifier"] = classifier
        extension = self.meta_attr.get("type")
        if extension:
            qualifiers["type"] = extension

        purl_data: dict[str, Union[str, dict[str, str]]] = {
            "name": self.name,
            "version": self.version,
            "qualifiers": qualifiers,
        }
        group_id = self.meta_attr.get("group_id")
        if group_id:
            purl_data["namespace"] = group_id

        return purl_data

    def _build_module_purl(self) -> dict[str, str]:
        # Break down RHEL module version into its specific parts:
        # NSVC = Name, Stream, Version, Context
        version, _, context = self.release.partition(".")
        stream = self.version
        return dict(
            name=self.name,
            version=f"{stream}:{version}:{context}",
        )

    def _build_rpm_purl(self) -> dict[str, Any]:
        qualifiers = {
            "arch": self.arch,
        }
        if self.epoch:
            qualifiers["epoch"] = str(self.epoch)
        # Don't append '-' unless release is populated
        release = f"-{self.release}" if self.release else ""
        return dict(
            name=self.name,
            version=f"{self.version}{release}",
            qualifiers=qualifiers,
        )

    def _build_container_purl(self) -> dict[str, Any]:
        digest = ""
        if self.meta_attr.get("digests"):
            for digest_fmt in CONTAINER_DIGEST_FORMATS:
                digest = self.meta_attr["digests"].get(digest_fmt)
                if digest:
                    break
        # Use the last path of the repository_url if available
        purl_name = self.name
        name_from_label = self.meta_attr.get("name_from_label")
        if name_from_label:
            purl_name = name_from_label
        # Add the tag which matches version+release (check for empty release)
        release = f"-{self.release}" if self.release else ""
        qualifiers = {
            "tag": f"{self.version}{release}",
        }
        # Only add the arch qualify if it's not an image_index
        if self.arch != "noarch":
            qualifiers["arch"] = self.arch
        # Add full repository_url as well
        repository_url = self.meta_attr.get("repository_url")
        if repository_url:
            qualifiers["repository_url"] = repository_url
        # Note that if no container digest format matched, the digest below is ""
        # and the constructed purl has no .version attribute
        return dict(
            name=purl_name,
            version=digest,
            qualifiers=qualifiers,
        )

    def strip_namespace(self, purl: str) -> str:
        """Remove "redhat/" that we prepend to namespace strings in our purls"""
        if self.namespace == Component.Namespace.REDHAT and purl:
            # purls like pkg:golang/redhat/otherstuff cause issues with purl2url
            purl = purl.replace("/redhat/", "/", 1)
        return purl

    def strip_release(self, purl_version: str) -> str:
        """Remove self.release that we append to version strings in our purls"""
        # TODO: Need to look at other enhancements / fixes for our purls
        #  Some of them are missing recommended / necessary information for purl2url
        #  Maybe we should add ?release= as a domain-specific qualifier
        #  Instead of appending to upstream versions
        if self.release and purl_version:
            purl_version = purl_version.replace(f"-{self.release}", "", 1)
        return purl_version

    @staticmethod
    def _build_github_download_url(purl: str) -> str:
        """Return a GitHub download URL from the `purl` string."""
        # TODO: Open PR for this upstream
        # github download urls are just zip files like below:
        # https://github.com/RedHatProductSecurity/django-mptt/archive/commit_hash.zip
        purl_data = PackageURL.from_string(purl)

        namespace = purl_data.namespace
        name = purl_data.name
        version = purl_data.version

        if namespace and name and version:
            return f"https://github.com/{namespace}/{name}/archive/{version}.zip"
        elif name and version:
            # Name should embed namespace if not discovered explicitly
            return f"https://github.com/{name}/archive/{version}.zip"
        return ""

    @classmethod
    def _build_golang_download_url(cls, purl: str) -> str:
        """
        Return a download URL from the `purl` string for golang.
        Due to the non deterministic nature of go package locations
        this function works in a best effort basis.
        """
        # Copied from open PR upstream, not merged yet:
        # https://github.com/package-url/packageurl-python/pull/113/files
        purl_dict = PackageURL.from_string(purl).to_dict()

        namespace = purl_dict.get("namespace")
        name = purl_dict.get("name")
        version = purl_dict.get("version")

        download_url = ""
        # Key sometimes has a None value instead of an empty dict
        qualifiers = purl_dict.get("qualifiers")
        if qualifiers:
            download_url = qualifiers.get("download_url")

        if download_url:
            return download_url

        if not (namespace and name and version):
            return ""

        if "github.com" in namespace:
            namespace = namespace.split("/")

            # if the version is a pseudo version and contains several sections
            # separated by - the last section is a git commit id
            # what should be referred in the tree of the repo
            # https://stackoverflow.com/questions/57355929/
            # what-does-incompatible-in-go-mod-mean-will-it-cause-harm
            if "-" in version:
                version = version.split("-")[-1]

            # if the version refers to a module using semantic versioning,
            # but not opted to use modules it has a
            # '+incompatible' differentiator in the version what can be just omitted in our case.
            # Ref: https://stackoverflow.com/questions/57355929/
            # what-does-incompatible-in-go-mod-mean-will-it-cause-harm
            # Ref: https://github.com/golang/go/wiki/
            # Modules#can-a-module-consume-a-package-that-has-not-opted-in-to-modules
            version = version.replace("+incompatible", "")

            purl_dict["version"] = version
            # If the referred module is in a Github repo,
            # like github.com/user/repo/path/to/module
            # then keep only user as the (github) namespace
            # and keep repo as the (github) component name
            # Ignore the (golang) component name and namespace,
            # and ignore github.com plus any subpaths in the repo
            if len(namespace) >= 2:
                purl_dict["namespace"] = namespace[1]
            if len(namespace) >= 3:
                purl_dict["name"] = namespace[2]
            purl_dict["type"] = "github"
            purl = PackageURL(**purl_dict).to_string()
            return cls._build_github_download_url(purl)

        else:
            return f"https://proxy.golang.org/{namespace}/{name}/@v/{version}.zip"

    @classmethod
    def _build_golang_repo_url(cls, purl: str) -> str:
        """
        Return a golang repository URL from the `purl` string.
        Due to the non deterministic nature of go package locations
        this function works in a best effort basis.
        """
        purl_dict = PackageURL.from_string(purl).to_dict()

        namespace = purl_dict.get("namespace")
        name = purl_dict.get("name")
        version = purl_dict.get("version")

        if not (namespace and name and version):
            return ""

        if "github.com" in namespace:
            namespace = namespace.split("/")

            # if the version is a pseudo version and contains several sections
            # separated by - the last section is a git commit id
            # what should be referred in the tree of the repo
            # https://stackoverflow.com/questions/57355929/
            # what-does-incompatible-in-go-mod-mean-will-it-cause-harm
            if "-" in version:
                version = version.split("-")[-1]

            # if the version refers to a module using semantic versioning,
            # but not opted to use modules it has a
            # '+incompatible' differentiator in the version what can be just omitted in our case.
            # Ref: https://stackoverflow.com/questions/57355929/
            # what-does-incompatible-in-go-mod-mean-will-it-cause-harm
            # Ref: https://github.com/golang/go/wiki/
            # Modules#can-a-module-consume-a-package-that-has-not-opted-in-to-modules
            version = version.replace("+incompatible", "")

            purl_dict["version"] = version
            # If the referred module is in a Github repo,
            # like github.com/user/repo/path/to/module
            # then keep only user as the (github) namespace
            # and keep repo as the (github) component name
            # Ignore the (golang) component name and namespace,
            # and ignore github.com plus any subpaths in the repo
            if len(namespace) >= 2:
                purl_dict["namespace"] = namespace[1]
            if len(namespace) >= 3:
                purl_dict["name"] = namespace[2]
            purl_dict["type"] = "github"
            purl = PackageURL(**purl_dict).to_string()
            return purl2url.get_repo_url(purl) or ""

        else:
            return f"https://pkg.go.dev/{namespace}/{name}@{version}"

    @staticmethod
    def _build_maven_download_url(purl: str) -> str:
        """Return a maven download URL from the `purl` string."""
        # TODO: Open PR for this upstream
        # Based on existing url2purl logic for Maven, and official docs:
        # https://maven.apache.org/repositories/layout.html
        purl_data = PackageURL.from_string(purl)
        central_maven_server = "https://repo1.maven.org/maven2"

        namespace = purl_data.namespace
        if namespace:
            namespace = namespace.split(".")
            namespace = "/".join(namespace)

        name = purl_data.name
        version = purl_data.version
        if version and (".redhat" in version or "-redhat" in version):
            central_maven_server = "https://maven.repository.redhat.com/ga"

        classifier = purl_data.qualifiers.get("classifier")
        classifier = f"-{classifier}" if classifier else ""
        extension = purl_data.qualifiers.get("type")

        if namespace and name and version and extension:
            return (
                f"{central_maven_server}/{namespace}/{name}/{version}/"
                f"{name}-{version}{classifier}.{extension}"
            )

        elif namespace and name and version:
            return f"{central_maven_server}/{namespace}/{name}/{version}"

        else:
            return ""

    @staticmethod
    def _build_maven_repo_url(purl: str) -> str:
        """Return a maven repository URL from the `purl` string."""
        # TODO: Open PR for this upstream
        # Based on existing url2purl logic for Maven, and official docs:
        # https://maven.apache.org/repositories/layout.html
        purl_data = PackageURL.from_string(purl)
        central_maven_server = "https://mvnrepository.com/artifact"

        namespace = purl_data.namespace
        name = purl_data.name
        version = purl_data.version

        if namespace and name and version:
            return f"{central_maven_server}/{namespace}/{name}/{version}"
        return ""

    @staticmethod
    def _build_pypi_download_url(purl: str) -> str:
        """Return a PyPI download URL from the `purl` string."""
        # TODO: Open PR for this upstream
        #  Or don't, this predictable URL is a legacy thing we're not really supposed to use
        # https://stackoverflow.com/questions/47781035/does-pypi-have-simple-urls-for-package-downloads#47840593
        purl_data = PackageURL.from_string(purl)
        central_pypi_server = "https://pypi.io/packages/source"

        name = purl_data.name
        version = purl_data.version
        if name and version:
            return f"{central_pypi_server}/{name[0]}/{name}/{name}-{version}.tar.gz"
        return ""

    def save_component_taxonomy(self):
        """Link related components together using foreign keys. Avoids repeated MPTT tree lookups"""
        upstreams = self.get_upstreams_pks(using="default")
        self.upstreams.set(upstreams)
        self.provides.set(self.get_provides_nodes(using="default"))
        self.sources.set(self.get_sources_nodes(using="default"))

    @property
    def provides_queryset(self, using: str = "read_only") -> Iterator["Component"]:
        """Return the "provides" queryset using the read-only DB, for use in templates"""
        return self.provides.get_queryset().using(using).iterator()

    def is_srpm(self):
        return self.type == Component.Type.RPM and self.arch == "src"

    def get_nvr(self) -> str:
        release = f"-{self.release}" if self.release else ""
        return f"{self.name}-{self.version}{release}"

    def get_nevra(self) -> str:
        epoch = f":{self.epoch}" if self.epoch else ""
        release = f"-{self.release}" if self.release else ""
        arch = f".{self.arch}" if self.arch else ""

        return f"{self.name}{epoch}-{self.version}{release}{arch}"

    def _build_repo_url_for_type(self) -> str:
        """Get an upstream repo URL based on a purl"""
        # Remove Red Hat-specific identifiers that break purl2url
        purl = self.strip_namespace(self.purl)
        purl_dict = PackageURL.from_string(purl).to_dict()
        purl_dict["version"] = self.strip_release(purl_dict["version"])
        purl = PackageURL(**purl_dict).to_string()

        if self.type == Component.Type.GEM:
            # Work around a bug in the library
            purl = purl.replace("pkg:gem/", "pkg:rubygems/")
            related_url = purl2url.get_repo_url(purl)

        elif self.type == Component.Type.GENERIC:
            related_url = self.related_url
            # Usually (15k of 17k) generic upstream components point at Github
            generic_pkg_on_github_http = "pkg:generic/github.com/"
            generic_pkg_on_github_git = "pkg:generic/git%40github.com:"
            github_pkg = "pkg:github/"

            if purl.startswith(generic_pkg_on_github_http):
                purl = purl.replace(generic_pkg_on_github_http, github_pkg)
                related_url = purl2url.get_repo_url(purl) or ""
            elif purl.startswith(generic_pkg_on_github_git):
                purl = purl.replace(generic_pkg_on_github_git, github_pkg)
                related_url = purl2url.get_repo_url(purl) or ""
            # else the component isn't hosted on Github, so we don't know

            if "openshift-priv" in related_url:
                # Sometimes we need to redirect to the public OpenShift repos
                related_url = related_url.replace("openshift-priv", "openshift")

            if not related_url:
                # The component isn't available on Github
                # TODO: We can't build a Cachito link here
                #  We need a human-readable upstream page, not a download link
                #  There's nothing in meta_attr besides RPM ID
                #  We could call getRPMHeaders, but does the RPM's URL header
                #  necessarily match the remote-source component's upstream?
                pass

        elif self.type == Component.Type.GOLANG:
            related_url = self._build_golang_repo_url(purl)

        elif self.type == Component.Type.MAVEN:
            related_url = self._build_maven_repo_url(purl)

        elif self.type in Component.REMOTE_SOURCE_COMPONENT_TYPES:
            # All other remote-source component types are natively supported by purl2url
            related_url = purl2url.get_repo_url(purl)

        else:
            # RPM or OCI have values set on ingestion, don't overwrite them
            # We don't care about RPMMOD
            related_url = ""
        # purl2url.get_download_url() returns None if it failed
        # If so, use the existing value for the field (or the empty string default value) instead
        return related_url if related_url else self.related_url

    def save(self, *args, **kwargs):
        self.nvr = self.get_nvr()
        self.nevra = self.get_nevra()
        purl = self.get_purl()
        self.purl = purl.to_string()
        self.related_url = self._build_repo_url_for_type()

        # generate version_arr, release_arr and el_match (used by filters, ex. latest filter)
        if (self.type == Component.Type.RPM and self.arch == "src") or (
            self.type == Component.Type.CONTAINER_IMAGE and self.arch == "noarch"
        ):
            version_arr = re.split(RELEASE_VERSION_DELIM_RE, self.version)
            if version_arr:
                self.version_arr = version_arr
            release_arr = re.split(RELEASE_VERSION_DELIM_RE, self.release)
            if release_arr:
                self.release_arr = release_arr
            el_match = re.match(EL_MATCH_RE, self.release)
            if el_match:
                self.el_match = [x for x in el_match.groups() if x]

        super().save(*args, **kwargs)

    def save_product_taxonomy(
        self, product_pks_dict: Union[dict[str, QuerySet], None] = None
    ) -> None:
        """
        Save links between ProductModel subclasses and this Component

        product_pks_dict should contain a mapping of ProductModel type to IDs
        These are all the products / versions / etc. that relate to some build
        As determined by the PCR table and ProductModel taxonomy.
        We call save_product_taxonomy() on the build, which calls this method
        for each of the build's related components.
        """
        if not product_pks_dict:
            raise ValueError(
                "Call SoftwareBuild.save_product_taxonomy(),"
                "instead of Component.save_product_taxonomy() directly"
            )
        # Since we're only setting the product details for a specific build id we need
        # to ensure we are only updating, not replacing the existing product details.
        self.products.add(*product_pks_dict["products"])
        self.productversions.add(*product_pks_dict["productversions"])
        self.productstreams.add(*product_pks_dict["productstreams"])
        self.productvariants.add(*product_pks_dict["productvariants"])
        self.channels.add(*product_pks_dict["channels"])
        return None

    def get_roots(self, using: str = "read_only") -> list[ComponentNode]:
        """Return component root entities."""
        roots: list[ComponentNode] = []
        # If a component does not have a softwarebuild that means it's not built at Red Hat
        # therefore it doesn't need its upstreams listed. If we start using the get_roots property
        # for functions other than get_upstreams we might need to revisit this check
        if not self.software_build:
            return roots
        for cnode in self.cnodes.get_queryset().using(using).iterator():
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
                rpm_descendant = (
                    cnode.get_ancestors(include_self=True)
                    .filter(component__type=Component.Type.RPM)
                    .using(using)
                    .exists()
                )
                if not rpm_descendant:
                    roots.append(root)
            else:
                roots.append(root)
        return roots

    @property
    def build_meta(self):
        return self.software_build.meta_attr

    def _build_download_url_for_type(self) -> str:
        """Get a source code or binary download URL based on a purl"""
        # Remove Red Hat-specific identifiers that break purl2url
        purl = self.strip_namespace(self.purl)
        purl_dict = PackageURL.from_string(purl).to_dict()
        purl_dict["version"] = self.strip_release(purl_dict["version"])
        purl = PackageURL(**purl_dict).to_string()

        if self.type == Component.Type.GEM:
            # Work around a bug in the library:
            # https://github.com/package-url/packageurl-python/pull/114
            purl = purl.replace("pkg:gem/", "pkg:rubygems/")
            download_url = purl2url.get_download_url(purl)

        elif self.type == Component.Type.GENERIC:
            # purl2url can't support this type very well, for obvious reasons
            # just return a download URL if the purl has an explicit one
            purl_obj = PackageURL.from_string(purl)
            download_url = purl_obj.qualifiers.get("download_url", "")

            if not download_url:
                # Usually (15k of 17k) generic upstream components point at Github
                generic_pkg_on_github_http = "pkg:generic/github.com/"
                generic_pkg_on_github_git = "pkg:generic/git%40github.com:"
                github_pkg = "pkg:github/"

                if purl.startswith(generic_pkg_on_github_http):
                    purl = purl.replace(generic_pkg_on_github_http, github_pkg)
                    download_url = self._build_github_download_url(purl)
                elif purl.startswith(generic_pkg_on_github_git):
                    purl = purl.replace(generic_pkg_on_github_git, github_pkg)
                    download_url = self._build_github_download_url(purl)
                # else the component isn't hosted on Github, so we don't know

            # if "openshift-priv" in download_url:
            #    Sometimes we need to redirect to the public OpenShift repos
            #    But different repos will have different commit IDs / "versions"
            #    Users should just request access to the OpenShift repo in question
            #    download_url = download_url.replace("openshift-priv", "openshift")

            if not download_url:
                # The component isn't available on Github
                # TODO: Build some download link for an internal service,
                #  but these generic components aren't available in
                #  f"{settings.BREW_DOWNLOAD_ROOT_URL}/packages/"
                #  and I can't figure out how to find them in Cachito
                pass

        elif self.type == Component.Type.GITHUB:
            download_url = self._build_github_download_url(purl)

        elif self.type == Component.Type.GOLANG:
            download_url = self._build_golang_download_url(purl)

        elif self.type == Component.Type.MAVEN:
            download_url = self._build_maven_download_url(purl)

        elif self.type == Component.Type.PYPI:
            download_url = self._build_pypi_download_url(purl)

        elif self.type in Component.REMOTE_SOURCE_COMPONENT_TYPES:
            # All other remote-source component types are natively supported by purl2url
            download_url = purl2url.get_download_url(purl)

        # All other component types are either not currently supported or have no downloadable
        # artifacts (e.g. RHEL module builds).
        else:
            download_url = ""

        # purl2url.get_download_url() returns None if it failed, but we need an empty string
        return download_url if download_url else ""

    @property
    def download_url(self) -> str:
        """Report a URL for RPMs and container images that can be used in manifests"""
        # TODO: Should we eventually make this a stored field on the model
        #  instead of computing the property each time?
        # RPM ex:
        # /downloads/content/aspell-devel/0.60.8-8.el9/aarch64/fd431d51/package
        # We can't build URLs like above because the fd431d51 signing key is required,
        # but isn't available in the meta_attr / other properties (CORGI-342). Get it with:
        # rpm -q --qf %{SIGPGP:pgpsig} aspell-devel-0.60.8-8.el9.x86_64 | tail -c8
        if self.type == Component.Type.RPM:
            return self.RPM_PACKAGE_BROWSER

        # Image ex:
        # /software/containers/container-native-virtualization/hco-bundle-registry/5ccae1925a13467289f2475b
        # /software/containers/openshift/ose-local-storage-diskmaker/
        # 5d9347b8dd19c70159f2f6e4?architecture=s390x&tag=v4.4.0-202007171809.p0
        # We can't build Container Catalog URLs like above because the hash is required,
        # but doesn't match any hash in the meta_attr / other properties.
        # repository_url / related_URL is the customer-facing location,
        # so for now we just build a pull URL from that instead.

        elif self.type == Component.Type.CONTAINER_IMAGE:
            if not self.related_url:
                return self.CONTAINER_CATALOG_SEARCH

            # registry.redhat.io/repo/name:version-release
            release = f"-{self.release}" if self.release else ""
            return f"{self.related_url}:{self.version}{release}"

        else:
            # Usually a remote-source component
            # Anything else (RPMMOD) just returns an empty string
            return self._build_download_url_for_type()

    @property
    def errata(self) -> list[str]:
        """Return errata that contain component."""
        if not self.software_build:
            return []
        errata_qs = (
            ProductComponentRelation.objects.filter(
                type=ProductComponentRelation.Type.ERRATA,
                build_id=self.software_build.build_id,
                build_type=self.software_build.build_type,
            )
            .values_list("external_system_id", flat=True)
            .distinct()
            .using("read_only")
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

    def get_provides_nodes(self, include_dev: bool = True, using: str = "read_only") -> set[str]:
        """return a set of descendant ids with PROVIDES ComponentNode type"""
        # Used in taxonomies. Returns only PKs
        provides_set = set()

        type_list: tuple[ComponentNode.ComponentNodeType, ...] = (
            ComponentNode.ComponentNodeType.PROVIDES,
        )
        if include_dev:
            type_list = ComponentNode.PROVIDES_NODE_TYPES
        for cnode in self.cnodes.get_queryset().iterator():
            provides_set.update(
                cnode.get_descendants()
                .filter(type__in=type_list)
                .using(using)
                .values_list("object_id", flat=True)
                .iterator()
            )
        return provides_set

    def get_provides_nodes_queryset(
        self, include_dev: bool = True, using: str = "read_only"
    ) -> QuerySet[ComponentNode]:
        """return a QuerySet of descendants with PROVIDES ComponentNode type"""
        # Used in manifests. Returns a QuerySet of (node type, linked component UUID)
        type_list: tuple[ComponentNode.ComponentNodeType, ...] = (
            ComponentNode.ComponentNodeType.PROVIDES,
        )
        if include_dev:
            type_list = ComponentNode.PROVIDES_NODE_TYPES

        provides_set = set()
        for cnode in self.cnodes.get_queryset().iterator():
            provides_set.update(
                cnode.get_descendants()
                .filter(type__in=type_list)
                .using(using)
                .values_list("pk", flat=True)
                .iterator()
            )
        return (
            ComponentNode.objects.filter(pk__in=provides_set)
            .using(using)
            .values_list("type", "object_id")
            # Ensure generated manifests only change when content does
            .order_by("object_id")
            .iterator()
        )

    def get_sources_nodes(self, include_dev: bool = True, using: str = "read_only") -> set[str]:
        """Return a set of ancestors ids for all PROVIDES ComponentNodes"""
        sources_set = set()
        type_list: tuple[ComponentNode.ComponentNodeType, ...] = (
            ComponentNode.ComponentNodeType.PROVIDES,
        )
        if include_dev:
            type_list = ComponentNode.PROVIDES_NODE_TYPES
        # Return ancestors of only PROVIDES nodes for this component
        # Sources should be inverse of provides, so don't consider other nodes
        # Inverting "PROVIDES descendants of all nodes" gives "all ancestors of PROVIDES nodes"
        for cnode in self.cnodes.filter(type__in=type_list).iterator():
            sources_set.update(
                cnode.get_ancestors(include_self=False)
                .using(using)
                .values_list("object_id", flat=True)
                .iterator()
            )
        return sources_set

    def get_upstreams_nodes(self, using: str = "read_only") -> list[ComponentNode]:
        """return upstreams component ancestors in family trees"""
        # The get_roots logic is too complicated to use only Django filtering
        # So it has to use Python logic and return a set, instead of a QuerySet
        # which forces us to use Python in this + all other get_upstreams_* methods
        upstreams = []
        roots = self.get_roots(using=using)
        for root in roots:
            # For SRPM/RPMS, and noarch containers, these are the cnodes we want.
            source_descendants = (
                root.get_descendants()
                .filter(type=ComponentNode.ComponentNodeType.SOURCE)
                .using(using)
            )
            # Cachito builds nest components under the relevant source component for that
            # container build, eg. buildID=1911112. In that case we need to walk up the
            # tree from the current node to find its relevant source
            root_obj = root.obj
            if (
                root_obj
                and root_obj.type == Component.Type.CONTAINER_IMAGE
                and root_obj.arch == "noarch"
                and source_descendants.count() > 1
            ):
                upstreams.extend(
                    self.cnodes.get_queryset()
                    .get_ancestors(include_self=True)
                    .filter(
                        type=ComponentNode.ComponentNodeType.SOURCE,
                        component__namespace=Component.Namespace.UPSTREAM,
                    )
                    .using(using)
                    .iterator()
                )
            else:
                upstreams.extend(source_descendants)
        return upstreams

    def get_upstreams_pks(self, using: str = "read_only") -> tuple[str, ...]:
        """Return only the primary keys from the set of all upstream nodes"""
        linked_pks = set(str(node.object_id) for node in self.get_upstreams_nodes(using=using))
        return tuple(linked_pks)

    def get_upstreams_purls(self, using: str = "read_only") -> set[str]:
        """Return only the purls from the set of all upstream nodes"""
        return set(node.purl for node in self.get_upstreams_nodes(using=using))


class ComponentTag(Tag):
    tagged_model = models.ForeignKey(Component, on_delete=models.CASCADE, related_name="tags")


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
        constraints = (
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
                fields=("name", "type", "product", "initial_product_version", "stream"),
                name="unique_lifecycle_entity",
            ),
        )


class RedHatProfile(models.Model):
    """Additional information provided by Red Hat SSO, used for access controls"""

    rhat_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rhat_roles = models.TextField(default="")
    # Storing CN instead of trying to split it into Django's given/first/family/last
    # bc https://www.kalzumeus.com/2010/06/17/falsehoods-programmers-believe-about-names/
    full_name = models.CharField(max_length=256, default="")

    def __str__(self) -> str:
        return f"{self.full_name} <{self.user.email}>"
