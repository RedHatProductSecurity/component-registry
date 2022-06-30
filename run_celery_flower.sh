#!/usr/bin/env bash

exec celery -A config flower
