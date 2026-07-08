"""generate_json 재시도 — 일시적 LLM 오류(429/5xx/타임아웃/스트림끊김)는 재시도,
영구 오류(401 등)는 즉시 전파. provider는 mock — 실제 API/스키마 안 탐."""
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from new_etf_insight.llm import generate_json

_SCHEMA = Path("dummy_schema.json")  # provider mock이라 실제로 안 읽힘


class GenerateJsonRetryTest(unittest.TestCase):
    def test_retries_transient_then_succeeds(self) -> None:
        sleeps: list[float] = []
        with patch("new_etf_insight.llm.get_provider") as get_provider_mock:
            provider = get_provider_mock.return_value
            provider.generate_json.side_effect = [
                RuntimeError("ChatGPT OAuth responses call failed (503): overloaded"),
                requests.exceptions.ReadTimeout("stream drop"),
                '{"ok": true}',
            ]
            result = generate_json("prompt", output_schema_path=_SCHEMA, _sleep=sleeps.append)
        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(provider.generate_json.call_count, 3)  # 총 3회 시도
        self.assertEqual(sleeps, [3.0, 6.0])  # 선형 백오프

    def test_does_not_retry_permanent(self) -> None:
        sleeps: list[float] = []
        with patch("new_etf_insight.llm.get_provider") as get_provider_mock:
            provider = get_provider_mock.return_value
            provider.generate_json.side_effect = RuntimeError(
                "ChatGPT OAuth responses call failed (401): unauthorized"
            )
            with self.assertRaises(RuntimeError):
                generate_json("prompt", output_schema_path=_SCHEMA, _sleep=sleeps.append)
        self.assertEqual(provider.generate_json.call_count, 1)  # 재시도 없음
        self.assertEqual(sleeps, [])

    def test_exhausts_retries_then_raises(self) -> None:
        sleeps: list[float] = []
        with patch("new_etf_insight.llm.get_provider") as get_provider_mock:
            provider = get_provider_mock.return_value
            provider.generate_json.side_effect = requests.exceptions.ConnectionError("down")
            with self.assertRaises(requests.exceptions.ConnectionError):
                generate_json("prompt", output_schema_path=_SCHEMA, _sleep=sleeps.append)
        self.assertEqual(provider.generate_json.call_count, 3)  # 총 3회 후 포기
        self.assertEqual(sleeps, [3.0, 6.0])


if __name__ == "__main__":
    unittest.main()
