import re

import requests
from django.conf import settings


class ManifestHintsParser(object):
    """
    class that attempts to extract components metadata based on manifest_hints.txt.
    Returns a list of dict with below items:
    [
        # 1.1 embedded:enterprise_linux:6/atk-2.22.0-3 (in firefox)
        {
          "embedded": "embedded",
          "product": "enterprise_linux:6",
          "component": "atk-2.22.0-3",
          "annotation": "in firefox",
          "in": "firefox",
          "original": "embedded:enterprise_linux:6/atk-2.22.0-3 (in firefox)"
        },
        # 1.2 embedded:ceph_storage:2::el7/parted-3.1-26.el7/gnulib
        {
          "embedded": "embedded",
          "product": "ceph_storage:2::el7",
          "parent": "parted-3.1-26.el7",
          "component": "gnulib",
          "original": "embedded:ceph_storage:2::el7/parted-3.1-26.el7/gnulib"
        },
        # 2.1 libmagic = file in manifest (in the file-libs binary rpm)
        {
          "component": "libmagic",
          "annotation": "file in manifest (in the file-libs binary rpm)",
          "via": "file",
          "in": "manifest (in the file-libs binary rpm)",
          "original": "libmagic = file in manifest (in the file-libs binary rpm)"
        },
        # 2.1 ajv-5.5.2 = directory_server_11:redhat-ds:11/389-ds-base (via cockpit-389-ds)
        {
          "component": "ajv-5.5.2",
          "product": "directory_server_11",
          "parent": "redhat-ds:11",
          "annotation": "via cockpit-389-ds",
          "via": "cockpit-389-ds",
          "original":
            "ajv-5.5.2 = directory_server_11:redhat-ds:11/389-ds-base" (via cockpit-389-ds)"
        },
        # 3.1 openshift_container_storage:4.7::el8/noobaa-operator-container/cloud.google.com/go:?
        {
          "product": "openshift_container_storage:4.7::el8",
          "parent": "noobaa-operator-container/cloud.google.com",
          "component": "go:?",
          "original":
            "openshift_container_storage:4.7::el8/noobaa-operator-container/cloud.google.com/go:?"
        },
        # 3.2 servicemesh:0.9/istio/envoy
        {
          "product": "servicemesh:0.9",
          "parent": "istio",
          "component": "envoy",
          "original": "servicemesh:0.9/istio/envoy"
        },
        # 4 tor (stands for tor network not for tor browser in fedora/tor epel/tor)
        {
          "component": "tor",
          "annotation": "stands for tor network not for tor browser in fedora/tor epel/tor",
          "via": "stands for tor network not for tor browser",
          "in": "fedora/tor epel/tor",
          "original": "tor (stands for tor network not for tor browser in fedora/tor epel/tor)"
        },
    ]
    """

    lines_started_with_embedded_patterns = [
        # 'embedded:enterprise_linux:6/expat-2.0.0 (in firefox)'
        # with parenthese
        re.compile(
            r"""
            (?P<embedded>embedded):     # lines started with embedded:
            (?P<product>.+?)\/          # any string till a literal '/' found(non-greedy).
            (?P<component>[^\s]+)       # any string till a whitespace found.
            .+(?=\()\(                  # any string till an opening paren found, also consumes it.
            (?P<annotation>[^)]+)       # any string till a closing paren found
            """,
            re.VERBOSE,
        ),
        # embedded:storage:3.3::el7/heketi-9.0.0-7.el7rhgs/github.com-ghodss-yaml
        # without parenthese
        re.compile(
            r"""
            (?P<embedded>embedded):     # lines started with 'embedded:'
            (?P<product>.+?)\/          # any string till a literal '/' found(non-greedy).
            (?P<parent>.+)\/            # matches any string till a forward slash(/) found
            (?P<component>.+)           # matches any string
            """,
            re.VERBOSE,
        ),
    ]
    lines_with_equal_sign_patterns = [
        # cat manifest-hints.txt | grep -v embedded | grep -v "^#" | grep "=" | grep "("
        # with parenthese
        re.compile(
            r"""
            (?P<component>[^\s?]+)      # matches string till whitespace is seen(non-greedy)
            [\s]+=[\s]+                 # matches '=' with whitespace(s) before or after the '='
            (?P<product>.+?):           # matches string till the first colon is seen(non-greedy)
            (?P<parent>.+)\/            # matches any string(greedy) till '/' is seen
            .+?[\s]+
            \((?P<annotation>.+?)\)     # matches string inside the parenthese
            """,
            re.VERBOSE,
        ),
        # cat manifest-hints.txt | grep -v embedded | grep "=" | grep -v "("
        # without parenthese
        re.compile(
            r"""
            (?P<component>.+?)          # matches string till whitespace is seen(non-greedy)
            [\s]+=[\s]+                 # matches '=' with whitespace(s) before or after the '='
            (?P<annotation>.+)          # matches any string
            """,
            re.VERBOSE,
        ),
    ]
    lines_with_colon_patterns = [
        # cat manifest-hints.txt | grep -v "^embedded" | grep -v "^#" | grep -v "=" | grep ":"
        re.compile(
            r"""
            (?P<product>.+?)\/          # matches any string(non-greedy) till first '/' is seen
            (?P<parent>.*)\/            # matches any string(greedy) till last '/' is seen
            (?P<component>.+)           # matches any string
            """,
            re.VERBOSE,
        )
    ]
    lines_with_parenthese_patterns = [
        # cat manifest-hints.txt | grep -v "^embedded" | grep -v "^#" | grep -v "=" | grep -v ":"
        re.compile(
            r"""
            (?P<component>.+?)[\s]+       # matches any string(non greedy) till whitespace is met
            \((?P<annotation>.+)\)        # matches string inside the parenthese
            """,
            re.VERBOSE,
        )
    ]
    annotation_patterns = [
        # e.g., 'in nodejs:12/nodejs via npm' or 'in ruby 2.0+, via onigmo fork'
        re.compile(
            r"""
            ^in[\s]+                    # matches lines start with 'in' following whitespace
            (?P<in>[^,]+),?             # matches string after a 'in' till a commma(,) found
            [\s]+via[\s]+               # matches 'via' with preceding/trailing whitespaces.
            (?P<via>.+)                 # matches any string
            """,
            re.VERBOSE,
        ),
        # e.g., jbossweb in manifest-jar.txt
        re.compile(
            r"""
            (?P<via>.+?)[\s]+           # matches string(non-greedy) till whitespace found
            in[\s]+                     # matches 'in' following whitespaces
            (?P<in>.+)                 # matches any string
            """,
            re.VERBOSE,
        ),
        # e.g., via cockpit-389-ds
        re.compile(
            r"""
            ^via[\s]+                   # matches lines start with 'via' following whitespace
            (?P<via>.+)                # matches any string
            """,
            re.VERBOSE,
        ),
        # e.g., in ruby
        re.compile(
            r"""
            ^in[\s]+                    # matches lines start with 'in' following whitespace
            (?P<in>.+)                  # matches any string
            """,
            re.VERBOSE,
        ),
        # e.g., certificate_system:10/pki-extras
        re.compile(
            r"""
            (?P<product>.+)\/           # matches any string till a forward slash(/) found
            (?P<in>.+)                  # matches any string
            """,
            re.VERBOSE,
        ),
    ]

    def __init__(self, url=settings.MANIFEST_HINTS_URL):
        self.hint_url = url

    def load_manifest_hints(self) -> list:
        response = requests.get(self.hint_url)
        response.raise_for_status()
        return list(filter(None, response.text.split("\n")))

    def extract_from_annotation(self, annotation) -> dict:
        """
        An attempt to extract data from annotations with below form:
        - in nodejs:12/nodejs via npm
        - jbossweb in manifest-jar.txt
        - via cockpit-389-ds
        - in ruby 2.0+, via onigmo fork
        - in ruby
        - certificate_system:10/pki-extras
        """
        data = {}
        for pattern in self.annotation_patterns:
            match = pattern.match(annotation)
            if match:
                data = match.groupdict()
                break
        return data

    def parse(self) -> list:
        components = []
        manifest_hints = self.load_manifest_hints()
        for line in manifest_hints:
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("#"):
                # FIXME: some of the inline comments might be useful hints for subsequent lines.
                continue
            if line.startswith("embedded"):
                for pattern in self.lines_started_with_embedded_patterns:
                    match = pattern.match(line)
                    if match:
                        d = match.groupdict()
                        if "annotation" in d:
                            annotation_data = self.extract_from_annotation(d.get("annotation"))
                            d.update(annotation_data)
                        d["original"] = line
                        components.append(d)
                        break
            elif "=" in line:
                for pattern in self.lines_with_equal_sign_patterns:
                    match = pattern.match(line)
                    if match:
                        d = match.groupdict()
                        annotation_data = self.extract_from_annotation(d.get("annotation"))
                        d.update(annotation_data)
                        d["original"] = line
                        components.append(d)
                        break
            elif ":" in line:
                for pattern in self.lines_with_colon_patterns:
                    match = pattern.match(line)
                    if match:
                        d = match.groupdict()
                        d["original"] = line
                        components.append(d)
                        break
            elif "(" in line:
                # these 4 lines remains by now:
                # tor (stands for tor network not for tor browser in fedora/tor epel/tor)
                # acpi-support (NOT SHIPPED, Debian specific)
                # sessionclean (php5 script, NOT SHIPPED, Debian specific)
                # -------------------------
                for pattern in self.lines_with_parenthese_patterns:
                    match = pattern.match(line)
                    if match:
                        d = match.groupdict()
                        annotation_data = self.extract_from_annotation(d.get("annotation"))
                        d.update(annotation_data)
                        d["original"] = line
                        components.append(d)
                        break
            else:
                # If none of the above matches, append the line without processing
                components.append({"unprocessed": True, "original": line})

        return components
