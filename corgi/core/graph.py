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
            MERGE (V:Product {name:%s, ofuri:%s, object_id:%s})
            RETURN V
            $$) as (V agtype);
            """,
            ["products", product_name, ofuri, str(object_id)],
        )
    if product_type == "ProductVersion":
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
            MERGE (V:ProductVersion {name:%s, ofuri:%s, object_id:%s})
            RETURN V
            $$) as (V agtype);
            """,
            ["products", product_name, ofuri, str(object_id)],
        )
    if product_type == "ProductStream":
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
            MERGE (V:ProductStream {name:%s, ofuri:%s, object_id:%s})
            RETURN V
            $$) as (V agtype);
            """,
            ["products", product_name, ofuri, str(object_id)],
        )
    if product_type == "ProductVariant":
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
            MERGE (V:ProductVariant {name:%s, ofuri:%s, object_id:%s})
            RETURN V
            $$) as (V agtype);
            """,
            ["products", product_name, ofuri, str(object_id)],
        )
    if product_type == "Channel":
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
            MERGE (V:Channel {name:%s, ofuri:%s, object_id:%s})
            RETURN V
            $$) as (V agtype);
            """,
            ["products", product_name, ofuri, str(object_id)],
        )
    return None


def create_product_node_edge(pk_parent, pk_child):
    """create product node edge between a parent and a child"""
    logger.info(pk_parent)
    conn = connections["graph"]
    conn.ensure_connection()
    cursor = conn.connection.cursor()
    cursor.execute('SET search_path = ag_catalog, "$user", public')
    cursor.execute(
        """
        SELECT *
        FROM cypher(%s, $$
        MATCH (a {object_id:%s}), (b {object_id:%s})
        CREATE (a)-[e:CHILD]->(b)
        RETURN e
        $$) as (e agtype)
        """,
        ["products", str(pk_parent), str(pk_child)],
    )
    return None


def create_component_node(component_name, purl, object_id, type, namespace, version, nevra):
    """create component node"""
    conn = connections["graph"]
    conn.ensure_connection()
    cursor = conn.connection.cursor()
    cursor.execute('SET search_path = ag_catalog, "$user", public')
    cursor.execute(
        """
        SELECT * from cypher(%s, $$
        MERGE (V:Component {name:%s, purl:%s, object_id:%s,type:%s,namespace:%s,version:%s,nevra:%s})
        RETURN V
        $$) as (V agtype);
        """,
        ["components", component_name, purl, str(object_id), type, namespace, version, nevra],
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
            MATCH (a:Component {object_id:%s}), (b:Component{object_id:%s})
            CREATE (a)-[e:SOURCE]->(b)
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
            MATCH (a:Component {object_id:%s}), (b:Component{object_id:%s})
            CREATE (a)-[e:REQUIRES]->(b)
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
            MATCH (a:Component {object_id:%s}), (b:Component{object_id:%s})
            CREATE (a)-[e:PROVIDES]->(b)
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
            MATCH (a:Component {object_id:%s}), (b:Component{object_id:%s})
            CREATE (a)-[e:PROVIDES_DEV]->(b)
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
            MATCH (a:Component {purl:%s})-[r:PROVIDES*]->(b:Component)
            RETURN b.object_id
            $$) as (b agtype)
            """,
            ["components", purl],
        )
        for row in cursor.fetchall():
            out.extend(row)
        cursor.execute(
            """
            SELECT *
            FROM cypher(%s, $$
            MATCH (a:Component {purl:%s})-[r:PROVIDES_DEV*]->(b:Component)
            RETURN b.object_id
            $$) as (b agtype)
            """,
            ["components", purl],
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
        # TODO: Apache Age does not support edge OR condition
        out = []
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
                    MATCH (V {purl:%s})-[R:SOURCE]->(V2)
                    RETURN V2.object_id
            $$) as (V agtype);
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
        # TODO: Apache Age does not support edge OR condition
        out = []
        cursor.execute(
            """
            SELECT * from cypher(%s, $$
                    MATCH (V {purl:%s})-[R:SOURCE]->(V2)
                    RETURN V2.object_id
            $$) as (V agtype);
            """,
            ["components", purl],
        )
        for row in cursor.fetchall():
            out.extend(row)
        return [pk.replace('"', "") for pk in out]
    return None
