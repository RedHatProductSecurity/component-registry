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
    cpe = models.TextField(null=True)  # noqa: DJ01
    product_version = models.ForeignKey(
        CollectorErrataProductVersion, on_delete=models.CASCADE, related_name="variants"
    )


class CollectorComposeRhelModule(models.Model):

    build_id = models.IntegerField(primary_key=True)
    nvr = models.TextField(unique=True)

    def __str__(self):
        return f"{self.nvr}"


class CollectorComposeSRPM(models.Model):

    build_id = models.IntegerField(primary_key=True)

    def __str__(self):
        return f"{self.build_id}"


# Not every RPM built as part of a SRPM is included in a module
# Which is why we don't directly related SRPM builds to product_streams using relations table
class CollectorComposeRPM(models.Model):

    nvr = models.TextField(unique=True)
    rhel_module = models.ForeignKey(
        CollectorComposeRhelModule, on_delete=models.CASCADE, related_name="rpms"
    )
    srpm = models.ForeignKey(CollectorComposeSRPM, on_delete=models.CASCADE, related_name="rpms")

    def __str__(self):
        return f"{self.nvr}"
