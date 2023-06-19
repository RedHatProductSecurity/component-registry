#!/usr/bin/env bash
set -e

./manage.py shell < scripts/graph/component/dump_component_nodes_as_cypher.py > component_node_dump.cypher
#./manage.py shell < scripts/dump_component_node_edges_as_csv.py > component_node_edges_dump.csv
