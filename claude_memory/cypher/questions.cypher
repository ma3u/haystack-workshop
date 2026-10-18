// Ten questions to the agent memory about Neo4j projects and challenges. Read-only.
// ask_memory.py runs every block below (split on the "// Qn:" headers) and renders the results.
// MKG links a session to every project directory it touched (cd into a subfolder creates another :Project).
// Per-project questions therefore use one home project per session: the project whose repo_root is the cwd of
// the session's first event; subagent worktrees under .claude/worktrees/ count for the repository they belong to.

// Q1: Which projects did I work on with Claude Code, and how much?
MATCH (s:Session)-[:FIRST_EVENT]->(f:SessionEvent)
MATCH (p:Project {repo_root: split(f.cwd, '/.claude/worktrees/')[0]})
MATCH (s)-[:HAS_EVENT]->(e:SessionEvent)
RETURN p.name AS project, count(DISTINCT s) AS sessions,
       count(CASE WHEN e.event_name = 'UserPromptSubmit' THEN 1 END) AS prompts,
       count(CASE WHEN e.event_name = 'PreToolUse' THEN 1 END) AS tool_calls,
       toString(date(min(e.timestamp))) AS first, toString(date(max(e.timestamp))) AS last
ORDER BY prompts DESC;

// Q2: In which projects did Neo4j come up, and how often (prompts, tool calls)?
MATCH (s:Session)-[:FIRST_EVENT]->(f:SessionEvent)
MATCH (p:Project {repo_root: split(f.cwd, '/.claude/worktrees/')[0]})
MATCH (s)-[:HAS_EVENT]->(e:SessionEvent)
WHERE toLower(coalesce(e.prompt, '') + ' ' + coalesce(e.tool_input, '')) CONTAINS 'neo4j'
RETURN p.name AS project,
       count(CASE WHEN e.event_name = 'UserPromptSubmit' THEN 1 END) AS neo4j_prompts,
       count(CASE WHEN e.event_name = 'PreToolUse' THEN 1 END) AS neo4j_tool_calls
ORDER BY neo4j_prompts + neo4j_tool_calls DESC;

// Q3: Which Neo4j instances (connection URIs) did I use, where?
MATCH (s:Session)-[:FIRST_EVENT]->(f:SessionEvent)
MATCH (p:Project {repo_root: split(f.cwd, '/.claude/worktrees/')[0]})
MATCH (s)-[:HAS_EVENT]->(e:SessionEvent)
WITH p, coalesce(e.prompt, '') + ' ' + coalesce(e.tool_input, '') + ' ' + coalesce(e.tool_response, '') AS t
// Escaped JSON leaves backslashes and quotes around the URI: turn them and \n into separators first
WITH p, reduce(x = t, c IN ['\\n', '\\', '"', "'", '`', ',', ';', '(', ')', '\n'] | replace(x, c, ' ')) AS t
WITH p, [m IN split(t, ' ')
         WHERE m =~ '(bolt|neo4j)(\\+s|\\+ssc)?://[A-Za-z0-9.\\-]+:[0-9]{4,5}(/.*)?' | split(m, '/')[2]] AS uris
UNWIND uris AS uri
RETURN uri, collect(DISTINCT p.name) AS projects, count(*) AS mentions
ORDER BY mentions DESC LIMIT 10;

// Q4: Which Neo4j / Cypher errors did I hit?
MATCH (s:Session)-[:FIRST_EVENT]->(f:SessionEvent)
MATCH (p:Project {repo_root: split(f.cwd, '/.claude/worktrees/')[0]})
MATCH (s)-[:HAS_EVENT]->(e:SessionEvent {event_name: 'PostToolUse'})
WHERE e.tool_response =~ '(?s).*(Neo\\.(ClientError|DatabaseError|TransientError)\\.[A-Za-z.]+|neo4j\\.exceptions\\.[A-Za-z]+).*'
WITH p, e, [x IN split(reduce(r = e.tool_response, c IN ['\\n', '\\', '{', '}', ':', '"', ',', '\n'] | replace(r, c, ' ')), ' ')
            WHERE NOT x CONTAINS '..'] AS words
// Prefer the full status code (Neo.ClientError...); tool output cut mid-code falls back to the exception class
WITH p, e, coalesce([x IN words WHERE x STARTS WITH 'Neo.'][0],
                    [x IN words WHERE x STARTS WITH 'neo4j.exceptions.'][0]) AS error
WHERE error IS NOT NULL  // a code cut off before its last segment leaves nothing usable
RETURN error, collect(DISTINCT p.name) AS projects, count(*) AS times,
       toString(date(min(e.timestamp))) AS first_seen
ORDER BY times DESC LIMIT 10;

// Q5: How was each Neo4j error resolved? Error -> next successful call of the same tool in that session
MATCH (bad:SessionEvent {event_name: 'PostToolUse'})
WHERE bad.tool_response =~ '(?s).*(Neo\\.(ClientError|DatabaseError)\\.[A-Za-z.]+|neo4j\\.exceptions\\.[A-Za-z]+|AuthError).*'
MATCH path = (bad)-[:NEXT*1..40]->(fix:SessionEvent {event_name: 'PostToolUse'})
WHERE fix.tool_name = bad.tool_name
  AND NOT fix.tool_response =~ '(?s).*(Error|Traceback|exceptions).*'
WITH bad, fix, length(path) AS steps ORDER BY steps
WITH bad, head(collect({fix: fix, steps: steps})) AS first
MATCH (pre:SessionEvent {event_name: 'PreToolUse', tool_use_id: first.fix.tool_use_id})
WITH bad, first, pre,
     [x IN split(reduce(r = bad.tool_response, c IN ['\\n', '\\', '{', '}', ':', '"', ',', '\n'] | replace(r, c, ' ')), ' ')
      WHERE NOT x CONTAINS '..'] AS words
WITH bad, first, pre, coalesce([x IN words WHERE x STARTS WITH 'Neo.'][0],
                               [x IN words WHERE x STARTS WITH 'neo4j.exceptions.' OR x = 'AuthError'][0]) AS error
WHERE error IS NOT NULL
RETURN toString(date(bad.timestamp)) AS day, error,
       first.steps AS events_until_fix,
       left(replace(pre.tool_input, '\\n', ' '), 110) AS fixing_call
ORDER BY day LIMIT 10;

// Q6: Which Neo4j challenges and exercises did I ask for?
MATCH (s:Session)-[:FIRST_EVENT]->(f:SessionEvent)
MATCH (p:Project {repo_root: split(f.cwd, '/.claude/worktrees/')[0]})
MATCH (s)-[:HAS_EVENT]->(e:SessionEvent {event_name: 'UserPromptSubmit'})
WHERE toLower(e.prompt) =~ '(?s).*(challenge|exercise|excercise|graphrag|neo4j).*'
  AND size(e.prompt) < 400
RETURN toString(date(e.timestamp)) AS day, p.name AS project, left(replace(e.prompt, '\n', ' '), 110) AS prompt
ORDER BY e.timestamp LIMIT 15;

// Q7: Which Cypher / Neo4j features did I use in tool calls?
UNWIND [['vector index', 'CREATE VECTOR INDEX'], ['SEARCH clause (Cypher 25)', 'SEARCH '],
        ['db.index.vector.queryNodes', 'queryNodes'], ['vector.similarity', 'vector.similarity'],
        ['MERGE', 'MERGE '], ['constraint', 'CONSTRAINT'], ['COUNT {} subquery', 'COUNT {'],
        ['variable-length path', ']-*'], ['DETACH DELETE', 'DETACH DELETE'], ['fulltext index', 'FULLTEXT']] AS f
MATCH (e:SessionEvent {event_name: 'PreToolUse'})
WHERE e.tool_input CONTAINS f[1] OR (f[1] = ']-*' AND e.tool_input =~ '(?s).*\\[:[A-Z_]+\\*[0-9.]*.*')
RETURN f[0] AS feature, count(e) AS tool_calls, toString(date(min(e.timestamp))) AS first_used
ORDER BY tool_calls DESC;

// Q8: Which files did my Neo4j work touch most? (enriched tool-call graph)
MATCH (c:ToolCall)-[:TARGETS]->(r:Resource)
WHERE (toLower(c.tool_input) CONTAINS 'neo4j' OR toLower(r.name) CONTAINS 'neo4j' OR toLower(r.name) CONTAINS 'graph')
  // enrich also records tool responses, directories and shell fragments as resources: keep real file names only
  AND r.type = 'file' AND r.name =~ '[A-Za-z0-9_./~-]+\\.[A-Za-z0-9]{1,8}'
WITH r, c, last(split(r.name, '/')) AS file
RETURN file, collect(DISTINCT r.name)[0..2] AS paths, count(DISTINCT c) AS calls
ORDER BY calls DESC LIMIT 10;

// Q9: Where did the agent get blocked (permission denials, classifier), per project?
MATCH (s:Session)-[:FIRST_EVENT]->(f:SessionEvent)
MATCH (p:Project {repo_root: split(f.cwd, '/.claude/worktrees/')[0]})
MATCH (s)-[:HAS_EVENT]->(e:SessionEvent {event_name: 'PostToolUse'})
WHERE e.tool_response CONTAINS 'denied by the Claude Code auto mode classifier'
   OR e.tool_response STARTS WITH 'Permission'
WITH p, e, coalesce([x IN split(e.tool_response, 'Reason: ')[1..] | split(x, '.')[0]][0], 'permission denied') AS reason
RETURN p.name AS project, reason, count(*) AS times, collect(DISTINCT e.tool_name) AS tools
ORDER BY times DESC LIMIT 10;

// Q10: Explain a result: why does the workshop use bolt://127.0.0.1:7687? Trace prompt -> tool calls -> decision
MATCH (s:Session)-[:HAS_EVENT]->(change:SessionEvent {event_name: 'PreToolUse'})
WHERE change.tool_input CONTAINS '127.0.0.1:7687' AND change.tool_input CONTAINS 'NEO4J_URI'
WITH s, change ORDER BY change.timestamp LIMIT 1
MATCH (prompt:SessionEvent {event_name: 'UserPromptSubmit'})-[:NEXT*1..60]->(change)
WHERE (s)-[:HAS_EVENT]->(prompt)
WITH change, prompt ORDER BY prompt.timestamp DESC LIMIT 1
MATCH chain = (prompt)-[:NEXT*1..60]->(change)
UNWIND nodes(chain) AS e
WITH e WHERE e.event_name IN ['UserPromptSubmit', 'PreToolUse', 'PostToolUse']
RETURN toString(e.timestamp) AS ts, e.event_name AS event, e.tool_name AS tool,
       left(replace(replace(coalesce(e.prompt, e.tool_input, e.tool_response, ''), '\n', ' '), '\\n', ' '), 100) AS content;

// Q11: What did the memory learn per project? (LLM-extracted :Learning, after the autonomous gate)
MATCH (p:Project)-[:HAS_LEARNING]->(l:Learning)
RETURN p.name AS project, l.status AS status, count(*) AS learnings,
       collect(left(replace(l.text, '\n', ' '), 80))[0..2] AS examples
ORDER BY project, learnings DESC;

// Q12: Episodic timeline: what happened with Neo4j, in order? (:Observation chain)
MATCH (p:Project)-[:HAS_OBSERVATION]->(o:Observation)
WITH p, o, toLower(o.title + ' ' + coalesce(o.narrative, '') + ' ' + reduce(f = '', x IN coalesce(o.facts, []) | f + ' ' + x)) AS t
WHERE t =~ '(?s).*(neo4j|graphrag|cypher|knowledge graph|bolt://).*'
RETURN toString(date(datetime(o.started_at))) AS day, p.name AS project, o.type AS type,
       left(o.title, 90) AS observation
ORDER BY o.started_at LIMIT 15;
