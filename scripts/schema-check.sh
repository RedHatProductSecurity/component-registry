#!/usr/bin/env bash
./manage.py spectacular --file openapi.yml --settings=config.settings.test &> /dev/null && git diff --quiet openapi.yml &> /dev/null
