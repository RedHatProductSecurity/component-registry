#!/usr/bin/env python3

from corgi.core.models import (
    ComponentNode,
)

# preamble
print("load 'age';")
print('SET search_path = ag_catalog, "$user", public;')

# create all component nodes
for cn in ComponentNode.objects.get_queryset().all():
    c = cn.obj
    print(
        f"""
        SELECT * from cypher('components', $$
            CREATE (node:Component {{parent_id:'{cn.parent_id}', id:'{cn.id}', name:'{c.name}', purl:'{c.purl}', type:'{c.type}', namespace:'{c.namespace}', version:'{c.version}', nevra:'{c.nevra}', arch:'{c.arch}'}}) 
            RETURN NULL
        $$) as (node agtype);
        """,
    )

# create all edges between component nodes
for cn in ComponentNode.objects.get_queryset().all():
    if cn.parent:
        print(
            f"""
            SELECT * from cypher('components', $$
                MATCH (node1:Component {{ id:'{cn.parent_id}' }}), (node2:Component{{id:'{cn.id}' }})
                CREATE (node1)-[e:{cn.type}]->(node2)
                RETURN NULL
            $$) as (e agtype);
        """,
        )
