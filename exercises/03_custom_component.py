"""
Exercise 3: Custom Haystack Component
Goal: Build custom @component with typed inputs/outputs.

Tasks:
1. Decorate class with @component
2. Define run() with @component.output_types
3. Wire into pipeline
"""
from typing import List
from haystack import component, Pipeline, Document


@component
class KeywordFilter:
    """Filter docs containing all keywords."""

    def __init__(self, keywords: List[str]):
        self.keywords = [k.lower() for k in keywords]

    @component.output_types(documents=List[Document])
    def run(self, documents: List[Document]):
        filtered = [
            d for d in documents
            if all(k in d.content.lower() for k in self.keywords)
        ]
        return {"documents": filtered}


@component
class WordCounter:
    @component.output_types(counts=List[int], total=int)
    def run(self, documents: List[Document]):
        counts = [len(d.content.split()) for d in documents]
        return {"counts": counts, "total": sum(counts)}


if __name__ == "__main__":
    docs = [
        Document(content="Haystack pipelines are powerful and flexible."),
        Document(content="Pipelines compose components into DAGs."),
        Document(content="Vector databases store embeddings efficiently."),
    ]

    pipe = Pipeline()
    pipe.add_component("filter", KeywordFilter(keywords=["pipeline"]))
    pipe.add_component("counter", WordCounter())
    pipe.connect("filter.documents", "counter.documents")

    result = pipe.run({"filter": {"documents": docs}})
    print(f"Filtered: {len(result['counter']['counts'])} docs, {result['counter']['total']} words")
