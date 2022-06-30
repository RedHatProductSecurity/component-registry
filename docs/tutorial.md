# Component Registry Tutorial

[[_TOC_]]


**NOTE**: Some functionality in this document still needs to be implemented.

## Using the Component Registry REST API

Corgi exposes a REST API from which any number of clients can connect, from cURL to a custom-made front-end application
to serve as web client, in this tutorial we will go through the basics of using the API .

The [OpenAPI specification](https:///github.com/RedHatProductSecurity/corgi/-/blob/master/openapi.yml) provides developer
level documentation for endpoint usage.

### Authentication and authorization (NOT YET IMPLEMENTED)

Corgi employs kerberos authentication with LDAP providing authorization.

- Client performs Kerberos / GSSAPI authentication using the SPNEGO protocol
- After initial Kerberos authentication, client receives JSON Web Tokens for subsequent communication

#### Generating auth token

The following endpoint generates a JSON Web Token used to access all other endpoints.

##### cURL
```bash
$ curl -H 'Content-Type: application/json' \
       --negotiate -u : \
       "https://${CORGI_DOMAIN}/auth/token" \

{"refresh": ..., "access": ...}
```

##### python
```python
import requests
from requests_gssapi import HTTPSPNEGOAuth

TOKEN_URL = f"https://{CORGI_DOMAIN}/auth/token"

response = requests.get(TOKEN_URL, auth=HTTPSPNEGOAuth())
response.raise_for_status()

body = response.json()
ACCESS_TOKEN = body['access']
REFRESH_TOKEN = body['refresh']
print(ACCESS_TOKEN)
```

#### Using token for authorization

The following will fail to dereference:

#####  cURL
```bash
$ curl "https://${CORGI_DOMAIN}/api/v1/status"
```
##### python

```python
import requests

response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/status")
```

with a 403 status code error. This is due to attempting to retrieving data from an endpoint without providing
any credentials.

To provide credentials, first retrieve an access token (as described above) and supply via an Authorization
header on HTTP Request.

#####  cURL

```bash
$ curl -H "Authorization: Bearer ${ACCESS_TOKEN}"\
       "https://${CORGI_DOMAIN}/api/v1/status"
```

##### python

```python
import requests

headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/status", headers=headers)
response.raise_for_status()
```

#### Refreshing access token

Corgi uses JSON Web Tokens (JWTs) for authentication, meaning you get a token for access and a refresh
token from which you can generate new access tokens.

It is best practice to generate access tokens for each request.

##### cURL
```bash
$ curl -X POST "https://${CORGI_DOMAIN}/auth/token/refresh" \
       -H 'Content-Type: application/json' \
       -d "\{\"refresh\": \"${REFRESH_TOKEN}\"\}"

{"access": ...}
```

##### python
```python
import requests

REFRESH_URL = f"https://{CORGI_DOMAIN}/auth/token/refresh"
response = requests.get(REFRESH_URL, json={"refresh": REFRESH_TOKEN})
response.raise_for_status()
ACCESS_TOKEN = response.json()["access"]
```

Note - when a refresh token expires, the client must re-authenticate.

#### Verifying tokens

Corgi exposes a token-verification endpoint which will return an HTTP 200 response with empty body if the token is valid,
or if invalid an HTTP 401 response if the token is invalid.

##### cURL
```bash
$ curl -X POST "https://${CORGI_DOMAIN}/auth/token/verify \
       -H 'Content-Type: application/json' \
       -d "\{\"token\": \"${ACCESS_TOKEN}\"\}"
```

##### python
```python
import requests

VERIFY_URL = f"https://{CORGI_DOMAIN}/auth/token/verify"
response = requests.get(VERIFY_URL, json={"token": REFRESH_TOKEN})
response.raise_for_status()
```

### Fetching data

[REST API docs](https:///github.com/RedHatProductSecurity/corgi/-/blob/main/openapi.yml) provide detailed
usage on all endpoints.

#### Retrieving components

Most endpoints provide a paginated data response.

##### cURL
```bash
$ curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
       "https://${CORGI_DOMAIN}/api/v1/components"
```

##### python
```python
import requests

headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components", headers=headers)
response.raise_for_status()
```

The following is an example response from this endpoint.

```json
{
    "count": 104,
    "next": "https://{CORGI_DOMAIN}/api/v1/components?limit=20&name=curl&offset=20",
    "previous": null,
    "results": [
        {
            "uuid": "54050976-adfd-432a-b4b7-c20be68be22e",
            "type": "RPM",
            "purl": "pkg:rpm/redhat/curl@7.61.1-22.el8?arch=x86_64&build=brew:1733792&release=22.el8&version=7.61.1",
            "name": "curl",
            "description": "curl is a command line tool for transferring data with URL syntax, supporting\nFTP, FTPS, HTTP, HTTPS, SCP, SFTP, TFTP, TELNET, DICT, LDAP, LDAPS, FILE, IMAP,\nSMTP, POP3 and RTSP.  curl supports SSL certificates, HTTP POST, HTTP PUT, FTP\nuploading, HTTP form based upload, proxies, cookies, user+password\nauthentication (Basic, Digest, NTLM, Negotiate, kerberos...), file transfer\nresume, proxy tunneling and a busload of other useful tricks.",
            "tags": {},
            "version": "7.61.1",
            "release": "22.el8",
            "arch": "x86_64",
            "nvr": "curl-7.61.1-22.el8",
            "epoch": "0",
            "license": "MIT",
            "software_build": {
                "build_id": 1733792,
                "type": "BREW",
                "name": "curl",
                "tags": {},
                "brew_tags": "['rhel-8.5.0', 'rhel-8.5.0-candidate', 'RHSA-2021:4511-released', 'rhel-8.5.0-RC-1.0-set', 'rhel-8.5.0-candidate-RC-1.0-set', 'rhel-8.5.0-z-batch-0.0-set', 'rhel-8.5.0-z-batch-0.0-set-test', 'kpatch-kernel-4.18.0-348.1.1.el8_5-build', 'kpatch-kernel-4.18.0-348.2.1.el8_5-build', 'rhel-8.5.0-z-batch-1.0-set', 'kpatch-kernel-4.18.0-350.el8-build', 'rhel-8.5.0-z-batch-1.1-set', 'kpatch-kernel-4.18.0-348.7.1.el8_5-build', 'kpatch-kernel-4.18.0-355.el8-build', 'rhel-8.5.0-z-batch-2.0-set', 'kpatch-kernel-4.18.0-348.12.2.el8_5-build', 'kpatch-kernel-4.18.0-359.el8-build', 'rhel-8.5.0-z-batch-2.1-set', 'kpatch-kernel-4.18.0-367.el8-build', 'rhel-8.5.0-z-batch-3.0-set', 'rhel-8.5.0-z-batch-2.2-set', 'kpatch-kernel-4.18.0-348.19.1.el8_5-build', 'rhel-8.5.0-z-batch-3.1-set', 'kpatch-kernel-4.18.0-348.20.1.el8_5-build', 'rhel-8.5.0-z-batch-4.0-set']"
            },
            "errata": [
                "RHSA-2021:4511"
            ],
            "products": [
                "rhel"
            ],
            "product_versions": [
                "rhel-8"
            ],
            "product_streams": [
                "rhel-8.5.0.z"
            ],
            "product_variants": [
                "BaseOS-8.5.0.Z.MAIN"
            ],
            "sources": [
                "pkg:srpm/redhat/curl@7.61.1-22.el8?build=brew:1733792&release=22.el8&version=7.61.1"
            ],
            "provides": [],
            "upstream": [
                "pkg:upstream/curl.haxx.se/curl@7.61.1?version=7.61.1"
            ],
            "meta_attr": {
                "nvr": "curl-7.61.1-22.el8",
                "url": "https://curl.haxx.se/",
                "arch": "x86_64",
                "name": "curl",
                "epoch": "0",
                "source": "[]",
                "license": "MIT",
                "release": "22.el8",
                "summary": "A utility for getting files from remote servers (FTP, HTTP, and others)",
                "version": "7.61.1",
                "namespace": "redhat",
                "description": "curl is a command line tool for transferring data with URL syntax, supporting\nFTP, FTPS, HTTP, HTTPS, SCP, SFTP, TFTP, TELNET, DICT, LDAP, LDAPS, FILE, IMAP,\nSMTP, POP3 and RTSP.  curl supports SSL certificates, HTTP POST, HTTP PUT, FTP\nuploading, HTTP form based upload, proxies, cookies, user+password\nauthentication (Basic, Digest, NTLM, Negotiate, kerberos...), file transfer\nresume, proxy tunneling and a busload of other useful tricks.",
                "capabilities": "[{'type': 'requires', 'id': '10258728-cap-1', 'name': 'libc.so.6()(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-2', 'name': 'libc.so.6(GLIBC_2.14)(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-3', 'name': 'libc.so.6(GLIBC_2.17)(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-4', 'name': 'libc.so.6(GLIBC_2.2.5)(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-5', 'name': 'libc.so.6(GLIBC_2.3)(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-6', 'name': 'libc.so.6(GLIBC_2.3.4)(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-7', 'name': 'libc.so.6(GLIBC_2.4)(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-8', 'name': 'libc.so.6(GLIBC_2.7)(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-9', 'name': 'libcrypto.so.1.1()(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-10', 'name': 'libcurl(x86-64)', 'version': '7.61.1-22.el8', 'flags': 12, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-11', 'name': 'libcurl.so.4()(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-12', 'name': 'libpthread.so.0()(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-13', 'name': 'libpthread.so.0(GLIBC_2.2.5)(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-14', 'name': 'libssl.so.1.1()(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-15', 'name': 'libz.so.1()(64bit)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-16', 'name': 'rpmlib(CompressedFileNames)', 'version': '3.0.4-1', 'flags': 16777226, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-17', 'name': 'rpmlib(FileDigests)', 'version': '4.6.0-1', 'flags': 16777226, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-18', 'name': 'rpmlib(PayloadFilesHavePrefix)', 'version': '4.0-1', 'flags': 16777226, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-19', 'name': 'rpmlib(PayloadIsXz)', 'version': '5.2-1', 'flags': 16777226, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'requires', 'id': '10258728-cap-20', 'name': 'rtld(GNU_HASH)', 'version': '', 'flags': 16384, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'provides', 'id': '10258728-cap-21', 'name': 'curl', 'version': '7.61.1-22.el8', 'flags': 8, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'provides', 'id': '10258728-cap-22', 'name': 'curl(x86-64)', 'version': '7.61.1-22.el8', 'flags': 8, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'provides', 'id': '10258728-cap-23', 'name': 'curl-full', 'version': '7.61.1-22.el8', 'flags': 8, 'analysis_meta': {'source': ['koji.getRPMDeps']}}, {'type': 'provides', 'id': '10258728-cap-24', 'name': 'webclient', 'version': '', 'flags': 0, 'analysis_meta': {'source': ['koji.getRPMDeps']}}]"
            },
            ...elided....
        ]
      }
```

#### Retrieving component detail
Components are addressable by a unique id (uuid).

##### cURL

```bash
$ curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
       "https://${CORGI_DOMAIN}/api/v1/components/2fe16efb-11cb-4cd2-b31b-d769ba821073"
```

##### python

```python
import requests

component_id = "2fe16efb-11cb-4cd2-b31b-d769ba821073"
headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components/{component_id}", headers=headers)
response.raise_for_status()
```

##### cURL

```bash
$ curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
       "https://${CORGI_DOMAIN}/api/v1/components/CVE-2005-0001"
```

##### python

```python
import requests

component_id = "CVE-2005-0001"
headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components/{component_id}", headers=headers)
response.raise_for_status()
```

### Searching for components

#### Filtering by specific field

##### cURL
```bash
$ curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
       "https://${CORGI_DOMAIN}/api/v1/components?name=curl"
```

##### python
```python
import requests

headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
params = {"name": "curl"}
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components", headers=headers, params=params)
response.raise_for_status()
```

Which will return any components with the name 'curl'.

Some URL parameters provide regular expression matching (prefixed by `re_`).

##### cURL
```bash
$ curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
       "https://${CORGI_DOMAIN}/api/v1/components?re_name=^curl$"
```

#### Full text search

You may also perform full text search:

##### cURL

```bash
$ curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
       "https://${CORGI_DOMAIN}/api/v1/components?search=openjdk
```

##### python

```python
import requests

headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
params = {"search": "openjdk"}
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components", headers=headers, params=params)
response.raise_for_status()
```

#### Including/excluding data (NOT YET IMPLEMENTED)

- `include_fields` -- fields to be included in response.

- `exclude_fields` -- fields to be excluded in response.

##### cURL
```bash
$ curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
       "https://${CORGI_DOMAIN}/api/v1/components?include_fields=purl,description"
```

##### python

```python
import requests

headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
params = {"include_fields": ["purl","description"]}
response = requests.get(f"https://{CORGI_DOMAIN}/api/v1/components", headers=headers, params=params)
response.raise_for_status()
```

### Tagging a component

##### cURL
```bash
$ curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
       -H "Content-Type: application/json" \
       -X PUT \
       -d '{"component_review": "https://example.org/doc.txt"}' \
       "https://${CORGI_DOMAIN}/api/v1/components/${uuid}/tags"
```

##### python
```python
import requests

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
}
data = {
    "component_review": "https://example.org/doc.txt",
}
response = requests.put(f"https://{CORGI_DOMAIN}/api/v1/components/{uuid}/tags", headers=headers, json=data)
response.raise_for_status()
```
