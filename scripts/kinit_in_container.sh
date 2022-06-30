#!/usr/bin/env bash

if [[ -z "$1" ]]; then
    user=$(whoami)@REDHAT.COM
else
    user="$1"@REDHAT.COM
fi

# Only ask the password one time here and reuse in each pod
echo -n "Password for ${user}: "; read -s password; echo

celery_pods=$(podman ps -f name=corgi-celery -q)
for celery_pod in $celery_pods; do
    podman exec -it $celery_pod /bin/bash -c "echo ${password} | kinit ${user}"
done
