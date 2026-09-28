import hashlib

def generate_content_hash(content: bytes) -> str:
    """Generate SHA256 hash for raw file content."""
    return hashlib.sha256(content).hexdigest()

def generate_ingest_fingerprint(source_hash: str, parser_version: str) -> str:
    """
    Generate a fingerprint based on source hash and parser version.
    This allows for re-ingestion if the parser logic is updated.
    """
    composite = f"{source_hash}:{parser_version}".encode('utf-8')
    return hashlib.sha256(composite).hexdigest()
