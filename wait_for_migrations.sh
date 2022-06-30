#!/usr/bin/bash

while true; do
    python3 manage.py showmigrations | grep '\[ \]' > /dev/null
    if [ $? -eq 1 ]; then
        # No unapplied migrations found
        exit 0
    fi
    sleep 1s
done
