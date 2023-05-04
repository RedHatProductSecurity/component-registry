import uuid as uuid

from django.contrib.postgres import fields
from django.db import models


class CollectorErrataModel(models.Model):

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    et_id = models.IntegerField(unique=True)
    name = models.TextField(unique=True)

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


class CollectorErrataProductVariant(CollectorErrataModel):
    cpe = models.TextField(default="")
    product_version = models.ForeignKey(
        CollectorErrataProductVersion, on_delete=models.CASCADE, related_name="variants"
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
