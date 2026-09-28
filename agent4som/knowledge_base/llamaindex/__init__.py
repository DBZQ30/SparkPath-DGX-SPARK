"""LlamaIndex adaptor layer for SOM knowledge base.

This package bridges LlamaIndex's document processing pipeline with the
existing ChromaRepository-based storage.  LlamaIndex handles document
reading + parsing + chunking + metadata extraction; Hermes retains
control of ACL, quota, versioning, and conflict resolution.
"""

__all__ = [
    "RawIndexNodeMetadataExtractor",
    "StructuredExcelReader",
    "create_ingestion_pipeline",
    "create_simple_reader",
    "node_to_raw_index_node",
]
