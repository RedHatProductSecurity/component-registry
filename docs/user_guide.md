# Component Registry User Guide

## Interacting with the REST API

Component Registry exposes a REST API that any number of clients can connect to, from cURL to a custom-made
front-end application to serve as a web client.

For more in-depth information about the resources served by the API, see section
(REST API Resource Definitions)[#rest-api-resource-definitions].

The [OpenAPI specification](https:///github.com/RedHatProductSecurity/corgi/-/blob/master/openapi.yml) provides
developer level documentation for endpoint usage.

### Fetching data

[REST API docs](https:///github.com/RedHatProductSecurity/corgi/-/blob/main/openapi.yml) provide detailed
usage on all endpoints.

#### Retrieving components

Most endpoints provide a paginated data response.

##### cURL
```bash
$ curl "https://${CORGI_DOMAIN}/api/v1/components"
```

##### python
```python
import requests

response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components")
response.raise_for_status()
```

#### Retrieving component detail

Components are addressable by a unique id (UUID).

##### cURL

```bash
$ curl "https://${CORGI_DOMAIN}/api/v1/components/2fe16efb-11cb-4cd2-b31b-d769ba821073"
```

##### python

```python
import requests

component_id = "2fe16efb-11cb-4cd2-b31b-d769ba821073"
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components/{component_id}")
response.raise_for_status()
```

### Searching for components

#### Filtering by specific field

##### cURL
```bash
$ curl "https://${CORGI_DOMAIN}/api/v1/components?name=curl"
```

##### python
```python
import requests

params = {"name": "curl"}
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components", params=params)
response.raise_for_status()
```

Which will return any components with the name `curl`.

Some URL parameters provide regular expression matching (prefixed by `re_`).

##### cURL
```bash
$ curl "https://${CORGI_DOMAIN}/api/v1/components?re_name=^curl$"
```

##### python
```python
import requests

params = {"re_name": "^curl$"}
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components", params=params)
response.raise_for_status()
```

#### Full text search

You may also perform full text search:

##### cURL
```bash
$ curl "https://${CORGI_DOMAIN}/api/v1/components?search=openjdk
```

##### python

```python
import requests

params = {"search": "openjdk"}
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components", params=params)
response.raise_for_status()
```

## REST API Resource Definitions

### Product Data

Product metadata is defined in a hierarchy where each parent can have one or more children:
- `product`: RHEL or OpenShift
- `product_version`: RHEL 7
- `product_stream`: RHEL 7.9.z
- `product_variant`: RHEL 7.9.z Workstation

Each of these have a unique identity defined in the `ofuri` (Offering URI) attribute (akin to `purl` for components).

### Build Data

A `build` tracks the composition of sources into artifacts. It is an abstract entity that contains information about
how a set of components was built, and what those components are, when and where they were built, and what source
was used to build them.

### Component Data

The following is an example of one component (with some data omitted for brevity):

```bash
{
    "link": "https://corgi-stage.prodsec.redhat.com/api/v1/components?purl=pkg%3Arpm/redhat/rh-nodejs12-npm%406.14.16-12.22.12.2.el7%3Farch%3Daarch64",
    "download_url": "$BREW_WEB_URL/vol/rhel-7/packages/rh-nodejs12-nodejs/6.14.16/12.22.12.2.el7/aarch64/rh-nodejs12-npm-6.14.16-12.22.12.2.el7.aarch64.rpm",
    "uuid": "e3d37dc3-7469-4d52-a50c-4b7f36113511",
    "type": "RPM",
    "purl": "pkg:rpm/redhat/rh-nodejs12-npm@6.14.16-12.22.12.2.el7?arch=aarch64",
    "name": "rh-nodejs12-npm",
    "description": "npm is a package manager for node.js. You can use it to install and publish\nyour node programs. It manages dependencies and does other cool stuff.",
    "related_url": "http://nodejs.org/",
    "tags": [],
    "version": "6.14.16",
    "release": "12.22.12.2.el7",
    "arch": "aarch64",
    "nvr": "rh-nodejs12-npm-6.14.16-12.22.12.2.el7",
    "nevra": "rh-nodejs12-npm-6.14.16-12.22.12.2.el7.aarch64",
    "epoch": 0,
    "license": "MIT and ASL 2.0 and ISC and BSD",
    "license_list": [
        "MIT",
        "ASL 2.0",
        "ISC",
        "BSD"
    ],
    "software_build": {
        "link": "https://corgi-stage.prodsec.redhat.com/api/v1/builds/2034513",
        "build_id": 2034513,
        "type": "BREW",
        "name": "rh-nodejs12-nodejs",
        "source": "git://pkgs.devel.redhat.com/rpms/nodejs#dba41e058293ae79f9b239b6f49c50e5d70f88d3"
    },
    "errata": [],
    "products": [
        {
            "ofuri": "o:redhat:rhscl",
            "link": "https://corgi-stage.prodsec.redhat.com/api/v1/products?ofuri=o:redhat:rhscl&type=SRPM&limit=3000",
            "name": "rhscl"
        }
    ],
    "product_versions": [
        {
            "ofuri": "o:redhat:rhscl:3",
            "link": "https://corgi-stage.prodsec.redhat.com/api/v1/product_versions?ofuri=o:redhat:rhscl:3&type=SRPM&limit=3000",
            "name": "rhscl-3"
        }
    ],
    "product_streams": [
        {
            "ofuri": "o:redhat:rhscl:3.8.z",
            "link": "https://corgi-stage.prodsec.redhat.com/api/v1/product_streams?ofuri=o:redhat:rhscl:3.8.z&type=SRPM&limit=3000",
            "name": "rhscl-3.8.z"
        },
        {
            "ofuri": "o:redhat:rhscl:3.9",
            "link": "https://corgi-stage.prodsec.redhat.com/api/v1/product_streams?ofuri=o:redhat:rhscl:3.9&type=SRPM&limit=3000",
            "name": "rhscl-3.9"
        }
    ],
    "product_variants": [],
    "sources": [
        {
            "link": "https://corgi-stage.prodsec.redhat.com/api/v1/components?purl=pkg%3Asrpm/redhat/rh-nodejs12-nodejs%4012.22.12-2.el7%3Farch%3Dsrc",
            "purl": "pkg:srpm/redhat/rh-nodejs12-nodejs@12.22.12-2.el7?arch=src"
        }
    ],
    "provides": [
        {
            "link": "https://corgi-stage.prodsec.redhat.com/api/v1/components?purl=pkg%3Anpm/lodash.restparam%403.6.1",
            "purl": "pkg:npm/lodash.restparam@3.6.1"
        },
        {
            "link": "https://corgi-stage.prodsec.redhat.com/api/v1/components?purl=pkg%3Anpm/wcwidth%401.0.1",
            "purl": "pkg:npm/wcwidth@1.0.1"
        },
        [...SNIP...]
    "upstreams": []
}
```

The following is a listing of most of the attributes shown above with a description for each:

- `link`: points to the web URL for the component.

- `download_url`: a URL from which this component can be downloaded.

- `uuid`: a unique identifier of a component (note that this ID may change if we refresh the data in the database;
  use purl for identity instead).

- `type`: the component's type as listed in `corgi/core/models.py::Component.Type`. Note that the package types
  specified on the Component entity may differ from those used in the purl string. We are still finalizing the usage
  of purl across all components and collating the types across various systems is a future goal.

- `purl`: a unique identifier of a component (mostly) following the [purl spec](https://github.com/package-url/purl-spec).

- `name`, `description`: self-explanatory

- `related_url`: if this is an upstream component, this URL may point to the associated location for that component,
   e.g. for an OpenSSL component it may point to https://www.openssl.org.

- `tags`: a list of user-defined tags applied on this component.

- `version`, `release`, `arch`, `nvr`: `nevra`, `epoch`: attributes of a component that combined with the name
  identify it uniquely; `nvr` and `nevra` are frequently-used combinations of these attributes.
-
- `license`: the license string as it is included in the component's spec file.
-
- `license_list`: the license string parsed into its individual components.
-
- `software_build`: a minimal representation of the build that produced this component.

- `errata`: a list of any errata that shipped this component.

- `products`, `product_versions`, `product_streams`, `product_variants`: a listing of product-related metadata as
  defined in the section above. Some components may be missing their product data, which may indicate that they are
  still unshipped, and we have no way of associating that build to a specific products based on its Brew tags or errata.

- `sources`: a list of parent components that are either the source of this component or this component is embedded in.

- `provides`: an inverse relationship to the `sources` list, a list of components that this component provides,
   either as components built from this component, or components embedded in this component.

- `upstreams`: a list of upstream sources that were used to build this component. This can be sources stored in
   dist-git, or pulled directly from upstream (e.g. Go dependencies pulled from GitHub and stored in Cachito).

### Manifests

Each product-level entity has a `/manifest` endpoint that takes a list of components belonging to that entity and
generates an SPDX manifest for all of them.
