FROM registry.redhat.io/ubi9/ubi

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

# Create a virtual env and activate it (by setting the necessary env vars) so python3 points to
# python3.11 within the venv (otherwise we'd have to update all uses of python3 to python3.11).
ENV VIRTUAL_ENV=/opt/app-root/src/venv
RUN python3.11 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install Python package dependencies from requirements file passed in PIP_REQUIREMENT (local
# docker-compose overrides this in the build step). First, install build dependencies of the gssapi
# package and use no build isolation in the pip install step to prevent these errors:
#
# In --require-hashes mode, all requirements must have their versions pinned with ==
#
# which are caused by the gssapi package not specifying hashes for its build dependencies:
# https://github.com/pythongssapi/python-gssapi/blob/b15b1394/pyproject.toml#LL3C5-L3C33
# Using build isolation would try to install those deps in a separate ephemeral venv where they
# would be installed without hashes, causing the entire pip install command to fail.
RUN python3 -m pip install -r requirements/gssapi_build.txt
ARG PIP_REQUIREMENT="requirements/base.txt"
RUN python3 -m pip install --no-build-isolation

# Limit copied files to only the ones required to run the app
COPY ./files/krb5.conf /etc
COPY ./*.sh ./*.py ./
COPY ./config ./config
COPY ./corgi ./corgi

RUN chgrp -R 0 /opt/app-root && \
    chmod -R g=u /opt/app-root
