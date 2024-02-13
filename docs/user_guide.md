# Component Registry User Guide

## Interacting with the REST API

Component Registry exposes a REST API that any number of clients can connect to, from cURL to a custom-made
front-end application to serve as a web client.

For more in-depth information about the resources served by the API, see section
[REST API Resource Definitions](#rest-api-resource-definitions).

The [OpenAPI specification](https:///github.com/RedHatProductSecurity/component-registry/blob/main/openapi.yml) provides
developer level documentation for endpoint usage.

### Fetching data

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
    "license_concluded": "MIT and ASL 2.0 and ISC and BSD",
    "license_concluded_list": [
        "MIT",
        "ASL 2.0",
        "ISC",
        "BSD"
    ],
    "license_declared": "MIT and ASL 2.0 and ISC and BSD",
    "license_declared_list": [
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

- `license_concluded`: An SPDX license expression as determined by an OpenLCS scan.

- `license_concluded_list`: the same license string parsed into its individual components.

- `license_declared`: the license string as it is included in the component's spec file, docker image label, etc.

- `license_declared_list`: the same license string parsed into its individual components.

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
$ curl -L -s https://${CORGI_HOST}/api/v1/components?purl=pkg:npm/is-svg@2.1.0 | jq '.sources[] | .purl' | grep -v "\-container\-source" | awk -F@ '{print $1}' | cut -c2- | sort | uniq
pkg:oci/console-ui-rhel8
pkg:oci/grafana
pkg:oci/machineexec-rhel8
pkg:oci/ose-console
pkg:oci/quay-rhel8
pkg:oci/theia-rhel8
pkg:rpm/redhat/cfme-gemset
pkg:rpm/redhat/cockpit-ceph-installer
pkg:rpm/redhat/cockpit-ovirt
pkg:rpm/redhat/dotnet3.1
pkg:rpm/redhat/firefox
pkg:rpm/redhat/foreman
pkg:rpm/redhat/grafana
pkg:rpm/redhat/kibana
pkg:rpm/redhat/ovirt-web-ui
pkg:rpm/redhat/subscription-manager
pkg:rpm/redhat/tfm-rubygem-katello
pkg:rpm/redhat/thunderbird
```

Let's say you wanted to know which product streams the grafana rpm was shipped to. We could do component search using that name. Just using the name alone however returns 162 results currently:

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?name=grafana&type=RPM" | jq '.count'
153
```

If we wanted to know which product streams this RPM was shipped to, we could filter and sort the results by product_streams field eg:

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?name=grafana&type=RPM" | jq '.results[] | .product_streams[] | .ofuri' | sort | uniq
"o:redhat:ceph-2-default:"
"o:redhat:ceph:3"
"o:redhat:rhel:8.1.0.z"
"o:redhat:rhel:8.2.0.z"
"o:redhat:rhel:8.4.0.z"
"o:redhat:rhel:8.6.0"
"o:redhat:rhel:8.6.0.z"
"o:redhat:rhel:8.7.0"
"o:redhat:rhel:8.7.0.z"
"o:redhat:rhel:8.8.0"
"o:redhat:rhel:9.0"
"o:redhat:rhel:9.0.0.z"
"o:redhat:rhel:9.1.0"
"o:redhat:rhel:9.1.0.z"
"o:redhat:rhes:3.5"
```

Using the current version of the API, we have to repeat the above query for each component in the sources list of the first component query. Also we want to be able to limit the results to only those product streams which are currently receiving security updates. This is probably best automated by a client tool.

#### List the product streams and root-level containers which include an RPM package

Suppose we were interested in which container products shipped the polkit RPM package. Since we don't know the version in this case, we search by name and type. Normally when we search for an RPM package we are interested in SRPMs, but they are not installed in containers, only arch specific RPMs are installed. We could choose any arch to search for, but let's use x86_64 as an example in this case. I made sure all results where included in a single query by increasing the limit to 50. Also let's process the results so that we only see a single container results, not all versions.

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?type=RPM&name=polkit&arch=x86_64&limit=50" | jq '.results[] | .sources[] | .purl' | grep 'pkg:oci' | awk -F@ '{print $1}' | cut -c2- | sort | uniq
pkg:oci/assisted-installer-agent-rhel8
pkg:oci/cephcsi-container
pkg:oci/kubevirt-tekton-tasks-disk-virt-customize
pkg:oci/kubevirt-tekton-tasks-disk-virt-customize-rhel9
pkg:oci/kubevirt-tekton-tasks-disk-virt-sysprep
pkg:oci/kubevirt-tekton-tasks-disk-virt-sysprep-rhel9
pkg:oci/kubevirt-v2v-conversion
pkg:oci/libguestfs-tools
pkg:oci/libguestfs-tools-rhel9
pkg:oci/metrics-hawkular-metrics
pkg:oci/mtv-virt-v2v-rhel8
pkg:oci/openstack-nova-compute
pkg:oci/openstack-nova-compute-ironic
pkg:oci/openstack-nova-libvirt
pkg:oci/ose-agent-installer-node-agent
pkg:oci/ose-cluster-node-tuned
pkg:oci/ose-cluster-node-tuning-operator
pkg:oci/ose-ironic-machine-os-downloader
pkg:oci/rook-ceph-operator-container
pkg:oci/sssd
pkg:oci/virt-launcher
pkg:oci/virt-launcher-rhel9
pkg:oci/vm-import-virtv2v-rhel8
```

If we wanted to know which product streams these containers ship to, we can look at the product_streams field for each of these containers one by one, for example:

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?re_purl=pkg:oci/assisted-installer-agent-rhel8&arch=noarch&limit=60" | jq '.results[] | .product_streams[] | .ofuri' | sort | uniq
"o:redhat:openshift:4.6.z"
"o:redhat:rhacm:2.3.z"
"o:redhat:rhacm:2.4.z"
"o:redhat:rhai:1"
```

The last request would have to be repeated for each container image, which is something best handled by a CLI client.

#### Search by upstream path

Upstream path could mean a few things, for example it could include golang modules or packages with the upstream path in the name. Alternatively it could mean the upstream path from which we obtain the source code for some build.

Regardless everything in Component Registry is a component, so we can utilize regular expressions to search for components with a substring in the name, eg:

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?re_name=github.com/ulikunitz/xz" | jq '.results[] | .purl'
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
$ curl -s "https://${CORGI_HOST}/api/v1/components?name=github.com/ulikunitz/xz" | jq '.results[] | .purl'
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
$ curl -s "https://${CORGI_HOST}/api/v1/components?re_name=github.com/3scale/apicast&limit=50" | jq '.results[] | .purl' | awk -F@ '{print $1}' | cut -c2- | sort | uniq
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
```

Notice the `generic` namespace is used to denote an upstream source in Component Registry. 

#### Find components by type

You can use the `type` url parameters on the `components` endpoint to limit results to a single type. For example if we want to only include upstream types in the previous query, we use a query such as:

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?type=GENERIC&re_name=github.com/3scale/apicast&limit=50" | jq '.results[] | .purl' | awk -F@ '{print $1}' | cut -c2- | sort | uniq
pkg:generic/github.com/3scale/apicast
pkg:generic/github.com/3scale/apicast-operator
```

The types available to filter results on can be found in the openapi schema. These types are subject to change in future versions.

```bash
$ curl -s https://${CORGI_HOST}/api/v1/schema?format=json | jq '.paths[] | .get | select(.operationId == "v1_components_list") | .parameters[] | select(.name == "type")'
{
  "in": "query",
  "name": "type",
  "schema": {
    "type": "string",
    "enum": [
      "CARGO",
      "GEM",
      "GENERIC",
      "GITHUB",
      "GOLANG",
      "MAVEN",
      "NPM",
      "OCI",
      "PYPI",
      "RPM",
      "RPMMOD"
    ]
  }
}
```

#### List the dependencies of a specific component

The dependencies (dependent components) of a component are listed in the `provides` field of that component. For example:

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?nvr=bare-metal-event-relay-operator-container-v4.11.1-56&arch=noarch" | jq '.results[] | .provides[] | .purl' | sort
"pkg:oci/bare-metal-event-relay@sha256:5350a1cc503912f67ef02f13bfbd0dac159d96d52fea96590f2e2fef0cb5c01d?arch=x86_64&repository_url=registry.redhat.io/openshift4/bare-metal-event-relay&tag=v4.11.1-56"
"pkg:rpm/redhat/audit-libs@3.0.7-2.el8.2?arch=x86_64"
"pkg:rpm/redhat/basesystem@11-5.el8?arch=noarch"
"pkg:rpm/redhat/bash@4.4.20-4.el8_6?arch=x86_64"
...
```

#### List the components in a product stream

Let's start listing all the active product streams in Component Registry. By default inactive product streams (those not listed as active in product_definitions) are excluded when listing all product_streams using the following query.

```bash
$ curl -s https://${CORGI_HOST}/api/v1/product_streams | jq '.count'
286
```

If we wanted to include inactive streams as well, we'd do it like this:

```bash
$ curl -s https://${CORGI_HOST}/api/v1/product_streams?active=all | jq '.count'
1154
```

Another useful property of product_streams we can filter on (client side) is the build_count. We can use a query such as this to limit the results to only the active product streams for which we have builds recorded.

```bash
$ curl -s https://${CORGI_HOST}/api/v1/product_streams?limit=311 | jq '.results[] | select(.build_count > 0) | .ofuri'
"o:redhat:3amp:2"
"o:redhat:amq:7"
"o:redhat:amq-cl:2"
"o:redhat:amq-ic:1"
"o:redhat:amq-on:1"
"o:redhat:amq-st:2"
"o:redhat:ansible_automation_platform:1.2"
...
```

This time including the build_count and sorting by it:

```bash
$ curl -s https://${CORGI_HOST}/api/v1/product_streams?limit=311 | jq -r '.results[] | select(.build_count > 0) | [.ofuri, .build_count] | @tsv' | sort -t$'\t' -k2 -nr
o:redhat:openshift:4.12	13941
o:redhat:rhel:7.9.z	13043
o:redhat:rhel:7.7.z	11155
o:redhat:rhel:8.7.0.z	10397
o:redhat:openstack-13-els:	10075
o:redhat:rhel:8.6.0.z	9155
o:redhat:openstack:16.1	8006
o:redhat:rhel:7.6.z	7765
o:redhat:rhel-6-els:	7707
o:redhat:rhel:8.4.0.z	7616
o:redhat:openshift-enterprise:3.11.z	7597
...
```

Let's focus in on the `o:redhat:openshift:4.11.z` product stream, as it doesn't have too many components, and includes rpms and containers. We can inspect the `components` field of the product stream to get a link to the components filter for the root-level components in that stream:

```bash
$ curl -s -L https://${CORGI_HOST}/api/v1/product_streams?ofuri=o:redhat:openshift:4.11.z | jq '.components'
"https://${CORGI_HOST}/api/v1/components?ofuri=o:redhat:openshift:4.11.z&view=summary"
```

Let's first make sure we're including all results:

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?ofuri=o:redhat:openshift:4.11.z&view=summary" | jq '.count'
604
```

The reason this figure is less than the builds count (3662) is because the `ofuri` filter only includes the latest builds, whereas the `build_count` property above includes all builds in the stream.

The following query demonstrates only including a certain type in the product stream's latest results:

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?ofuri=o:redhat:openshift:4.11.z&view=summary&type=RPM&limit=155" | jq '.results[] | .purl'
"pkg:rpm/redhat/ovn22.06@22.06.0-27.el8fdp?arch=src"
"pkg:rpm/redhat/containernetworking-plugins@1.0.1-5.rhaos4.11.el8?arch=src"
"pkg:rpm/redhat/toolbox@0.1.0-1.rhaos4.11.el8?arch=src"
"pkg:rpm/redhat/openvswitch2.17@2.17.0-62.el8fdp?arch=src"
"pkg:rpm/redhat/criu@3.15-4.rhaos4.11.el8?arch=src"
"pkg:rpm/redhat/runc@1.1.2-1.rhaos4.11.el8?arch=src&epoch=3"
"pkg:rpm/redhat/libslirp@4.4.0-2.rhaos4.11.el8?arch=src"
"pkg:rpm/redhat/fuse-overlayfs@1.9-1.rhaos4.11.el8?arch=src"
"pkg:rpm/redhat/NetworkManager@1.36.0-8.el8_6?arch=src&epoch=1"
"pkg:rpm/redhat/haproxy@2.2.24-1.el8?arch=src"
"pkg:rpm/redhat/podman@4.0.2-6.rhaos4.11.el8?arch=src&epoch=2"
"pkg:rpm/redhat/conmon@2.1.2-2.rhaos4.11.el8?arch=src&epoch=2"
"pkg:rpm/redhat/python-funcsigs@1.0.2-17.el8?arch=src"
"pkg:rpm/redhat/python-fasteners@0.14.1-21.el8?arch=src"
...
```

#### Exploring RHEL Modules

To list the RHEL Modules in a product stream we need to use the `product_stream` property instead of ofuri because ofuri does not include RHEL Modules results.

```bash
$ curl -s "https://${CORGI_HOST}/api/v1/components?product_stream=rhel-br-8.6.0.z&type=RPMMOD&limit=1" | jq '.results[] | .link'
"https://${CORGI_HOST}/api/v1/components?purl=pkg%3Arpmmod/redhat/389-ds%401.4%3A8000020190424152135%3Aab753183"
```

To inspect the RPMs in a module, look at the `provides` property. In order to translate the purl into a url, it's best to use the `link` property instead of `purl` as used above, eg:

```bash
$ curl -s -L https://${CORGI_HOST}/api/v1/components?purl=pkg%3Arpmmod/redhat/389-ds%401.4%3A8000020190424152135%3Aab753183 | jq '.provides[] | .purl'
"pkg:rpm/redhat/389-ds-base@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=aarch64"
"pkg:rpm/redhat/389-ds-base@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=ppc64le"
"pkg:rpm/redhat/389-ds-base@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=s390x"
"pkg:rpm/redhat/389-ds-base@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=x86_64"
"pkg:rpm/redhat/389-ds-base-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=aarch64"
"pkg:rpm/redhat/389-ds-base-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=ppc64le"
"pkg:rpm/redhat/389-ds-base-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=s390x"
"pkg:rpm/redhat/389-ds-base-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=x86_64"
"pkg:rpm/redhat/389-ds-base-debugsource@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=aarch64"
"pkg:rpm/redhat/389-ds-base-debugsource@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=ppc64le"
"pkg:rpm/redhat/389-ds-base-debugsource@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=s390x"
"pkg:rpm/redhat/389-ds-base-debugsource@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=x86_64"
"pkg:rpm/redhat/389-ds-base-devel@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=aarch64"
"pkg:rpm/redhat/389-ds-base-devel@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=ppc64le"
"pkg:rpm/redhat/389-ds-base-devel@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=s390x"
"pkg:rpm/redhat/389-ds-base-devel@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=x86_64"
"pkg:rpm/redhat/389-ds-base-legacy-tools@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=aarch64"
"pkg:rpm/redhat/389-ds-base-legacy-tools@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=ppc64le"
"pkg:rpm/redhat/389-ds-base-legacy-tools@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=s390x"
"pkg:rpm/redhat/389-ds-base-legacy-tools@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=x86_64"
"pkg:rpm/redhat/389-ds-base-legacy-tools-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=aarch64"
"pkg:rpm/redhat/389-ds-base-legacy-tools-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=ppc64le"
"pkg:rpm/redhat/389-ds-base-legacy-tools-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=s390x"
"pkg:rpm/redhat/389-ds-base-legacy-tools-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=x86_64"
"pkg:rpm/redhat/389-ds-base-libs@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=aarch64"
"pkg:rpm/redhat/389-ds-base-libs@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=ppc64le"
"pkg:rpm/redhat/389-ds-base-libs@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=s390x"
"pkg:rpm/redhat/389-ds-base-libs@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=x86_64"
"pkg:rpm/redhat/389-ds-base-libs-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=aarch64"
"pkg:rpm/redhat/389-ds-base-libs-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=ppc64le"
"pkg:rpm/redhat/389-ds-base-libs-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=s390x"
"pkg:rpm/redhat/389-ds-base-libs-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=x86_64"
"pkg:rpm/redhat/389-ds-base-snmp@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=aarch64"
"pkg:rpm/redhat/389-ds-base-snmp@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=ppc64le"
"pkg:rpm/redhat/389-ds-base-snmp@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=s390x"
"pkg:rpm/redhat/389-ds-base-snmp@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=x86_64"
"pkg:rpm/redhat/389-ds-base-snmp-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=aarch64"
"pkg:rpm/redhat/389-ds-base-snmp-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=ppc64le"
"pkg:rpm/redhat/389-ds-base-snmp-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=s390x"
"pkg:rpm/redhat/389-ds-base-snmp-debuginfo@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=x86_64"
"pkg:rpm/redhat/python3-lib389@1.4.0.20-10.module%2Bel8.0.0%2B3096%2B101825d5?arch=noarch"
```
