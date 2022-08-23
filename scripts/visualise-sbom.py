# -----------------
# visualise sbom script
# -----------------
# (inspired by https://github.com/mwhitecoverity/sbom-tools)
#
# simple viz of sbom
#
# > python3 scripts/visualise-sbom.py {SPDX-json-filename}
#
# Note- require installation of pyvis:

import argparse

import json
import sys
import logging

from pyvis.network import Network


# setup pyvis network opts
opts_string = json.dumps(
    {
        "nodes": {"font": {"size": 10}},
        "edges": {"color": {"inherit": True}, "smooth": False},
        "layout": {"hierarchical": {"enabled": True, "nodeSpacing": 460}},
        "physics": {
            "hierarchicalRepulsion": {"centralGravity": 0},
            "minVelocity": 0.75,
            "solver": "hierarchicalRepulsion",
        },
    }
)

# setup logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

# get args
parser = argparse.ArgumentParser()
parser.add_argument("sbom_json_filename")
args = parser.parse_args()

infile = args.sbom_json_filename
outfile = "{}-sbom-visualisation.html".format(infile)

logger.info("process: {}".format(infile))

print(opts_string)

nt = Network("1500px", "1500px", directed=True)

if infile.endswith(".gz"):
    print("skipping gz file")
    sys.exit(0)

with open(sys.argv[1]) as fl:
    data = json.load(fl)

    # central node should be the top level 'package'

    nt.add_node(label=data["name"], shape="square", n_id="ROOT")

    mainPackage = data["documentDescribes"]
    fileRelationships = []

    if "packages" not in data:
        print("- file does not have packages")
        sys.exit(1)

    if "relationships" not in data:
        print("- file does not have relationships")
        sys.exit(1)

    if "files" not in data:
        print("- file does not have files (not fatal)")
    else:
        for f in data["files"]:
            lbl = "{} (file)".format(f["fileName"])

            if "checksums" in f:
                for c in f["checksums"]:
                    if c["algorithm"] == "SHA1":
                        lbl = "{}\nSHA1:{}".format(lbl, c["checksumValue"])

            nt.add_node(f["SPDXID"], label=lbl, size=5)

    for p in data["packages"]:
        lbl = "{} (package)".format(p["name"])

        if "checksums" in p:
            for c in p["checksums"]:
                if c["algorithm"] == "SHA1":
                    lbl = "{} SHA1:{}".format(lbl, c["checksumValue"])

        nt.add_node(p["SPDXID"], label=lbl, size=10)
        if "hasFiles" in p:
            for ff in p["hasFiles"]:
                fileRelationships.append([p["SPDXID"], ff])

    for r in data["relationships"]:
        if r["relationshipType"] == "CONTAINS" or r["relationshipType"] == "DEPENDS_ON":
            rel = "D" if r["relationshipType"] == "DEPENDS_ON" else "C"
        else:
            rel = r["relationshipType"]

        try:
            nt.add_edge(r["spdxElementId"], r["relatedSpdxElement"], label=rel)
        except Exception as e:
            # do nothing print('-error adding node: {}'.format(e))
            pass

    for rel in fileRelationships:
        nt.add_edge(rel[0], rel[1], label="f")

    print(nt.get_nodes())

    for x in mainPackage:
        nt.add_edge("ROOT", x)

nt.show_buttons()
nt.toggle_physics(True)

nt.barnes_hut(overlap=1, spring_length=400, gravity=-100000)
nt.toggle_stabilization(True)

# nt.set_options(opts_string)

print('Output: "{}"'.format(outfile))
nt.show(outfile)
