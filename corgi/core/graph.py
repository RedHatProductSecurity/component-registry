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


def retrieve_component_provides_relationships(purl, flat=True):
    """return flat list of provides component purls

        Match all descendants from a given component
    `   ---------------
        MATCH (node1:Component {purl:%s})-[R*]->(node2:Component)
    `   ---------------

        refine the edge to match only PROVIDES or PROVIDES_DEV
    `   ---------------
        WITH R,node2
        WHERE type(R[0]) = 'PROVIDES' OR type(R[0]) = 'PROVIDES_DEV'
    `   ---------------
        the usage of WITH is anachronistic (a limitation of Apache AGE cypher) to be able to use
        type() function to retrieve relationship type ... once Apache AGE supports OR conditions directly
        we should be able to do this in the initial MATCH statement like

            (node1:Component {purl:%s})-[RLPROVIDES|PROVIDES_DEV*]->(node2:Component)
    `   ---------------
        RETURN node2.purl
    `   ---------------

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
                MATCH (node1:Component {purl:%s})-[R*]->(node2:Component) 
                WITH R,node2
                WHERE type(R[0]) = 'PROVIDES' OR type(R[0]) = 'PROVIDES_DEV'
                RETURN node2.purl
            $$) as (purl agtype)
            """,
            ["components", purl],
        )
        for row in cursor.fetchall():
            out.extend(row)
        return [purl.replace('"', "") for purl in out]
    return None


def retrieve_component_upstream_relationships(purl, flat=True):
    """return flat list of upstream component purls

        Search from given component for any immediate descendant component with a SOURCE edge.
    `   ---------------
        MATCH (node1:Component {purl:%s})-[R:SOURCE]->(node2:Component {namespace:'UPSTREAM'})
    `   ---------------
        this component (node2) should have namespace property equal to 'UPSTREAM'

        return the descendant (linked by SOURCE edge) purl
    `   ---------------
        RETURN node2.purl
    `   ---------------

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
                MATCH (node1:Component {purl:%s})-[R:SOURCE]->(node2:Component {namespace:'UPSTREAM'})
                RETURN node2.purl
            $$) as (purl agtype);
            """,
            ["components", purl],
        )
        for row in cursor.fetchall():
            out.extend(row)
        return [purl.replace('"', "") for purl in out]
    return None


def retrieve_component_source_relationships(purl, flat=True):
    """return flat list of source component purls

        The cypher query must return the sources of the supplied purl ...

        The first MATCH statement
    `   ---------------
        MATCH (node1:Component {purl:%s})<-[R*]-(node2:Component)
    `   ---------------
        essentially selects all ancestors (node2) of supplied component purl (node1)

        this is then narrowed down to the top ancestor by
    `   ---------------
        WHERE not(exists( (node2:Component)<-[]-() )) AND
    `   ---------------
        only selecting matches where node2 has no relationships pointing towards it

        then we narrow down by RPM/src or OCI/noarch property equivalence.
    `   ---------------
              ( (node2.type = 'RPM' AND node2.arch='src') OR (node2.type = 'OCI' AND node2.arch='noarch') )
    `   ---------------

        we then ensure the last match is a PROVIDES or PROVIDES_DEV relationship
    `   ---------------
        WITH R,node2
        WHERE type(R[0]) = 'PROVIDES' OR type(R[0]) = 'PROVIDES_DEV'
    `   ---------------
        the usage of WITH is anachronistic (a limitation of Apache AGE cypher) to be able to use
        type() function to retrieve relationship type ... once Apache AGE supports OR conditions directly
        we should be able to do this in the initial MATCH statement like

           (node1:Component {purl:%s})<-[R:PROVIDES|PROVIDES_DEV*]-(node2:Component)

        Finally we return just the purl of the source component
        ---------------
        RETURN node2.purl
       ---------------


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
                MATCH (node1:Component {purl:%s})<-[R*]-(node2:Component) 
                WHERE not(exists( (node2:Component)<-[]-() )) AND 
                      ( (node2.type = 'RPM' AND node2.arch='src') OR (node2.type = 'OCI' AND node2.arch='noarch') ) 
                WITH R,node2
                WHERE type(R[0]) = 'PROVIDES' OR type(R[0]) = 'PROVIDES_DEV' 
                RETURN node2.purl
            $$) as (purl agtype);
            """,
            ["components", purl],
        )  # noqa
        for row in cursor.fetchall():
            out.extend(row)
        return [purl.replace('"', "") for purl in out]
    return None


def retrieve_component_root(purl, root_type="RPM", root_arch="src", flat=True):
    """return flat list of component purls
        returns root component(s)

        The cypher query must return the roots of the supplied purl ... so it must
        do similar to retrieve sources but also return itself if it is the root node

        The first step is to check if supplied purl is itself a root component

        we first match it
        ---------------
        MATCH (node1:Component {purl:%s})
        ---------------

        then checks it is not provided by any other component via '(node1)<-[]-()' relationship match
        not existing ... and then the typical RPM/src or OCI/noarch property matches
        ---------------
        WHERE not(exists( (node1)<-[]-() )) AND
              ( (node1.type = 'RPM' AND node1.arch='src') OR (node1.type = 'OCI' AND node1.arch='noarch') )
        ---------------

        and return its purl value
        ---------------
        RETURN node1.purl
        ---------------

        The union  conflates with retrieve sources query so we only perform a single graphdb call
        ---------------
        UNION
        ---------------

        the following is verbatim cypher query for retrieving sources explained above.
    `   ---------------
        MATCH (node1:Component {purl:%s})<-[R*]-(node2:Component)
        WHERE not(exists( (node2:Component)<-[]-() )) AND
              ( (node2.type = 'RPM' AND node2.arch='src') OR (node2.type = 'OCI' AND node2.arch='noarch') )
        WITH R,node2
        WHERE type(R[0]) = 'PROVIDES' OR type(R[0]) = 'PROVIDES_DEV'
        RETURN node2.purl
       ---------------

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
    `           MATCH (node1:Component {purl:%s}) 
                WHERE not(exists( (node1)<-[]-() )) AND 
                      ( (node1.type = 'RPM' AND node1.arch='src') OR (node1.type = 'OCI' AND node1.arch='noarch') ) 
                RETURN node1.purl
                UNION
`                MATCH (node1:Component {purl:%s})<-[R*]-(node2:Component) 
                WHERE not(exists( (node2:Component)<-[]-() )) AND 
                      ( (node2.type = 'RPM' AND node2.arch='src') OR (node2.type = 'OCI' AND node2.arch='noarch') ) 
                WITH R,node2
                WHERE type(R[0]) = 'PROVIDES' OR type(R[0]) = 'PROVIDES_DEV' 
                RETURN node2.purl
            $$) as (purl agtype);
            """,
            ["components", purl, purl],
        )  # noqa
        for row in cursor.fetchall():
            out.extend(row)
        return [purl.replace('"', "") for purl in out]
    return None
