#!/usr/bin/env bash
set -e

./manage.py shell < scripts/graph/product/dump_product_nodes_as_cypher.py > product_node_dump.cypher

