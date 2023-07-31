#!/usr/bin/env bash

exec celery -A config flower --loglevel info --port=9455
