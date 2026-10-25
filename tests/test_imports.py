"""Every module must import. Without TEST_DATABASE_URL nothing imports skim.storage.postgres, so a syntax or placement error
there (it happened: a method appended after module-level SQL) would pass the whole suite and only fail at deploy."""
import importlib
import pkgutil

import skim


def test_every_module_imports_and_postgres_storage_has_the_full_ai_surface():
    for mod in pkgutil.walk_packages(skim.__path__, "skim."):
        importlib.import_module(mod.name)
    from skim.storage.memory import MemoryStorage
    from skim.storage.postgres import PostgresStorage
    ai_methods = [n for n in dir(MemoryStorage) if not n.startswith("_") and n in {
        "list_pending_messages", "list_open_events", "list_message_links", "commit_message_analysis", "list_events", "get_event",
        "list_message_analyses", "flag_events_of_deleted_messages", "ai_status", "list_pending_documents", "save_document_text",
        "record_document_error", "list_doc_chunks", "list_unembedded", "record_embed_failures", "save_embeddings",
        "search_embeddings", "count_embeddings", "reset_ai_data"}]
    assert len(ai_methods) == 19
    assert [m for m in ai_methods if not hasattr(PostgresStorage, m)] == []
