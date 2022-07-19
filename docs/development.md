# Developer Guide

[[_TOC_]]

## Project Setup

Install and activate a Python virtual environment:
```bash
> python3.9 -m venv venv  # Create Python virtual environment
> echo "export CORGI_REDIS_URL='redis://localhost:6379'  # This allows running celery inspect commands in a local shell" >> venv/bin/activate
> source venv/bin/activate  # Enable virtual env
> pip install pip-tools  # Install pip-tools
> pip-sync requirements/dev.txt
```

Alternatively, replace the pip-sync call with:
```bash
> pip install -r requirements/dev.txt
```

Next, define the database password and custom PostgreSQL port to be used:

```bash
export CORGI_DB_USER=postgres  # This is the RHSCL PostgreSQL image default admin username
export CORGI_DB_PASSWORD=secret  # This is the admin password used in docker-compose.yml
export CORGI_DB_PORT=5433  # This is the port used in docker-compose.yml
export DJANGO_SETTINGS_MODULE=config.settings.dev
export REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt  # or w/e bundle contains at least the internal root CA cert
```

Internal URLs are set via environment variables, to avoid leaking sensitive data in our public GitHub repo's history.
Copy the URLs needed to run tests from the internal Gitlab server's CI variables:
```bash
# Internal hostnames or URLs that appear in build metadata; used in tests
export CORGI_TEST_CACHITO_URL
export CORGI_TEST_CODE_URL
export CORGI_TEST_DOWNLOAD_URL
export CORGI_TEST_OSBS_HOST1
export CORGI_TEST_OSBS_HOST2
export CORGI_TEST_OSBS_HOST3
export CORGI_TEST_REGISTRY_URL
# Not used in tests directly, but needed for tests to pass
export CORGI_LOOKASIDE_CACHE_URL
export CORGI_APP_STREAMS_LIFE_CYCLE_URL
export CORGI_BREW_URL
export CORGI_BREW_DOWNLOAD_ROOT_URL
export CORGI_ERRATA_TOOL_URL
export CORGI_MANIFEST_HINTS_URL
export CORGI_PRODSEC_DASHBOARD_URL
# The internal Nexus PyPI mirror is used to avoid overloading the public PyPI service
export PIP_INDEX_URL
# The team mailing list is private, so we default to using secalert@redhat.com as our public contact address instead
export PRODSEC_EMAIL
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
export CORGI_FAILED_CELERY_TASK_SUBSCRIBERS
export CORGI_SERVER_EMAIL
export CORGI_UMB_BROKER_URL
```

It is recommended to add all the aforementioned environment variables to your virtual
environment's `venv/bin/activate` script.

In order for the environment variables to be passed into the celery pods started by podman-compose you'll also have to
add them to a .env file eg.
```bash
CORGI_APP_STREAMS_LIFE_CYCLE_URL=<value>
CORGI_BREW_DOWNLOAD_ROOT_URL=<value>
CORGI_BREW_URL=<value>
CORGI_ERRATA_TOOL_URL=<value>
CORGI_MANIFEST_HINTS_URL=<value>
CORGI_PRODSEC_DASHBOARD_URL=<value>
CORGI_LOOKASIDE_CACHE_URL=<value>
```

Build container images:
```bash
> podman-compose build
```

To start system locally
```bash
> podman-compose up -d
```

The application should be available on http://localhost:8008.

To shut down and clean up: 
```bash
> podman-compose down -v  # Also removes data volume
```

### Running the Development Shell

Ensure you have environment variables defined as noted in "Project Setup"; then run:

```bash
./manage.py shell
```

### Running tests

To run the full complement of tests and linters:
```
tox
```
This target should be run before making any commits.

Tox accepts additional arguments, for example to select to run just unit tests:
```
tox -e corgi -- -m "unit"
```
Alternatively, you can always run individual tests:
```
tox -e corgi -- tests/test_model.py::test_product_model
```

By default, `VCR.py` is run with '[record-mode=once](https://vcrpy.readthedocs.io/en/latest/usage.html#once)'
under `testenv:corgi`. To overwrite/renew existing cassette files, run:
```
tox -e corgi-vcr-record-rewrite
```

Remember to commit and push if there is a newly generated cassette file. 
