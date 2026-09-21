import io
import json
import socket
import ssl
import unittest
import urllib.error

from agentic_analytics.providers.mia import MiaClient, MiaError


def response(content="Merhaba", calls=None, finish="stop"):
    return {"id": "response-1", "model": "deployed-alias", "choices": [{"finish_reason": finish,
        "message": {"role": "assistant", "content": content, "tool_calls": calls or [],
                    "reasoning": "private model thoughts", "reasoning_content": "also private"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4}}


class MiaClientTests(unittest.TestCase):
    def test_connection_failures_keep_safe_diagnostics_and_retry_budget(self):
        failures = [(TimeoutError('secret-value'), 'timeout'),
                    (urllib.error.URLError(socket.gaierror('secret-value')), 'dns'),
                    (urllib.error.URLError(ssl.SSLError('secret-value')), 'tls'),
                    (ConnectionResetError('secret-value'), 'connection'),
                    (urllib.error.URLError('secret-value'), 'network')]
        for failure, expected in failures:
            with self.subTest(expected=expected):
                attempts, waits = [], []
                def transport(*_):
                    attempts.append(1)
                    raise failure
                client = MiaClient('secret-value', transport=transport, sleeper=waits.append)
                with self.assertRaises(MiaError) as raised:
                    client.chat([{'role': 'user', 'content': 'test'}])
                error = raised.exception
                self.assertEqual(error.code, 'PROVIDER_UNAVAILABLE')
                self.assertEqual(error.failure_kind, expected)
                self.assertEqual(error.attempts, 3)
                self.assertEqual(waits, [1, 2])
                self.assertGreaterEqual(error.elapsed_ms, 0)
                self.assertNotIn('secret-value', str(error) + repr(vars(error)))

    def test_native_tool_call_with_stop_and_null_content_is_preserved(self):
        calls = [{"id": "call-1", "type": "function", "function": {"name": "describe", "arguments": '{"metric_id":"x"}'}}]
        client = MiaClient("secret-value", transport=lambda *_: response(None, calls))
        result = client.chat([{"role": "user", "content": "incele"}])
        self.assertEqual(result["tool_calls"], calls)
        self.assertIsNone(result["content"])
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("secret-value", repr(client))

    def test_structured_json_options_are_forwarded_without_reasoning_output(self):
        sent = []
        client = MiaClient("test", transport=lambda endpoint, payload: sent.append(payload) or response('{"ok":true}'))
        schema = {"type": "json_schema", "json_schema": {"name": "answer", "schema": {"type": "object"}}}
        answer = client.chat([{"role": "user", "content": "test"}], response_format=schema)
        self.assertEqual(sent[0]["response_format"], schema)
        self.assertEqual(json.loads(answer["content"]), {"ok": True})

    def test_thinking_option_is_typed_flattened_and_omitted_by_default(self):
        sent = []
        client = MiaClient("test", transport=lambda endpoint, payload: sent.append(payload) or response())
        message = [{"role": "user", "content": "test"}]
        client.chat(message)
        self.assertNotIn("chat_template_kwargs", sent[-1])
        client.chat(message, enable_thinking=False)
        self.assertEqual(sent[-1]["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("extra_body", sent[-1])
        client.chat(message, enable_thinking=True)
        self.assertEqual(sent[-1]["chat_template_kwargs"], {"enable_thinking": True})
        for invalid in ("false", 0, 1, {}, []):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                client.chat(message, enable_thinking=invalid)
        self.assertEqual(len(sent), 3)

    def test_retry_is_bounded_and_auth_failures_do_not_retry_or_echo_body(self):
        attempts, waits = [], []
        def transport(*_):
            attempts.append(1)
            if len(attempts) < 3:
                raise urllib.error.HTTPError("https://example.invalid", 429, "limited", {"Retry-After": "99"}, io.BytesIO(b"secret-value"))
            return response()
        client = MiaClient("secret-value", transport=transport, sleeper=waits.append)
        self.assertEqual(client.chat([{"role": "user", "content": "x"}])["content"], "Merhaba")
        self.assertEqual(waits, [10, 10])
        attempts.clear()
        def denied(*_):
            attempts.append(1)
            raise urllib.error.HTTPError("https://example.invalid", 401, "secret-value", {}, io.BytesIO(b"secret-value"))
        client._transport = denied
        with self.assertRaises(MiaError) as raised:
            client.chat([{"role": "user", "content": "x"}])
        self.assertEqual(raised.exception.code, "PROVIDER_AUTH_ERROR")
        self.assertEqual(len(attempts), 1)
        self.assertNotIn("secret-value", str(raised.exception))

    def test_embedding_reorders_and_rejects_duplicate_indices(self):
        raw = {"data": [{"index": 1, "embedding": [0., 1.]}, {"index": 0, "embedding": [1., 0.]}]}
        client = MiaClient("test", transport=lambda *_: raw)
        self.assertEqual(client.embedding(["a", "b"])["vectors"], [[1., 0.], [0., 1.]])
        raw["data"][0]["index"] = 0
        with self.assertRaises(MiaError):
            client.embedding(["a", "b"])

    def test_ocr_uses_required_parameters_and_marks_truncation(self):
        sent = []
        client = MiaClient("test", transport=lambda endpoint, payload: sent.append(payload) or response("bad", finish="length"))
        result = client.ocr(b"\x89PNG\r\n\x1a\nexample")
        self.assertTrue(result["truncated"])
        self.assertTrue(result["requires_content_validation"])
        self.assertEqual(sent[0]["vllm_xargs"], {"ngram_size": 35, "window_size": 128})
        self.assertEqual(sent[0]["messages"][0]["content"][-1]["text"], "<image>\ndocument parsing")
        self.assertFalse(sent[0]["skip_special_tokens"])

    def test_missing_credentials_and_invalid_tool_ids_are_typed_failures(self):
        with self.assertRaises(MiaError) as raised:
            MiaClient("")
        self.assertEqual(raised.exception.code, "MISSING_API_KEY")
        calls = [{"function": {"name": "x", "arguments": "{}"}}]
        client = MiaClient("test", transport=lambda *_: response(None, calls))
        with self.assertRaises(MiaError):
            client.chat([{"role": "user", "content": "x"}])


if __name__ == "__main__":
    unittest.main()
