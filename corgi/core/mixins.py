import logging

from django.db import models

logger = logging.getLogger(__name__)


class TimeStampedModel(models.Model):
    """Abstract model that auto-sets timestamps on every inherited model."""

    last_changed = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
