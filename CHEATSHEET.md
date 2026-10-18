# Haystack 2.x Cheatsheet

## Pipeline API

```python
from haystack import Pipeline
p = Pipeline()
p.add_component("name", instance)
p.connect("src.output", "dst.input")
result = p.run({"name": {"input_key": value}})
```

## Document

```python
from haystack import Document
Document(content="...", meta={"source": "x"}, score=0.9, embedding=[...])
```

## Document Stores

| Store | Import | Use |
|-------|--------|-----|
| InMemory | `haystack.document_stores.in_memory.InMemoryDocumentStore` | Dev, demos |
| Qdrant | `haystack_integrations.document_stores.qdrant.QdrantDocumentStore` | Production vector |
| Elasticsearch | `haystack_integrations.document_stores.elasticsearch.ElasticsearchDocumentStore` | Hybrid, metadata |
| Weaviate | `haystack_integrations.document_stores.weaviate.WeaviateDocumentStore` | Managed vector |
| Chroma | `haystack_integrations.document_stores.chroma.ChromaDocumentStore` | Local dev |

Integrations installed via `pip install qdrant-haystack`, `elasticsearch-haystack`, etc.

## Retrievers

```python
# BM25 (sparse)
InMemoryBM25Retriever(document_store=store, top_k=5)

# Dense (embedding)
InMemoryEmbeddingRetriever(document_store=store, top_k=5)
# Needs query_embedding input from SentenceTransformersTextEmbedder
# 3.x: from haystack_integrations.components.embedders.sentence_transformers import ...
#      (pip install sentence-transformers-haystack)

# Filters
retriever.run(query="x", filters={"field": "meta.source", "operator": "==", "value": "docs"})
```

## Generators

```python
# Haystack 3.x: chat generators only (OpenAIGenerator removed). Input = messages, output = replies[ChatMessage]
OpenAIChatGenerator(model="gpt-4o-mini", generation_kwargs={"temperature": 0.0})
OpenAIChatGenerator(model="gpt-4o-mini", streaming_callback=cb)
MockChatGenerator(responses="canned answer")  # offline testing, no API key
AnthropicChatGenerator(model="claude-3-5-sonnet-20241022")
HuggingFaceAPIGenerator(api_type="serverless_inference_api", api_params={"model": "meta-llama/Llama-3.1-8B"})
```

## PromptBuilder (Jinja2)

```python
template = """
{% for d in documents %}
[{{loop.index}}] {{ d.content }}
{% endfor %}
Question: {{ query }}
"""
PromptBuilder(template=template, required_variables=["query"])
```

## Custom @component

```python
from haystack import component
from typing import List

@component
class MyComp:
    @component.output_types(result=List[str], count=int)
    def run(self, texts: List[str]):
        return {"result": texts, "count": len(texts)}
```

## Agent (NEW)

```python
from haystack.components.agents import Agent
from haystack.tools import Tool

tool = Tool(
    name="search",
    description="Search docs",
    parameters={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
    function=my_search_fn,
)
agent = Agent(
    chat_generator=OpenAIChatGenerator(model="gpt-4o-mini"),
    tools=[tool],
    system_prompt="...",
    max_agent_steps=10,
    exit_conditions=["text"],  # or specific tool names
)
agent.warm_up()
agent.run(messages=[ChatMessage.from_user("...")])
```

## Serialization

```python
yaml_str = pipeline.dumps()                 # to YAML
pipeline = Pipeline.loads(yaml_str)         # from YAML
pipeline.save("pipe.yaml")
Pipeline.load("pipe.yaml")
```

## Debugging

```python
# Visualize
pipeline.show()                              # Jupyter
pipeline.draw("pipe.png")

# Inspect intermediate outputs
pipeline.run({...}, include_outputs_from={"retriever", "prompt"})

# Prevent infinite loops
Pipeline(max_runs_per_component=50)
```

## Document Chunking

```python
from haystack.components.preprocessors import DocumentSplitter, DocumentCleaner

DocumentCleaner(remove_empty_lines=True, remove_extra_whitespaces=True)
DocumentSplitter(split_by="word", split_length=200, split_overlap=20)
```

## Converters (ingest)

```python
from haystack.components.converters import (
    TextFileToDocument,
    PyPDFToDocument,
    HTMLToDocument,
    MarkdownToDocument,
    DOCXToDocument,
)
```

## Evaluation

```python
from haystack.components.evaluators import (
    FaithfulnessEvaluator,
    ContextRelevanceEvaluator,
    SASEvaluator,            # semantic answer similarity
    AnswerExactMatchEvaluator,
)
```

## Common Gotchas

- `required_variables=[...]` on PromptBuilder forces Jinja error on missing
- `warm_up()` loads models — call before `.run()` to avoid first-call latency
- Component input names must match connection name exactly
- Agents need `warm_up()` to initialize tool bindings
- `include_outputs_from` must be a `set`, not list
