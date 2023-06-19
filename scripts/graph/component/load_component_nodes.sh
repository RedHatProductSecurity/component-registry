#!/usr/bin/env bash
set -e

psql -h localhost -p 5455 --user corgi-db-user -d corgi-graph-db < component_node_dump.cypher
#psql -h localhost -p 5455 --user corgi-db-user -d corgi-graph-db -c "load 'age';SET search_path = ag_catalog, '$user', public;SELECT create_vlabel('components','Component');SELECT load_labels_from_file('components','Component','/home/jfuller/src/component-registry/component_node_dump.csv');"