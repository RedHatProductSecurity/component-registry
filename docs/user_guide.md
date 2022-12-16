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
$ curl "https://${CORGI_HOST}/api/v1/components"
```

##### python
```python
import requests

response = requests.get(f"https://{CORGI_HOST}/api/v1/components")
response.raise_for_status()
```

#### Retrieving component detail

Components are addressable by a unique id (UUID) or [Package URL (purl)](https://github.com/package-url/purl-spec/). 
UUID is subject to change so it's best to refer to a component by it's purl. Component purl lookups are redirected to
the UUID addresses listed below.

##### cURL

```bash
curl -L https://${CORGI_HOST}/api/v1/components?purl=pkg:npm/is-svg@2.1.0
```

```bash
$ curl "https://${CORGI_HOST}/api/v1/components/2fe16efb-11cb-4cd2-b31b-d769ba821073"
```

##### python

```python
import requests
purl = "pkg://npm/is-svg@2.1.0"
response = requests.get(f"https://{CORGI_HOST}/api/v1/components?purl={purl}")
response.raise_for_status()
```

```python
import requests

component_id = "2fe16efb-11cb-4cd2-b31b-d769ba821073"
response = requests.get(f"https://{CORGI_HOST}/api/v1/components/{component_id}")
response.raise_for_status()
```

### Searching for components

#### Filtering by specific field

##### cURL
```bash
$ curl "https://${CORGI_HOST}/api/v1/components?name=curl"
```

##### python
```python
import requests

params = {"name": "curl"}
response = requests.get(f"https://{CORGI_HOST}/api/v1/components", params=params)
response.raise_for_status()
```

Which will return any components with the name `curl`.

Some URL parameters provide regular expression matching (prefixed by `re_`).

##### cURL
```bash
$ curl "https://${CORGI_HOST}/api/v1/components?re_name=^curl$"
```

##### python
```python
import requests

params = {"re_name": "^curl$"}
response = requests.get(f"https://{CORGI_HOST}/api/v1/components", params=params)
response.raise_for_status()
```

#### Full text search

You may also perform full text search:

##### cURL
```bash
$ curl "https://${CORGI_HOST}/api/v1/components?search=openjdk
```

##### python

```python
import requests

params = {"search": "openjdk"}
response = requests.get(f"https://{CORGI_HOST}/api/v1/components", params=params)
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
    "link": "https://$CORGI_HOST/api/v1/components?purl=pkg%3Arpm/redhat/rh-nodejs12-npm%406.14.16-12.22.12.2.el7%3Farch%3Daarch64",
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
        "link": "https://$CORGI_HOST/api/v1/builds/2034513",
        "build_id": 2034513,
        "type": "BREW",
        "name": "rh-nodejs12-nodejs",
        "source": "git://pkgs.example.com/rpms/nodejs#dba41e058293ae79f9b239b6f49c50e5d70f88d3"
    },
    "errata": [],
    "products": [
        {
            "ofuri": "o:redhat:rhscl",
            "link": "https://$CORGI_HOST/api/v1/products?ofuri=o:redhat:rhscl&type=SRPM&limit=3000",
            "name": "rhscl"
        }
    ],
    "product_versions": [
        {
            "ofuri": "o:redhat:rhscl:3",
            "link": "https://$CORGI_HOST/api/v1/product_versions?ofuri=o:redhat:rhscl:3&type=SRPM&limit=3000",
            "name": "rhscl-3"
        }
    ],
    "product_streams": [
        {
            "ofuri": "o:redhat:rhscl:3.8.z",
            "link": "https://$CORGI_HOST/api/v1/product_streams?ofuri=o:redhat:rhscl:3.8.z&type=SRPM&limit=3000",
            "name": "rhscl-3.8.z"
        },
        {
            "ofuri": "o:redhat:rhscl:3.9",
            "link": "https://$CORGI_HOST/api/v1/product_streams?ofuri=o:redhat:rhscl:3.9&type=SRPM&limit=3000",
            "name": "rhscl-3.9"
        }
    ],
    "product_variants": [],
    "sources": [
        {
            "link": "https://$CORGI_HOST/api/v1/components?purl=pkg%3Asrpm/redhat/rh-nodejs12-nodejs%4012.22.12-2.el7%3Farch%3Dsrc",
            "purl": "pkg:srpm/redhat/rh-nodejs12-nodejs@12.22.12-2.el7?arch=src"
        }
    ],
    "provides": [
        {
            "link": "https://$CORGI_HOST/api/v1/components?purl=pkg%3Anpm/lodash.restparam%403.6.1",
            "purl": "pkg:npm/lodash.restparam@3.6.1"
        },
        {
            "link": "https://$CORGI_HOST/api/v1/components?purl=pkg%3Anpm/wcwidth%401.0.1",
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

- `license`: the license string as it is included in the component's spec file.

- `license_list`: the license string parsed into its individual components.

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

### Example Use cases

#### Find product streams and root-level components containing a specific artifact version

Take for example `NPM` artifact `is-svg` version `2.1.0`

If you know the exact purl syntax you can search for it directly. Notice I added the -L flag to curl which follows redirects.

```bash
curl -s -L https://${CORGI_HOST}/api/v1/components?purl=pkg:npm/is-svg@2.1.0
```

Alternatively use the type, name and version fields:

```bash
curl -s https://${CORGI_HOST}/api/v1/components?type=NPM&name=is-svg&version=2.1.0
```

This query returns a list of results include the component count. The component data can be found in the results field.
The sources field lists all versions of all components which embed this component, it's useful to process the results on the client side to get a clearer picture of the packages included:

```bash
$ curl -L -s https://${CORGI_HOST}/api/v1/components?purl=pkg:npm/is-svg@2.1.0 | jq '.sources[] | .purl' | awk -F@ '{print $1}' | cut -c2- | sort | uniq
pkg:container/redhat/console-ui-container
pkg:container/redhat/devspaces-machineexec-rhel8-container
pkg:container/redhat/devspaces-theia-rhel8-container
pkg:container/redhat/grafana-container
pkg:container/redhat/grafana-container-source
pkg:container/redhat/openshift-enterprise-console-container
pkg:container/redhat/openshift-enterprise-console-container-source
pkg:container/redhat/quay-registry-container
pkg:rpm/redhat/cfme-gemset
pkg:rpm/redhat/cockpit-ceph-installer
pkg:rpm/redhat/cockpit-ovirt
pkg:rpm/redhat/dotnet3.1
pkg:rpm/redhat/dotnet5.0
pkg:rpm/redhat/firefox
pkg:rpm/redhat/foreman
pkg:rpm/redhat/grafana
pkg:rpm/redhat/kibana
pkg:rpm/redhat/mozjs60
pkg:rpm/redhat/ovirt-engine-api-explorer
pkg:rpm/redhat/ovirt-engine-ui-extensions
pkg:rpm/redhat/ovirt-web-ui
pkg:rpm/redhat/polkit
pkg:rpm/redhat/rh-dotnet31-dotnet
pkg:rpm/redhat/rh-dotnet50-dotnet
pkg:rpm/redhat/subscription-manager
pkg:rpm/redhat/tfm-rubygem-katello
pkg:rpm/redhat/thunderbird
pkg:srpm/redhat/dotnet3.1
pkg:srpm/redhat/mozjs60
```

Let's say you wanted to know which product streams the openshift-enterprise-console-container was shipped to. We could do component search using that name. Just using the name alone however returns nearly 500 results currently:

```bash
$ curl -s https://${CORGI_HOST}/api/v1/components?name=openshift-enterprise-console-container | jq '.count'
467
```

Let's narrow down by specifying the arch to be 'noarch'. No arch containers represent an image index. It's sha256 digest can be used to pull the image on a container image registry client of any arch. In our data model noarch containers are parents of arch specific containers, or to use the taxonomy from the schema noarch containers provide arch specific containers.

```bash
curl -s "https://${CORGI_HOST}/api/v1/components?name=openshift-enterprise-console-container&arch=noarch&limit=500" | jq '.results[] | .purl'
```

If we wanted to know which product streams this container was shipped to, we could filter and sort the results by product_streams field eg:

```bash
curl -s "https://${CORGI_HOST}/api/v1/components?name=openshift-enterprise-console-container&arch=noarch&limit=500" | jq '.results[] | .product_streams[] | .ofuri' | sort | uniq
"o:redhat:openshift:4.10.z"
"o:redhat:openshift:4.11.z"
"o:redhat:openshift:4.4.z"
"o:redhat:openshift:4.5.z"
"o:redhat:openshift:4.8"
"o:redhat:openshift:4.8.z"
"o:redhat:openshift:4.9"
"o:redhat:openshift:4.9.z"
"o:redhat:openshift-enterprise:3.11.z"
```

Using the current version of the API, we have to repeat the above query for each component in the sources list of the first component query. Also we want to be able to limit the results to only those product streams which are currently receiving security updates. This is probably best automated by a client tool.

#### List the product streams and root-level containers which include an RPM package

Suppose we were interested in which container products shipped the polkit RPM package. Since we don't know the version in this case, we search by name and type. Normally when we search for an RPM package we are interested in SRPMs, but they are not installed in containers, only arch specific RPMs are installed. We could choose any arch to search for, but let's use x86_64 as an example in this case. I made sure all results where included in a single query by increasing the limit to 50. Also let's process the results so that we only see a single container results, not all versions.

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?type=RPM&name=polkit&&arch=x86_64&limit=50" | jq '.results[] | .sources[] | .purl' | grep 'pkg:container' | awk -F@ '{print $1}' | cut -c2- | sort | uniq
pkg:container/redhat/assisted-installer-agent-container
pkg:container/redhat/cephcsi-container
pkg:container/redhat/cluster-node-tuning-operator-container
pkg:container/redhat/flatpak-build-base-container
pkg:container/redhat/ironic-rhcos-downloader-container
pkg:container/redhat/kubevirt-tekton-tasks-disk-virt-customize-container
pkg:container/redhat/kubevirt-tekton-tasks-disk-virt-customize-rhel9-container
pkg:container/redhat/kubevirt-tekton-tasks-disk-virt-sysprep-container
pkg:container/redhat/kubevirt-tekton-tasks-disk-virt-sysprep-rhel9-container
pkg:container/redhat/kubevirt-v2v-conversion-container
pkg:container/redhat/libguestfs-tools-container
pkg:container/redhat/libguestfs-tools-rhel9-container
pkg:container/redhat/mtv-virt-v2v-container
pkg:container/redhat/multicluster-engine-assisted-installer-agent-container
pkg:container/redhat/openstack-nova-compute-container
pkg:container/redhat/openstack-nova-compute-ironic-container
pkg:container/redhat/openstack-nova-libvirt-container
pkg:container/redhat/ose-agent-installer-node-agent-container
pkg:container/redhat/rhacm-assisted-installer-agent-container
pkg:container/redhat/rook-ceph-operator-container
pkg:container/redhat/sssd-container
pkg:container/redhat/tuned-container
pkg:container/redhat/ubi8-init-container
pkg:container/redhat/virt-launcher-container
pkg:container/redhat/virt-launcher-rhel9-container
pkg:container/redhat/vm-import-virtv2v-container
```

If we wanted to know which product streams these containers ship to, we can look at the product_streams field for each of these containers one by one, for example:

```bash
curl -s "https://${CORGI_HOST}/api/v1/components?name=rhacm-assisted-installer-agent-container&arch=noarch" | jq '.results[] | .product_streams[] | .ofuri' | sort | uniq
"o:redhat:rhacm:2.3.z"
"o:redhat:rhacm:2.4.z"
```

The last request would have to be repeated for each container image, which is something best handled by a CLI client.

#### Search by upstream path

Upstream path could mean a few things, for example it could include golang modules or packages with the upstream path in the name. Alternatively it could mean the upstream path from which we obtain the source code for some build.

Regardless everything in Component Registry is a component, so we can utilize regular expressions to search for components with a substring in the name, eg:

```bash
curl -s "https://${CORGI_HOST}/api/v1/components?re_name=github.com/ulikunitz/xz" | jq '.results[] | .purl'
"pkg:golang/github.com/ulikunitz/xz@v0.5.9"
"pkg:golang/github.com/ulikunitz/xz@0.5.10"
"pkg:golang/github.com/ulikunitz/xz@v0.5.8"
"pkg:golang/github.com/ulikunitz/xz@v0.5.5"
"pkg:golang/github.com/ulikunitz/xz@v0.5.7"
"pkg:golang/github.com/ulikunitz/xz@v0.5.10"
"pkg:golang/github.com/ulikunitz/xz@v0.5.6"
"pkg:golang/github.com/ulikunitz/xz@v0.5.4"
"pkg:golang/github.com/ulikunitz/xz/internal/hash@v0.5.8"
"pkg:golang/github.com/ulikunitz/xz/internal/hash@v0.5.5"
```

If you want to exclude wildcard matches use a `name` query instead:

```bash
curl -s "https://${CORGI_HOST}/api/v1/components?name=github.com/ulikunitz/xz" | jq '.results[] | .purl'
"pkg:golang/github.com/ulikunitz/xz@0.5.10"
"pkg:golang/github.com/ulikunitz/xz@v0.5.10"
"pkg:golang/github.com/ulikunitz/xz@v0.5.4"
"pkg:golang/github.com/ulikunitz/xz@v0.5.5"
"pkg:golang/github.com/ulikunitz/xz@v0.5.6"
"pkg:golang/github.com/ulikunitz/xz@v0.5.7"
"pkg:golang/github.com/ulikunitz/xz@v0.5.8"
"pkg:golang/github.com/ulikunitz/xz@v0.5.9"
```

Another example query, which returns both `golang` and `generic` results:

```bash
curl -L -s "https://${CORGI_HOST}/api/v1/components?re_name=github.com/3scale/apicast&limit=50" | jq '.results[] | .purl' | awk -F@ '{print $1}' | cut -c2- | sort | uniq
pkg:generic/github.com/3scale/apicast
pkg:generic/github.com/3scale/apicast-operator
pkg:golang/github.com/3scale/apicast-operator
pkg:golang/github.com/3scale/apicast-operator/apis/apps
pkg:golang/github.com/3scale/apicast-operator/apis/apps/v1alpha1
pkg:golang/github.com/3scale/apicast-operator/controllers/apps
pkg:golang/github.com/3scale/apicast-operator/pkg/apicast
pkg:golang/github.com/3scale/apicast-operator/pkg/apis/apps
pkg:golang/github.com/3scale/apicast-operator/pkg/apis/apps/v1alpha1
pkg:golang/github.com/3scale/apicast-operator/pkg/helper
pkg:golang/github.com/3scale/apicast-operator/pkg/k8sutils
pkg:golang/github.com/3scale/apicast-operator/pkg/reconcilers
pkg:golang/github.com/3scale/apicast-operator/version
```

Notice the `generic` namespace is used to denote an upstream source in Component Registry. 

#### Find components by type

You can use the `type` url parameters on the `components` endpoint to limit results to a single type. For example if we want to only include upstream types in the previous query, we use a query such as:

```bash
curl -L -s "https://${CORGI_HOST}/api/v1/components?type=UPSTREAM&re_name=github.com/3scale/apicast&limit=50" | jq '.results[] | .purl' | awk -F@ '{print $1}' | cut -c2- | sort | uniq
pkg:generic/github.com/3scale/apicast
pkg:generic/github.com/3scale/apicast-operator
```

The types available to filter results on can be found in the openapi schema. These types are subject to change in future versions.

```bash
curl -s https://${CORGI_HOST}/api/v1/schema?format=json | jq '.paths[] | .get | select(.operationId == "v1_components_list") | .parameters[] | select(.name == "type")'
{
  "in": "query",
  "name": "type",
  "schema": {
    "type": "string",
    "enum": [
      "CONTAINER_IMAGE",
      "GOLANG",
      "MAVEN",
      "NPM",
      "PYPI",
      "RHEL_MODULE",
      "RPM",
      "SRPM",
      "UNKNOWN",
      "UPSTREAM"
    ]
  }
}
```

#### List the dependencies of a specific component

The dependencies (dependent components) of a component are listed in the `provides` field of that component. For example:

```bash
curl -s "https://${CORGI_HOST}/api/v1/components?nvr=bare-metal-event-relay-operator-container-v4.11.1-56&arch=noarch" | jq '.results[] | .provides[] | .purl' | sort
"pkg:container/redhat/bare-metal-event-relay-operator-container@v4.11.1-56?arch=x86_64&digest=sha256:5350a1cc503912f67ef02f13bfbd0dac159d96d52fea96590f2e2fef0cb5c01d"
"pkg:rpm/redhat/audit-libs@3.0.7-2.el8.2?arch=x86_64"
"pkg:rpm/redhat/basesystem@11-5.el8?arch=noarch"
"pkg:rpm/redhat/bash@4.4.20-4.el8_6?arch=x86_64"
...
```

#### List the components in a product stream

Let's start listing all the active product streams in Component Registry. By default inactive product streams (those not listed as active in product_definitions) are excluded when listing all product_streams using the following query.

```bash
$ curl -s https://${CORGI_HOST}/api/v1/product_streams | jq '.count'
311
```

If we wanted to include inactive streams as well, we'd do it like this:

```bash
$ curl -s https://${CORGI_HOST}/api/v1/product_streams?active=all | jq '.count'
1162
```

Another useful property of product_streams we can filter on (client side) is the build_count. We can use a query such as this to limit the results to only the active product streams for which we have builds recorded.

```bash
curl -s https://${CORGI_HOST}/api/v1/product_streams?limit=311 | jq '.results[] | select(.build_count > 0) | .ofuri, .build_count'
"o:redhat:3amp:2"
"o:redhat:amq:7"
"o:redhat:amq-cl:2"
"o:redhat:amq-ic:1"
"o:redhat:amq-on:1"
"o:redhat:amq-st:1"
"o:redhat:ansible_automation_platform:1.2"
...
```

This time including the build_count and sorting by it:

```bash
$ curl -s https://${CORGI_HOST}/api/v1/product_streams?limit=311 | jq -r '.results[] | select(.build_count > 0) | [.ofuri, .build_count] | @tsv' | sort -t$'\t' -k2 -nr
o:redhat:rhel:7.9.z	12984
o:redhat:rhel:7.7.z	11151
o:redhat:rhel:7.6.z	10594
o:redhat:rhel:8.6.0.z	9693
o:redhat:openstack-13-els:	9623
o:redhat:rhel-6-els:	7699
o:redhat:rhel:8.4.0.z	7536
o:redhat:openstack:16.1	7486
o:redhat:openshift:4.6.z	7387
...
```

Let's focus in on the `o:redhat:rhel-br:8.6.0.z` product stream, as it doesn't have too many components, and includes some rhel modules. We can inspect the `components` field of the product stream to get a link to the components filter for the root-level components in that stream:

```bash
curl -s -L https://${CORGI_HOST}/api/v1/product_streams?ofuri=o:redhat:rhel-br:8.6.0.z | jq '.components'
"https://${CORGI_HOST}/api/v1/components?ofuri=o:redhat:rhel-br:8.6.0.z&view=summary"
```

Let's first make sure we're including all results:

```bash
curl -s "https://${CORGI_HOST}/api/v1/components?ofuri=o:redhat:rhel-br:8.6.0.z&view=summary" | jq '.count'
1612
```

The reason this figure is less than the builds count (3085) is because the `ofuri` filter only includes the latest builds, whereas the `build_count` property above includes all builds in the stream.

Since there are so many root-level components in the `o:redhat:rhel-br:8.6.0.z` product stream let's limit the results to only rhel modules:

```bash
curl -s "https://${CORGI_HOST}/api/v1/components?ofuri=o:redhat:rhel-br:8.6.0.z&type=RHEL_MODULE" | jq ".results[] | .purl'
"pkg:rpmmod/redhat/mariadb-devel@10.3-8010020190902091509.cdc1202b?context=cdc1202b&stream=10.3&version=8010020190902091509"
"pkg:rpmmod/redhat/python38-devel@3.8-8020020200309184510.bbc63041?context=bbc63041&stream=3.8&version=8020020200309184510"
"pkg:rpmmod/redhat/virt-devel@rhel-820190226174025.9edba152?context=9edba152&stream=rhel&version=820190226174025"
```

To inspect the RPMs in those modules, simply inspect the `provides` property of each of them. In order to translate the purl into a url, it's best to use the `link` property instead of `purl` as used above, eg:

```bash
curl -s "https://${CORGI_HOST}/api/v1/components?ofuri=o:redhat:rhel-br:8.6.0.z&type=RHEL_MODULE?limit=1" | jq '.results[] | .link'
"https://${CORGI_HOST}/api/v1/components?purl=pkg%3Arpmmod/redhat/mariadb-devel%4010.3-8010020190902091509.cdc1202b%3Fcontext%3Dcdc1202b%26stream%3D10.3%26version%3D8010020190902091509"

curl -s -L https://${CORGI_HOST}/api/v1/components?purl=pkg%3Arpmmod/redhat/mariadb-devel%4010.3-8010020190902091509.cdc1202b%3Fcontext%3Dcdc1202b%26stream%3D10.3%26version%3D8010020190902091509 | jq '.provides[] | .purl'
"pkg:rpm/redhat/Judy-devel@1.0.5-18.module%2Bel8%2B2765%2Bcfa4f87b?arch=s390x"
"pkg:rpm/redhat/Judy-devel@1.0.5-18.module%2Bel8%2B2765%2Bcfa4f87b?arch=aarch64"
"pkg:rpm/redhat/mariadb-embedded-debuginfo@10.3.17-1.module%2Bel8.1.0%2B3974%2B90eded84?arch=i686"
...
```





