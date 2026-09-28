from abc import ABC, abstractmethod
from knowledge_base.models.schemas import RawIndexNode, BaseFact


class KnowledgeBaseRepository(ABC):
    """Port: abstract storage for the RAG data plane.

    Implementations must handle tier-scoped persistence of:
    - Raw index nodes (searchable document chunks with provenance metadata)
    - Normalized facts (structured data extracted from documents)
    """

    @abstractmethod
    def store_nodes(self, scope: str, nodes: list[RawIndexNode]) -> None:
        ...

    @abstractmethod
    def search_nodes(
        self, scopes: list[str], query_embedding: list[float], top_k: int = 5
    ) -> list[RawIndexNode]:
        ...

    @abstractmethod
    def delete_nodes(self, scope: str, source_file: str | None = None) -> bool:
        ...

    @abstractmethod
    def store_facts(self, scope: str, facts: list[BaseFact]) -> None:
        ...

    @abstractmethod
    def get_facts_by_keys(
        self, scopes: list[str], fact_keys: list[str]
    ) -> list[BaseFact]:
        ...

    @abstractmethod
    def purge_scope(self, scope: str) -> None:
        """Remove all nodes and facts under a given scope (used for user data cleanup)."""
        ...

    # ── quota & versioning support ───────────────────────────────────

    @abstractmethod
    def get_user_file_count(self, user_id: str) -> int:
        """Return the number of files ingested by *user_id* (for quota enforcement)."""
        ...

    @abstractmethod
    def get_user_daily_upload_count(self, user_id: str) -> int:
        """Return how many uploads *user_id* has performed today (for daily-limit check)."""
        ...

    @abstractmethod
    def get_file_metadata(self, user_id: str, filename: str, scope: str = "") -> dict[str, str | None]:
        """Return stored metadata (e.g. content_hash) for a previously-ingested file."""
        ...

    @abstractmethod
    def get_file_metadata_any_user(self, filename: str, scope: str) -> dict[str, str | None]:
        """Check if *filename* exists in *scope* for ANY user (shared-scope dedup)."""
        ...

    @abstractmethod
    def set_file_metadata(self, user_id: str, filename: str, metadata: dict[str, str]) -> None:
        """Persist metadata (e.g. content_hash) for a file after successful ingestion."""
        ...

    @abstractmethod
    def delete_user_data(self, user_id: str) -> None:
        """Remove all quota/version metadata for a user (cascade delete)."""
        ...
