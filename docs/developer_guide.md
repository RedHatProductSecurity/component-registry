# Developer Guide

## Project Setup

Install and activate a Python virtual environment:
```bash
> python3.11 -m venv venv  # Create Python virtual environment
> echo "export CORGI_REDIS_URL='redis://localhost:6379'  # This allows running celery inspect commands in a local shell" >> venv/bin/activate
> source venv/bin/activate  # Enable virtual env
> pip install pip-tools  # Install pip-tools
> pip-sync requirements/dev.txt
```

You will need to install at least some of the rpm dependencies from requirements/rpms.txt. On Fedora 38 Workstation for example:

```bash
> dnf install gcc krb5-devel krb5-workstation libpq-devel golang

Alternatively, replace the pip-sync call with:
```bash
> pip install -r requirements/dev.txt
```

Next, define the database password and custom PostgreSQL port to be used:

```bash
export CORGI_DB_USER=postgres  # This is the RHSCL PostgreSQL image default admin username
export CORGI_DB_PASSWORD=test  # This is the admin password used in docker-compose.yml
export CORGI_DB_PORT=5433  # This is the port used in docker-compose.yml
export DJANGO_SETTINGS_MODULE=config.settings.dev
export CORGI_COMMUNITY_MODE_ENABLED=true
```

If you're working on the enterprise version, you'll also need to set request to use the enterprise CA certificate

```bash
export CORGI_COMMUNITY_MODE_ENABLED=false
export REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt  # or w/e bundle contains at least the internal root CA cert
```

Internal URLs are set via environment variables, to avoid leaking sensitive data in our public GitHub repo's history.
The following values are populated as examples, but also allow to run in community mode. If running in enterprise mode,
copy the URLs needed to run tests from the internal Gitlab server's CI variables:
```bash
# Internal hostnames or URLs that appear in build metadata; used in tests
export CORGI_APP_INTERFACE_URL
export CORGI_PULP_URL=https://rhsm-pulp.example.com/pulp
# Not used in tests directly, but needed for tests to pass
export CORGI_BREW_URL=https://koji.fedoraproject.org/kojihub
export CORGI_BREW_DOWNLOAD_ROOT_URL=https://koji.fedoraproject.org
export CORGI_CENTOS_URL=https://cbs.centos.org/kojihub
export CORGI_CENTOS_DOWNLOAD_ROOT_URL=https://cbs.centos.org
export CORGI_LOOKASIDE_CACHE_URL=https://src.fedoraproject.org/repo/pkgs
export CORGI_APP_INTERFACE_URL="https://app-interface.example.com"
export CORGI_APP_STREAMS_LIFE_CYCLE_URL=https://appstream.example.com/lifecycle-defs/application_streams.yaml
export CORGI_ERRATA_TOOL_URL=https://errata.example.com
export CORGI_MANIFEST_HINTS_URL=https://manifesthints.example.com/manifest-hints.txt
export CORGI_PRODSEC_DASHBOARD_URL=https://dashboard.example.com/rest/api/latest
export CORGI_PYXIS_GRAPHQL_URL=https://catalog.example.com/api/containers
export CORGI_UMB_CERT=/path/to/cert.crt
export CORGI_UMB_KEY=/path/to/key.key
export PIP_INDEX_URL=https://pypi.org/simple
```

If you're working on the enterprise version, you'll also need the following options set:

```bash
# The internal root CA certificate is needed to use the Nexus PyPI mirror and other internal Red Hat services
export ROOT_CA_URL
```

Some URLs are only used when running the service locally, not in the test suite.
Copy these values from the private corgi-ops repo:
```bash
export CORGI_ADMINS
export CORGI_DOCS_URL
export CORGI_DOMAIN
export CORGI_EMAIL_HOST
export CORGI_SERVER_EMAIL
export CORGI_UMB_BROKER_URL
export CORGI_PULP_USERNAME
export CORGI_PULP_PASSWORD
```

It is recommended to add all the aforementioned environment variables to your virtual
environment's `venv/bin/activate` script.

In order for the environment variables to be passed into the celery pods started by podman-compose you'll also have to
add them to a .env file e.g.:
```bash
DJANGO_SETTINGS_MODULE=config.settings.dev
CORGI_BREW_DOWNLOAD_ROOT_URL=https://kojipkgs.fedoraproject.org
CORGI_BREW_URL=https://koji.fedoraproject.org/kojihub
CORGI_LOOKASIDE_CACHE_URL=https://src.fedoraproject.org/repo/pkgs
CORGI_COMMUNITY_MODE_ENABLED=true
CORGI_UMB_BREW_MONITOR_ENABLED=false
```


If doing enterprise development, be sure to set this to relevant value from CI environment.
If it's not set the local product-definitions.json file in the config directory will be used.
```bash
CORGI_COMMUNITY_MODE_ENABLED=false
CORGI_UMB_BREW_MONITOR_ENABLED=true
CORGI_PRODSEC_DASHBOARD_URL=<value>
CORGI_BREW_WEB_URL=<value>
```

It is recommended to add all the aforementioned environment variables to a `.env` file in the project root directory.

Build container images:
```bash
podman-compose build
```

To start system locally
```bash
podman-compose up -d
```

The application should be available on http://localhost:8080.

To shut down and clean up:
```bash
podman-compose down -v  # Also removes data volume
```

### Running the Development Shell

Ensure you have environment variables defined as noted in "Project Setup"; then run:

```bash
./manage.py shell
```

### Running Tests

To run the full complement of tests and linters:
```bash
tox
```

This target should be run before making any commits.

Tox accepts additional arguments, for example to select to run just performance tests:
```bash
tox -e corgi -- -m performance
```

Alternatively, you can always run individual tests:
```bash
tox -e corgi -- tests/test_model.py::test_product_model
```
