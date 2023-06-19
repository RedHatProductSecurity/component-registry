import logging

from django.db import connections

logger = logging.getLogger(__name__)


def create_product_node(product_type, product_name, ofuri, object_id):
    """create product node"""
    conn = connections["graph"]
    conn.ensure_connection()
    cursor = conn.connection.cursor()
    cursor.execute('SET search_path = ag_catalog, "$user", public')
    if product_type == "Product":
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
                MERGE (node:Product {name:%s, ofuri:%s, object_id:%s})
                RETURN node
            $$) as (node agtype);
            """,
            ["products", product_name, ofuri, str(object_id)],
        )
    if product_type == "ProductVersion":
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
                MERGE (node:ProductVersion {name:%s, ofuri:%s, object_id:%s})
                RETURN node
            $$) as (node agtype);
            """,
            ["products", product_name, ofuri, str(object_id)],
        )
    if product_type == "ProductStream":
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
                MERGE (node:ProductStream {name:%s, ofuri:%s, object_id:%s})
                RETURN node
            $$) as (node agtype);
            """,
            ["products", product_name, ofuri, str(object_id)],
        )
    if product_type == "ProductVariant":
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
                MERGE (node:ProductVariant {name:%s, ofuri:%s, object_id:%s})
                RETURN node
            $$) as (node agtype);
            """,
            ["products", product_name, ofuri, str(object_id)],
        )
    if product_type == "Channel":
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
                MERGE (node:Channel {name:%s, ofuri:%s, object_id:%s})
                RETURN node
            $$) as (node agtype);
            """,
            ["products", product_name, ofuri, str(object_id)],
        )
    return None


def create_product_node_edge(pk_parent, pk_child):
    """create product node edge between a parent and a child"""
    conn = connections["graph"]
    conn.ensure_connection()
    cursor = conn.connection.cursor()
    cursor.execute('SET search_path = ag_catalog, "$user", public')
    cursor.execute(
        """
        SELECT *
        FROM cypher(%s, $$
            MATCH (node1 {object_id:%s}), (node2 {object_id:%s})
            MERGE (node1)-[e:CHILD]->(node2)
            RETURN e
        $$) as (e agtype)
        """,
        ["products", str(pk_parent), str(pk_child)],
    )
    return None


def create_component_node(component_name, purl, object_id, type, namespace, version, nevra, arch):
    """create component node"""
    conn = connections["graph"]
    conn.ensure_connection()
    cursor = conn.connection.cursor()
    cursor.execute('SET search_path = ag_catalog, "$user", public')
    cursor.execute(
        """
        SELECT * from cypher(%s, $$
            MERGE (node:Component {name:%s, purl:%s, object_id:%s, type:%s, namespace:%s, version:%s, nevra:%s, arch:%s}) 
            RETURN node
        $$) as (node agtype);
        """,
        ["components", component_name, purl, str(object_id), type, namespace, version, nevra, arch],
    )
    return None


def create_component_node_edge(node_type, pk_parent, pk_child):
    """create component node edge between a parent and a child"""
    conn = connections["graph"]
    conn.ensure_connection()
    cursor = conn.connection.cursor()
    cursor.execute('SET search_path = ag_catalog, "$user", public')
    if node_type == "SOURCE":
        cursor.execute(
            """
            SELECT *
            FROM cypher(%s, $$
                MATCH (node1:Component {object_id:%s}), (node2:Component{object_id:%s})
                MERGE (node1)-[e:SOURCE]->(node2)
                RETURN e
            $$) as (e agtype)
            """,
            ["components", str(pk_parent), str(pk_child)],
        )
    if node_type == "REQUIRES":
        cursor.execute(
            """
            SELECT *
            FROM cypher(%s, $$
                MATCH (node1:Component {object_id:%s}), (node2:Component{object_id:%s})
                MERGE (node1)-[e:REQUIRES]->(node2)
                RETURN e
            $$) as (e agtype)
            """,
            ["components", str(pk_parent), str(pk_child)],
        )
    if node_type == "PROVIDES":
        cursor.execute(
            """
            SELECT *
            FROM cypher(%s, $$
                MATCH (node1:Component {object_id:%s}), (node2:Component{object_id:%s})
                MERGE (node1)-[e:PROVIDES]->(node2)
                RETURN e
            $$) as (e agtype)
            """,
            ["components", str(pk_parent), str(pk_child)],
        )
    if node_type == "PROVIDES_DEV":
        cursor.execute(
            """
            SELECT *
            FROM cypher(%s, $$
                MATCH (node1:Component {object_id:%s}), (node2:Component{object_id:%s})
                MERGE (node1)-[e:PROVIDES_DEV]->(node2)
                RETURN e
            $$) as (e agtype)
            """,
            ["components", str(pk_parent), str(pk_child)],
        )
    return None


def retrieve_component_relationships(purl, flat=True):
    """return flat list of component ids
    it is likely that we would want to return Component node data as
    well as a tree version
    """
    conn = connections["graph"]
    conn.ensure_connection()
    cursor = conn.connection.cursor()
    if flat:
        cursor.execute('SET search_path = ag_catalog, "$user", public')
        # TODO: Apache Age does not support edge OR condition
        out = []
        cursor.execute(
            """
            SELECT *
            FROM cypher(%s, $$
                MATCH (node1:Component {purl:%s})-[r:PROVIDES*]->(node2:Component)
                RETURN node2.object_id
                UNION
                MATCH (node1:Component {purl:%s})-[r:PROVIDES_DEV*]->(node2:Component)
                RETURN node2.object_id
            $$) as (node2 agtype)
            """,
            ["components", purl, purl],
        )
        for row in cursor.fetchall():
            out.extend(row)
        return [pk.replace('"', "") for pk in out]
    return None


def retrieve_component_upstream_relationships(purl, flat=True):
    """return flat list of component ids
    it is likely that we would want to return Component node data as
    well as a tree version
    """
    conn = connections["graph"]
    conn.ensure_connection()
    cursor = conn.connection.cursor()
    if flat:
        cursor.execute('SET search_path = ag_catalog, "$user", public')
        out = []
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
                MATCH (node1 {purl:%s})-[R:SOURCE]->(node2)
                RETURN node2.object_id
            $$) as (node2 agtype);
            """,
            ["components", purl],
        )
        for row in cursor.fetchall():
            out.extend(row)
        return [pk.replace('"', "") for pk in out]
    return None


def retrieve_component_source_relationships(purl, flat=True):
    """return flat list of component ids
    it is likely that we would want to return Component node data as
    well as a tree version
    """
    conn = connections["graph"]
    conn.ensure_connection()
    cursor = conn.connection.cursor()
    if flat:
        cursor.execute('SET search_path = ag_catalog, "$user", public')
        out = []
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
                MATCH (node1:Component {purl:%s})<-[R:PROVIDES*]-(node2:Component {type:"RPM", arch:"src"}) 
                RETURN node2.object_id
                UNION
                MATCH (node1:Component {purl:%s})<-[R:PROVIDES*]-(node2:Component {type:"OCI", arch:"noarch"}) 
                RETURN node2.object_id
            $$) as (node2 agtype);
            """,
            ["components", purl, purl],
        )  # noqa
        for row in cursor.fetchall():
            out.extend(row)
        return [pk.replace('"', "") for pk in out]
    return None


def retrieve_component_root(purl, root_type="RPM", root_arch="src", flat=True):
    """return flat list of component ids
    it is likely that we would want to return Component node data as
    well as a tree version
    """
    conn = connections["graph"]
    conn.ensure_connection()
    cursor = conn.connection.cursor()
    if flat:
        cursor.execute('SET search_path = ag_catalog, "$user", public')
        out = []
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
                MATCH (root {type:%s,arch:%s})-[:PROVIDES*]->(node2 {purl:%s})
                WHERE NOT exists( ()-[:PROVIDES]->(root) )
                RETURN root.object_id
            $$) as (root agtype);
            """,
            ["components", root_type, root_arch, purl],
        )
        for row in cursor.fetchall():
            out.extend(row)
        return [pk.replace('"', "") for pk in out]
    return None
