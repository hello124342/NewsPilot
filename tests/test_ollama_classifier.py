"""Tests for the local Ollama intent classifier."""

from unittest.mock import MagicMock, patch

from app.intent.ollama_classifier import OllamaIntentClassifier


class TestOllamaIntentClassifier:
    @patch("app.intent.ollama_classifier.httpx.post")
    def test_parses_valid_json(self, mock_post):
        response = MagicMock()
        response.json.return_value = {
            "response": '{"intent":"qa","confidence":0.91}'
        }
        mock_post.return_value = response

        prediction = OllamaIntentClassifier().predict("这条新闻有什么影响")

        assert prediction.intent == "qa"
        assert prediction.confidence == 0.91
        mock_post.assert_called_once()

    @patch("app.intent.ollama_classifier.httpx.post")
    def test_low_confidence_becomes_unknown(self, mock_post):
        response = MagicMock()
        response.json.return_value = {
            "response": '{"intent":"list","confidence":0.42}'
        }
        mock_post.return_value = response

        prediction = OllamaIntentClassifier().predict("随便说点什么")

        assert prediction.intent == "unknown"
        assert prediction.source == "ollama_low_confidence"

    @patch("app.intent.ollama_classifier.httpx.post")
    def test_transport_error_becomes_unknown(self, mock_post):
        import httpx

        mock_post.side_effect = httpx.ConnectError("offline")

        prediction = OllamaIntentClassifier().predict("测试消息")

        assert prediction.intent == "unknown"
        assert prediction.source == "ollama_error"
