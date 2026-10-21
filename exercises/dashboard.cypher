// Aura dashboard "Haystack GraphRAG on Neo4j": one query per widget. Read-only.
// Data: 07, 08, 09, 11 and eval_to_neo4j.py run against the Aura instance (NEO4J_URI = NEO4J_AURA_URI).

// ===== Page 1: RAG vs GraphRAG =====

// D1 (single value): GraphRAG accuracy
MATCH (:EvalVariant {name: 'graphrag'})-[a:ANSWERED]->() RETURN toString(toInteger(round(100 * avg(a.accuracy)))) + ' %' AS `GraphRAG accuracy`;

// D2 (single value): vector RAG accuracy
MATCH (:EvalVariant {name: 'vector'})-[a:ANSWERED]->() RETURN toString(toInteger(round(100 * avg(a.accuracy)))) + ' %' AS `Vector RAG accuracy`;

// D3 (single value): multi-hop gap, GraphRAG minus vector RAG
MATCH (v:EvalVariant)-[a:ANSWERED]->(q:EvalQuestion) WHERE q.hops > 1
WITH v.name AS variant, avg(a.accuracy) AS acc
WITH collect([variant, acc]) AS rows
RETURN '+' + toString(toInteger(round(100 * ([r IN rows WHERE r[0] = 'graphrag'][0][1] - [r IN rows WHERE r[0] = 'vector'][0][1])))) + ' pts' AS `Multi-hop gain`;

// D4 (bar): accuracy by variant and question type (one column per series: Aura bar charts ignore a group key)
MATCH (v:EvalVariant)-[a:ANSWERED]->(q:EvalQuestion)
WITH v.name AS variant, avg(CASE WHEN q.hops = 1 THEN a.accuracy END) AS single, avg(CASE WHEN q.hops > 1 THEN a.accuracy END) AS multi
RETURN variant, toInteger(round(100 * single)) AS `1-hop`, toInteger(round(100 * multi)) AS `multi-hop`
ORDER BY `multi-hop`;

// D5 (bar): cost side, average prompt context size in tokens
MATCH (v:EvalVariant)-[a:ANSWERED]->() RETURN v.name AS variant, toInteger(round(avg(a.ctx_tokens))) AS ctx_tokens ORDER BY ctx_tokens;

// D6 (table): per-question accuracy (3 runs)
MATCH (q:EvalQuestion)
OPTIONAL MATCH (:EvalVariant {name: 'vector'})-[v:ANSWERED]->(q)
OPTIONAL MATCH (:EvalVariant {name: 'vector_k8'})-[k:ANSWERED]->(q)
OPTIONAL MATCH (:EvalVariant {name: 'graphrag'})-[g:ANSWERED]->(q)
RETURN q.id AS id, q.hops AS hops, q.question AS question, q.answer AS reference,
       toInteger(round(100 * v.accuracy)) AS vector, toInteger(round(100 * k.accuracy)) AS vector_k8, toInteger(round(100 * g.accuracy)) AS graphrag
ORDER BY q.hops, q.id;

// ===== Page 2: Knowledge graph =====

// D7a (single value): chunks
MATCH (c:GraphRagChunk) RETURN count(c) AS Chunks;

// D7b (single value): entities
MATCH (e:GraphRagEntity) RETURN count(e) AS Entities;

// D7c (single value): relations
MATCH (:GraphRagEntity)-[r:REL]->(:GraphRagEntity) RETURN count(r) AS Relations;

// D8 (graph): multi-hop path for "In which office does the manager of Project Halcyon's lead work?"
MATCH p = (:GraphRagEntity {key: 'project halcyon'})-[:REL*1..3]-(:GraphRagEntity {type: 'Location'})
RETURN p LIMIT 25;

// D9 (bar): entity types extracted by the LLM
MATCH (e:GraphRagEntity) RETURN e.type AS type, count(*) AS entities ORDER BY entities DESC;

// D10 (table): most connected entities
MATCH (e:GraphRagEntity)
RETURN e.name AS entity, e.type AS type, COUNT { (e)-[:REL]-() } AS relations, COUNT { (:GraphRagChunk)-[:MENTIONS]->(e) } AS chunks
ORDER BY relations DESC LIMIT 15;

// D11 (graph): the whole entity graph
MATCH p = (:GraphRagEntity)-[:REL]->(:GraphRagEntity) RETURN p;

// D12 (table): which entities each eval question depends on
MATCH (q:EvalQuestion)-[:ABOUT]->(e:GraphRagEntity)
RETURN q.id AS id, q.question AS question, collect(e.name) AS entities ORDER BY id;

// ===== Page 3: Agent memory =====

// D13 (graph): what the agent remembers (current facts)
MATCH (:MemoryUser {id: 'demo-user'})-[:REMEMBERS]->(a)-[r:MEM_REL]->(b) WHERE r.valid_to IS NULL
RETURN a, r, b;

// D14 (table): fact history, including corrections
MATCH (:MemoryUser {id: 'demo-user'})-[:REMEMBERS]->(a)-[r:MEM_REL]->(b)
RETURN a.name AS subject, r.type AS relation, b.name AS object,
       toString(r.ts) AS stored, toString(r.valid_to) AS forgotten,
       CASE WHEN r.valid_to IS NULL THEN 'current' ELSE 'superseded' END AS status
ORDER BY status, stored;

// D15 (table): chat history per session
MATCH (:MemoryUser {id: 'demo-user'})-[:HAS_SESSION]->(s)-[:HAS_MESSAGE]->(m)
RETURN s.id AS session, m.seq AS seq, m.role AS role, left(m.text, 160) AS text ORDER BY session, seq;

// ===== Page 4: Document stores =====

// D16 (table): documents by store
MATCH (d) WHERE d:HaystackDocument OR d:HaystackRagDocument OR d:GraphRagChunk
RETURN labels(d)[0] AS store, count(d) AS documents, count(d.embedding) AS with_embedding ORDER BY store;

// D17 (table): vector RAG chunks (exercise 08)
MATCH (d:HaystackRagDocument) RETURN d.`meta.split_id` AS split, left(d.content, 200) AS content ORDER BY split;
