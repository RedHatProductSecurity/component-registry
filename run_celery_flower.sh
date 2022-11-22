#!/usr/bin/env bash

exec celery -A config flower --port=9455
