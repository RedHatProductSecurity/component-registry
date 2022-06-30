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
    cpe = models.TextField(null=True)
    product_version = models.ForeignKey(
        CollectorErrataProductVersion, on_delete=models.CASCADE, related_name="variants"
    )
