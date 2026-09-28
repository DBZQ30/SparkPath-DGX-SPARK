from knowledge_base.core.version_manager import VersionManager, IngestAction

class MockRegistryRepo:
    def __init__(self, existing_files=None):
        self.existing_files = existing_files or {}

    def get_file_metadata(self, user_id: str, filename: str, scope: str = ""):
        return self.existing_files.get(f"{user_id}:{filename}")

    def get_file_metadata_any_user(self, filename: str, scope: str = ""):
        for key, meta in self.existing_files.items():
            if filename in key:
                return meta
        return None

def test_dedup_same_content_hash_skips():
    repo = MockRegistryRepo({
        "user_1:test.pdf": {"content_hash": "hash123"}
    })
    manager = VersionManager(repo)

    action = manager.determine_ingest_action("user_1", "test.pdf", "hash123")
    assert action == IngestAction.SKIP

def test_dedup_diff_content_hash_replaces():
    repo = MockRegistryRepo({
        "user_1:test.pdf": {"content_hash": "old_hash"}
    })
    manager = VersionManager(repo)

    action = manager.determine_ingest_action("user_1", "test.pdf", "new_hash")
    assert action == IngestAction.REPLACE

def test_dedup_new_file_ingests():
    repo = MockRegistryRepo()
    manager = VersionManager(repo)

    action = manager.determine_ingest_action("user_1", "new.pdf", "hash123")
    assert action == IngestAction.NEW
