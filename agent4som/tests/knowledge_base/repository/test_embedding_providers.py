"""Tests for embedding providers with external API fallback."""
import logging
import pytest
from unittest.mock import MagicMock, patch
from knowledge_base.repository.embedding_providers import (
    OpenAICompatibleEmbeddingFunction,
    BuiltinEmbeddingFunction,
    FallbackEmbeddingFunction,
)


# ── OpenAICompatibleEmbeddingFunction ──────────────────────────────────

ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEEPSEEK_BASE = "https://api.deepseek.com/v1"


class TestOpenAICompatible:
    def test_calls_api_and_returns_embeddings(self):
        fn = OpenAICompatibleEmbeddingFunction(
            api_key="sk-test", model="embedding-2",
            base_url=ZHIPU_BASE,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3], "index": 0},
                {"embedding": [0.4, 0.5, 0.6], "index": 1},
            ]
        }

        with patch("requests.Session.post", return_value=mock_resp) as mock_post:
            result = fn(["text a", "text b"])

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["model"] == "embedding-2"
        assert call_kwargs["json"]["input"] == ["text a", "text b"]
        assert mock_post.call_args[0][0] == f"{ZHIPU_BASE}/embeddings"
        assert len(result) == 2
        assert list(result[0]) == [0.1, 0.2, 0.3]
        assert list(result[1]) == [0.4, 0.5, 0.6]

    def test_calls_deepseek_endpoint(self):
        fn = OpenAICompatibleEmbeddingFunction(
            api_key="sk-deep", model="deepseek-embedding",
            base_url=DEEPSEEK_BASE,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"embedding": [0.1, 0.2], "index": 0}]
        }

        with patch("requests.Session.post", return_value=mock_resp) as mock_post:
            fn(["text"])

        assert mock_post.call_args[0][0] == f"{DEEPSEEK_BASE}/embeddings"

    def test_raises_on_http_error(self):
        fn = OpenAICompatibleEmbeddingFunction(
            api_key="sk-test", model="embedding-2",
            base_url=ZHIPU_BASE,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = Exception("HTTP 401")

        with (patch("requests.Session.post", return_value=mock_resp) as mock_post,
              pytest.raises(Exception, match="HTTP 401")):
            fn(["text"])

    def test_single_text_input(self):
        fn = OpenAICompatibleEmbeddingFunction(
            api_key="sk-test", model="embedding-2",
            base_url=ZHIPU_BASE,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]
        }

        with patch("requests.Session.post", return_value=mock_resp) as mock_post:
            result = fn(["single text"])

        assert len(result) == 1
        assert list(result[0]) == [0.1, 0.2, 0.3]

    def test_empty_input_skips_http_call_and_propagates_wrapper_error(self):
        fn = OpenAICompatibleEmbeddingFunction(
            api_key="sk-test", model="embedding-2",
            base_url=ZHIPU_BASE,
        )
        with (patch("requests.Session.post") as mock_post,
              pytest.raises(ValueError, match="Expected Embed")):
            fn([])

        mock_post.assert_not_called()


# ── BuiltinEmbeddingFunction ───────────────────────────────────────────

class TestBuiltin:
    def test_delegates_to_chroma_default(self):
        mock_chroma_fn = MagicMock()
        mock_chroma_fn.return_value = [[0.1, 0.2, 0.3]]

        fn = BuiltinEmbeddingFunction(embedding_fn=mock_chroma_fn)
        result = fn(["hello"])

        mock_chroma_fn.assert_called_once_with(["hello"])
        assert list(result[0]) == [0.1, 0.2, 0.3]

    def test_default_constructor_creates_chroma_fn(self):
        fn = BuiltinEmbeddingFunction()
        # Should have created a DefaultEmbeddingFunction internally
        assert fn._fn is not None


# ── FallbackEmbeddingFunction ──────────────────────────────────────────

class TestFallback:
    def test_uses_primary_when_successful(self):
        primary = MagicMock(return_value=[[0.1, 0.2]])
        fallback = MagicMock()
        fn = FallbackEmbeddingFunction(primary, fallback)

        result = fn(["text"])

        primary.assert_called_once_with(["text"])
        fallback.assert_not_called()
        assert list(result[0]) == [0.1, 0.2]

    def test_falls_back_when_primary_fails(self):
        primary = MagicMock(side_effect=ConnectionError("API down"))
        fallback = MagicMock(return_value=[[0.3, 0.4]])
        fn = FallbackEmbeddingFunction(primary, fallback)

        result = fn(["text"])

        primary.assert_called_once_with(["text"])
        fallback.assert_called_once_with(["text"])
        assert list(result[0]) == [0.3, 0.4]

    def test_propagates_when_both_fail(self):
        primary = MagicMock(side_effect=ConnectionError("API down"))
        fallback = MagicMock(side_effect=RuntimeError("model crash"))
        fn = FallbackEmbeddingFunction(primary, fallback)

        with pytest.raises(RuntimeError, match="model crash"):
            fn(["text"])

    def test_primary_http_error_triggers_fallback(self):
        primary = MagicMock(side_effect=RuntimeError("HTTP 500"))
        fallback = MagicMock(return_value=[[0.5]])
        fn = FallbackEmbeddingFunction(primary, fallback)

        result = fn(["text"])

        assert list(result[0]) == [0.5]

    def test_fallback_passed_same_input(self):
        primary = MagicMock(side_effect=ValueError("bad"))
        fallback = MagicMock(return_value=[[0.1]])
        fn = FallbackEmbeddingFunction(primary, fallback)

        fn(["doc a", "doc b"])

        fallback.assert_called_once_with(["doc a", "doc b"])

    def test_logs_warning_on_fallback(self, caplog):
        caplog.set_level(logging.WARNING)
        primary = MagicMock(side_effect=ConnectionError("API timeout"))
        fallback = MagicMock(return_value=[[0.1]])
        fn = FallbackEmbeddingFunction(primary, fallback)

        fn(["text"])

        assert len(caplog.records) >= 1
        assert "Primary embedding failed" in caplog.text
        assert "API timeout" in caplog.text
