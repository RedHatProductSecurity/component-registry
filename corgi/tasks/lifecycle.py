import logging
from datetime import datetime

from django.db import transaction

from config.celery import app
from corgi.collectors.appstream_lifecycle import AppStreamLifeCycleCollector
from corgi.core.models import AppStreamLifeCycle

logger = logging.getLogger(__name__)


@app.task
@transaction.atomic
def update_appstream_lifecycles() -> None:
    lifecycles = AppStreamLifeCycleCollector.get_lifecycle_defs()
    logger.debug("Saving %s app stream lifecycle definitions.", len(lifecycles))
    for entry in lifecycles:
        # Core data
        name = entry.get("name")
        type_ = entry.get("type")
        product = entry.get("product")
        initial_product_version = entry.get("initial_product_version")
        stream = entry.get("stream")

        # Lifecycle dates
        start_date = entry.get("startdate")
        if start_date is not None:
            start_date = datetime.strptime(start_date, "%Y%m%d")
        end_date = entry.get("enddate")
        if end_date == "11111111":
            # Represents a rolling lifecycle stream; also marked as lifecycle==0, so we don't
            # need to store this fake date value.
            end_date = None
        else:
            end_date = datetime.strptime(end_date, "%Y%m%d")

        defaults = {
            "acg": entry.get("acg"),
            "start_date": start_date,
            "end_date": end_date,
            "lifecycle": entry.get("lifecycle"),
            "source": entry.get("source"),
            "private": entry.get("private"),
        }
        AppStreamLifeCycle.objects.update_or_create(
            name=name,
            type=type_,
            product=product,
            initial_product_version=initial_product_version,
            stream=stream,
            defaults=defaults,
        )
