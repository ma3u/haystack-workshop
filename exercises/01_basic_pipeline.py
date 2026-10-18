"""
Exercise 1: Basic Haystack Pipeline
Goal: Build minimal Q&A pipeline using ChatPromptBuilder + ChatGenerator.
(Haystack 3.x removed the non-chat OpenAIGenerator; use OpenAIChatGenerator.)

Tasks:
1. Create InMemoryDocumentStore with sample docs
2. Wire ChatPromptBuilder -> OpenAIChatGenerator
3. Query with context
"""
import os
from dotenv import load_dotenv
from haystack import Pipeline, Document
from haystack.document_stores.in_memory import InMemoryDocumentStore
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever
from haystack.components.builders import ChatPromptBuilder
from haystack.dataclasses import ChatMessage

from llm import chat_generator

load_dotenv()

docs = [
    Document(content="Haystack is an open-source framework by deepset for building production LLM apps."),
    Document(content="Pipelines are declarative DAGs of components connected via named inputs/outputs."),
    Document(content="Components are reusable building blocks: retrievers, generators, embedders, writers."),
]

# TODO 1: init DocumentStore and write docs
store = InMemoryDocumentStore()
store.write_documents(docs)

# TODO 2: build pipeline
template = [ChatMessage.from_user("""
Context:
{% for doc in documents %}{{ doc.content }}
{% endfor %}

Question: {{ query }}
Answer:
""")]

pipe = Pipeline()
pipe.add_component("retriever", InMemoryBM25Retriever(document_store=store))
pipe.add_component("prompt", ChatPromptBuilder(template=template, required_variables=["query"]))
pipe.add_component("llm", chat_generator())

pipe.connect("retriever.documents", "prompt.documents")
pipe.connect("prompt.prompt", "llm.messages")

# TODO 3: run
if __name__ == "__main__":
    query = "What is Haystack?"
    result = pipe.run({"retriever": {"query": query}, "prompt": {"query": query}})
    print(result["llm"]["replies"][0].text)
