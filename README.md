# Haystack Workshop - Hackathon Prep

Deep preparation for upcoming Deepset Haystack hackathon.

## Setup

```bash
cd haystack-workshop
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Structure

- `exercises/` - Hands-on coding challenges
- `notebooks/` - Jupyter notebooks for exploration
- `data/` - Sample datasets and documents
- `solutions/` - Reference implementations
- `claude_memory/` - Neo4j graph memory for Claude Code via MCP, CLI and HTTP API, with benchmark and live test ([README](claude_memory/README.md))

## Key Haystack 2.x Concepts

- **Pipeline**: Declarative workflow composition
- **Components**: Reusable building blocks (LLMs, retrievers, writers)
- **Document Stores**: Vector DBs, BM25, hybrid retrieval
- **RAG Patterns**: Retrieval-Augmented Generation workflows

## Quick Start

```python
from haystack import Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.components.generators.chat import OpenAIChatGenerator
from haystack.dataclasses import ChatMessage

pipeline = Pipeline()
pipeline.add_component("prompt", ChatPromptBuilder(template=[ChatMessage.from_user("What is {{query}}?")]))
pipeline.add_component("llm", OpenAIChatGenerator())
pipeline.connect("prompt.prompt", "llm.messages")

result = pipeline.run({"prompt": {"query": "Haystack"}})
print(result["llm"]["replies"][0].text)
```

## Haystack 3.x Gotchas

- `OpenAIGenerator` removed: use `OpenAIChatGenerator` (input `messages`, output `replies[ChatMessage]`).
- SentenceTransformers components moved to `haystack_integrations.components.embedders.sentence_transformers` (`pip install sentence-transformers-haystack`).
- `MockChatGenerator` runs pipelines offline without an API key.

## Resources

- [Haystack Docs](https://docs.deepset.ai/latest)
- [API Reference](https://docs.deepset.ai/latest/api)
- [GitHub](https://github.com/deepset-ai/haystack)
