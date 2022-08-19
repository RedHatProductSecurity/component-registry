#!/usr/bin/env bash

while true; do
    python3 manage.py showmigrations | grep -v '\[ \]' > /dev/null
    if [ $? -eq 0 ]; then
        # No unapplied migrations found
        exit 0
    fi
    sleep 1s
done
