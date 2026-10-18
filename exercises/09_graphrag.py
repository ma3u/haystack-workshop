"""
Exercise 9: GraphRAG on Neo4j.

Plain vector RAG retrieves passages that look like the question. Multi-hop questions
("In which office does the manager of Project Halcyon's lead work?") need facts spread over
several passages that do NOT look like the question. GraphRAG adds an entity graph:

  indexing:  passages -> LLM extracts (entity)-[RELATION]->(entity) -> Neo4j
  query:     vector + rerank seeds -> link question/seed entities -> 2-hop graph expansion -> facts
             -> prompt gets passages AND facts

Corpus: data/graphrag_corpus.txt, a fictional company, so the LLM cannot answer from memory.

Run:  python 09_graphrag.py            # builds the graph once (~25 LLM calls), then demo
      python 09_graphrag.py --reset    # wipe and rebuild
Then: python 10_eval_rag_vs_graphrag.py

Neo4j Browser:  MATCH p=(:GraphRagEntity)-[:REL]->(:GraphRagEntity) RETURN p
"""
import sys

from graphrag_pipelines import CORPUS, ENTITY_LABEL, build_indexing, build_query, make_store, query_inputs, reset_graph

store = make_store()


def build_graph() -> None:
    if "--reset" in sys.argv:
        reset_graph(store)
    if store.count_documents() and store.run_cypher(f"MATCH (e:{ENTITY_LABEL}) RETURN count(e) AS c")[0]["c"]:
        print("Graph already built (use --reset to rebuild).")
    else:
        print("Building graph: embedding chunks + LLM entity extraction ...")
        out = build_indexing(store).run({"converter": {"sources": [CORPUS]}})
        print(f"  chunks written: {out['writer']['documents_written']}, "
              f"entities: {out['graph_writer']['entities_written']}, relations: {out['graph_writer']['relations_written']}")
    stats = store.run_cypher(
        f"MATCH (e:{ENTITY_LABEL}) WITH count(e) AS entities "
        f"MATCH (:{ENTITY_LABEL})-[r:REL]->(:{ENTITY_LABEL}) RETURN entities, count(r) AS relations"
    )[0]
    print(f"Graph in Neo4j: {store.count_documents()} chunks, {stats['entities']} entities, {stats['relations']} relations\n")


if __name__ == "__main__":
    build_graph()

    vector = build_query(store, "vector")
    graph = build_query(store, "graphrag")
    for question in [
        "In which office does the manager of Project Halcyon's lead work?",
        "In which city is the manufacturer of the batteries used by Halcyon drones based?",
    ]:
        print(f"Q: {question}")
        v = vector.run(query_inputs(vector, question))
        print(f"  vector  : {v['llm']['replies'][0].text}")
        g = graph.run(query_inputs(graph, question), include_outputs_from={"graph"})
        print(f"  graphrag: {g['llm']['replies'][0].text}")
        print("  graph facts used (first 6):")
        for fact in g["graph"]["facts"][:6]:
            print(f"    {fact}")
        print()

    store.close()
