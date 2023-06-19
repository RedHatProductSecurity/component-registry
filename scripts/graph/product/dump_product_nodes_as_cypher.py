#!/usr/bin/env python3

from corgi.core.models import (
    Component,
    ComponentNode,
    ProductNode,
)

# preamble
print("load 'age';")
print('SET search_path = ag_catalog, "$user", public;')

# create all product nodes
for pn in ProductNode.objects.get_queryset().all():
    product_type = pn.obj._meta.object_name
    print(
        f"""
            SELECT * from cypher('products', $$
                MERGE (node:{product_type} {{parent_id:'{pn.parent_id}', id:'{pn.id}', name:'{pn.obj.name}', ofuri:'{pn.obj.ofuri}' }})
                RETURN NULL
            $$) as (node agtype);
        """,
    )
# create edges between product nodes
for pn in ProductNode.objects.get_queryset().all():
    if pn.parent:
        print(
            f"""
            SELECT * from cypher('products', $$
                MATCH (node1 {{id:'{pn.parent_id}' }}), (node2 {{id:'{pn.id}' }})
                MERGE (node1)-[e:CHILD]->(node2)
                RETURN NULL
            $$) as (e agtype);
        """,
        )
