FROM registry.redhat.io/ubi8/ubi:8.6

ARG PIP_INDEX_URL="https://pypi.org/simple"
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_INDEX_URL="${PIP_INDEX_URL}" \
    REQUESTS_CA_BUNDLE="/etc/pki/tls/certs/ca-bundle.crt"

LABEL maintainer="Red Hat Product Security Dev - Red Hat, Inc." \
      vendor="Red Hat Product Security Dev - Red Hat, Inc." \
      summary="Red Hat Component Registry (Corgi) application image" \
      distribution-scope="private"

ARG ROOT_CA_URL
RUN cd /etc/pki/ca-trust/source/anchors/ && \
    # The '| true' skips this step if the ROOT_CA_URL is unset or fails in another way
    curl -O "${ROOT_CA_URL}" | true && \
    update-ca-trust && \
    cd -

WORKDIR /opt/app-root/src/
COPY ./requirements/rpms.txt ./requirements/rpms.txt

RUN dnf --nodocs -y install --setopt install_weak_deps=false $(grep '^[^#]' ./requirements/rpms.txt) \
    && dnf clean all

COPY ./requirements ./requirements

# Install Python package dependencies from requirements file passed in PIP_REQUIREMENT (local
# docker-compose may override this in the build step).
ARG PIP_REQUIREMENT="./requirements/base.txt"
RUN python3.9 -m pip install -r "${PIP_REQUIREMENT}"

# Limit copied files to only the ones required to run the app
COPY ./files/krb5.conf /etc
COPY ./*.sh ./*.py ./
COPY ./config ./config
COPY ./corgi ./corgi

RUN chgrp -R 0 /opt/app-root && \
    chmod -R g=u /opt/app-root
