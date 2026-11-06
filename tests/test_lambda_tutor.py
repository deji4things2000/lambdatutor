import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from lambda_tutor import App, Int, TypeChecker, choose_model, ensure_model_selection, evaluate, format_type, parse


class LambdaTutorTests(unittest.TestCase):
    def test_identity_reduces(self):
        trace = evaluate(parse(r"(\x. x) 4"))
        self.assertEqual(trace["result"], "4")
        self.assertEqual(len(trace["steps"]), 1)
        self.assertEqual(trace["steps"][0]["index"], 0)

    def test_nested_application(self):
        trace = evaluate(parse(r"(\x. \y. x) 4"))
        self.assertEqual(trace["result"], r"(λy. 4)")

    def test_typed_identity(self):
        inferred = TypeChecker().check(parse(r"\x:Int. x"))
        self.assertEqual(format_type(inferred), "Int -> Int")

    def test_unannotated_type_is_rejected(self):
        with self.assertRaises(Exception):
            TypeChecker().check(parse(r"\x. x"))

    def test_application_parser(self):
        term = parse(r"(\x. x) 2")
        self.assertIsInstance(term, App)
        self.assertIsInstance(term.argument, Int)

    @patch.dict(os.environ, {"DARTMOUTH_CHAT_API_KEY": "test-key"}, clear=False)
    @patch("lambda_tutor.fetch_models", return_value=[{"id": "model-a"}, {"id": "model-b"}])
    @patch("builtins.input", return_value="2")
    def test_interactive_model_selection(self, _input, _fetch_models):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DARTMOUTH_MODEL", None)
            self.assertEqual(choose_model(), "model-b")
            self.assertEqual(os.environ["DARTMOUTH_MODEL"], "model-b")

    @patch.dict(os.environ, {"DARTMOUTH_CHAT_API_KEY": "test-key", "DARTMOUTH_MODEL": "retired-model"}, clear=False)
    @patch("lambda_tutor.fetch_models", return_value=[{"id": "model-a"}, {"id": "model-b"}])
    @patch("lambda_tutor.choose_model", return_value="model-a")
    def test_stale_model_is_reselected(self, select_model, _fetch_models):
        ensure_model_selection()
        select_model.assert_called_once_with()

    def test_model_endpoint_is_at_api_root(self):
        endpoint = "https://chat.dartmouth.edu/api/chat/completions"
        parts = urlsplit(endpoint)
        api_root = parts.path[:parts.path.find("/api/") + len("/api/")]
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{api_root}models",
                         "https://chat.dartmouth.edu/api/models")


if __name__ == "__main__":
    unittest.main()
