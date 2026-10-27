"""Regression checks for provider model selection and review fallback."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import reviewer


class ProviderError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class ReviewerModelTests(unittest.TestCase):
    def test_provider_defaults_use_current_models(self):
        self.assertEqual(reviewer.PROVIDERS["groq"].default_model, "openai/gpt-oss-120b")
        self.assertEqual(reviewer.PROVIDERS["gemini"].default_model, "gemini-3.6-flash")

    def test_retired_groq_model_retries_supported_model(self):
        attempted = []

        def chat(_client, model, _system, _user, _json_mode, _provider_key):
            attempted.append(model)
            if model == "retired-model":
                raise ProviderError(404, "The model retired-model does not exist")
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"overall_score": 7, "summary": "Reviewed."}')
            )])

        with patch.dict(os.environ, {"GROQ_API_KEY": "test", "LLM_MODEL": "retired-model"},
                        clear=True), patch.object(reviewer, "_make_client", return_value=object()), \
                patch.object(reviewer, "_chat", side_effect=chat):
            result = reviewer.review_code("print('hello')", "Python", "General")

        self.assertEqual(attempted, ["retired-model", "openai/gpt-oss-120b"])
        self.assertEqual(result.model, "openai/gpt-oss-120b")
        self.assertEqual(result.data["overall_score"], 7.0)

    def test_quota_error_does_not_try_another_model(self):
        error = ProviderError(429, "Rate limit exceeded")
        with patch.dict(os.environ, {"GROQ_API_KEY": "test"}, clear=True), \
                patch.object(reviewer, "_make_client", return_value=object()), \
                patch.object(reviewer, "_chat", side_effect=error) as chat:
            with self.assertRaises(reviewer.ReviewError):
                reviewer.review_code("print('hello')", "Python", "General")
        self.assertEqual(chat.call_count, 1)

    def test_groq_gpt_oss_hides_reasoning_in_json_mode(self):
        create = Mock()
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        reviewer._chat(client, "openai/gpt-oss-120b", "system", "user", True, "groq")
        self.assertEqual(create.call_args.kwargs["reasoning_format"], "hidden")
        self.assertEqual(create.call_args.kwargs["max_completion_tokens"], 4096)
        self.assertEqual(create.call_args.kwargs["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
