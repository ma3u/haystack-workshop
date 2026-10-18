// Metadata model of the agent-memory graph (meta-knowledge-graph schema).
// Run in Neo4j Browser / cypher-shell against bolt://127.0.0.1:7689, or via the MCP tool
// mcp__meta-knowledge-graph__neo4j_read_cypher. Every query here is read-only.

// M1: visual schema (Browser draws labels and relationship types as a graph)
CALL db.schema.visualization();

// M2: node counts per label
CALL db.labels() YIELD label
CALL (label) { MATCH (n) WHERE label IN labels(n) RETURN count(n) AS nodes }
RETURN label, nodes ORDER BY nodes DESC;

// M3: relationship counts per (start label)-[type]->(end label)
MATCH (a)-[r]->(b)
RETURN labels(a)[0] AS from, type(r) AS rel, labels(b)[0] AS to, count(*) AS n
ORDER BY n DESC;

// M4: properties per label, with types
CALL db.schema.nodeTypeProperties() YIELD nodeLabels, propertyName, propertyTypes
RETURN nodeLabels[0] AS label, collect(propertyName + ': ' + propertyTypes[0]) AS properties
ORDER BY label;

// M5: constraints and indexes (vector + fulltext indexes back recall)
SHOW INDEXES YIELD name, type, labelsOrTypes, properties, state
RETURN name, type, labelsOrTypes, properties, state ORDER BY type, name;
