import uuid as uuid

from django.contrib.postgres import fields
from django.db import models


class CollectorErrataModel(models.Model):

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    et_id = models.IntegerField(unique=True)
    name = models.TextField(unique=True)
    meta_attr = models.JSONField(default=dict)

    class Meta:
        abstract = True
        ordering = ["name"]


class CollectorErrataProduct(CollectorErrataModel):
    short_name = models.TextField(unique=True)


class CollectorErrataProductVersion(CollectorErrataModel):
    product = models.ForeignKey(
        CollectorErrataProduct, on_delete=models.CASCADE, related_name="versions"
    )
    brew_tags = fields.ArrayField(models.CharField(max_length=1024), default=list)


class CollectorErrataRelease(CollectorErrataModel):
    brew_tags = fields.ArrayField(models.CharField(max_length=1024), default=list)
    product_versions = models.ManyToManyField(
        CollectorErrataProductVersion, related_name="releases"
    )
    is_active = models.BooleanField(default=False)
    enabled = models.BooleanField(default=False)


class CollectorErrataProductVariant(CollectorErrataModel):
    cpe = models.TextField(default="")
    product_version = models.ForeignKey(
        CollectorErrataProductVersion, on_delete=models.CASCADE, related_name="variants", null=True
    )
    repos = fields.ArrayField(models.CharField(max_length=1024), default=list)


class CollectorRhelModule(models.Model):

    build_id = models.IntegerField(primary_key=True)
    nvr = models.TextField(unique=True)

    def __str__(self):
        return f"{self.nvr}"


class CollectorSRPM(models.Model):

    build_id = models.IntegerField(primary_key=True)

    def __str__(self):
        return f"{self.build_id}"


class CollectorRPM(models.Model):
    """Not every RPM built as part of a SRPM is included in a module
    Which is why we don't directly related SRPM builds to product_streams using relations table"""

    nvra = models.TextField()
    rhel_module = models.ManyToManyField(CollectorRhelModule)
    srpm = models.ForeignKey(CollectorSRPM, on_delete=models.CASCADE, related_name="rpms")

    def __str__(self):
        return f"{self.nvra}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                name="unique_collectorrpms",
                fields=("nvra", "srpm"),
            ),
        ]


class CollectorRPMRepository(models.Model):
    name = models.CharField(unique=True, max_length=200)
    content_set = models.CharField(max_length=200)
    relative_url = models.CharField(max_length=200, default="")

    def __str__(self):
        return f"{self.name}"


class CollectorPyxisModel(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meta_attr = models.JSONField(default=dict)

    class Meta:
        abstract = True
        ordering = ["uuid"]


class CollectorPyxisImage(CollectorPyxisModel):
    # Pyxis doesn't have a distinct Image for the "noarch" container like Corgi has, it only has
    # image objects for the arch specific containers, so we mirror that here. This value represents
    # the sha256 hash from the arch specific container purl version eg:
    # pkg:oci/ose-ovirt-csi-driver-operator@
    # --> sha256:596d18e9a4be00f7c5f1ef536b0d34b3a3a5b826807650cc866bbb7976b237b4 <--
    # ?arch=aarch64
    # &repository_url=registry.redhat.io/openshift4/ose-ovirt-csi-driver-operator
    # &tag=v4.12.0-202301042354.p0.gfeb14fb.assembly.stream
    image_id = models.CharField(max_length=200)
    arch = models.CharField(max_length=20)
    nvr = models.TextField()
    name_label = models.CharField(max_length=200, default="")
    creation_date = models.DateTimeField()
    pyxis_id = models.CharField(unique=True, max_length=50)


class CollectorPyxisImageRepository(CollectorPyxisModel):
    """This model represent one version of a noarch (image index) container in a repository"""

    name = models.CharField(max_length=200)
    registry = models.CharField(max_length=200)
    # We need a many-to-many here because one noarch container has a link to each of it's arch
    # specific binary containers. Also one version of a noarch container can ship to multiple repos
    images = models.ManyToManyField(CollectorPyxisImage, related_name="repos")
    image_advisory_id = models.CharField(max_length=50, default="")
    # This corresponds to the sha256 hash we use to lookup the image index or "noarch" container eg:
    # pkg:oci/ose-ovirt-csi-driver-operator@
    # --> sha256:2b55f17547ddcbdbefddde7372bc3ddfba9b66dfe76d453e479f9efd51514656 <--
    # ?repository_url=registry.redhat.io/openshift4/ose-ovirt-csi-driver-operator
    # &tag=v4.12.0-202301042354.p0.gfeb14fb.assembly.stream
    manifest_list_digest = models.CharField(max_length=200)
    tags = fields.ArrayField(models.CharField(max_length=200), default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                name="unique_pyxis_image_repo",
                fields=["registry", "name", "manifest_list_digest"],
            )
        ]

    def __str__(self):
        return f"{self.registry}/{self.name}"
