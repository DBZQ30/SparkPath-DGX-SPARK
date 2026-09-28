from knowledge_base.utils.hashing import generate_ingest_fingerprint, generate_content_hash

def test_ingest_fingerprint_generation():
    """Test generating fingerprint based on source hash and parser version."""
    source_hash = "abc123hash"
    parser_v1 = "1.0.0"
    parser_v2 = "1.1.0"

    fp1 = generate_ingest_fingerprint(source_hash, parser_v1)
    fp2 = generate_ingest_fingerprint(source_hash, parser_v2)

    # Must be consistent
    assert generate_ingest_fingerprint(source_hash, parser_v1) == fp1
    # Must change when parser updates
    assert fp1 != fp2

def test_generate_content_hash():
    """Test hash generation from raw bytes."""
    data = b"Hello SOM"
    h1 = generate_content_hash(data)
    h2 = generate_content_hash(b"Hello SOM")
    h3 = generate_content_hash(b"Goodbye SOM")

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64 # Assuming SHA256 hex digest
