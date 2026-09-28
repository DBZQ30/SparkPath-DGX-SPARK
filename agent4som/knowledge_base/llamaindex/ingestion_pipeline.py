"""LlamaIndex IngestionPipeline factory for SOM knowledge base.

Assembles the transformation chain:
  SentenceSplitter → RawIndexNodeMetadataExtractor

Embedding is handled separately by ``ChromaVectorStoreAdapter`` which
delegates to the existing ``ChromaRepository`` (which already owns the
Qwen3-Embedding-0.6B function).
"""

from __future__ import annotations


from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import BaseNode, Document

from knowledge_base.llamaindex.metadata_extractor import RawIndexNodeMetadataExtractor


def create_ingestion_pipeline(
    chunk_size: int = 512,
    chunk_overlap: int = 32,
    parser_version: str = "llamaindex-v1",
) -> IngestionPipeline:
    """Build the standard SOM ingestion pipeline.

    Transformations:
      1. ``SentenceSplitter`` — structure-aware chunking (sentence/paragraph
         boundaries, Chinese punctuation aware).
      2. ``RawIndexNodeMetadataExtractor`` — normalise per-format metadata
         into the 22-field ``RawIndexNode`` schema.
    """
    return IngestionPipeline(
        transformations=[
            SentenceSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                paragraph_separator="\n\n",
            ),
            RawIndexNodeMetadataExtractor(parser_version=parser_version),
        ],
    )


def run_pipeline(
    pipeline: IngestionPipeline,
    documents: list[Document],
    show_progress: bool = False,
) -> list[BaseNode]:
    """Run *pipeline* on *documents* and return ``TextNode`` list."""
    return pipeline.run(documents=documents, show_progress=show_progress)
