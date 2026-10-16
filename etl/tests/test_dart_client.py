import unittest

from new_etf_insight.dart_client import DartAPIError, fetch_dart_list


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.last_url = None
        self.last_params = None

    def get(self, url, params=None, timeout=None):
        self.last_url = url
        self.last_params = params
        return _FakeResp(self._payload)


class _BoomSession:
    def __init__(self, exc):
        self._exc = exc

    def get(self, url, params=None, timeout=None):
        raise self._exc


class TestFetchDartList(unittest.TestCase):
    def test_injects_key_and_returns_list_on_000(self):
        sess = _FakeSession({"status": "000", "list": [{"a": 1}]})
        rows = fetch_dart_list("http://x/api.json", {"corp_code": "007"}, "KEY", session=sess)
        self.assertEqual(rows, [{"a": 1}])
        # crtfc_key 주입 + 원본 params 병합
        self.assertEqual(sess.last_params, {"crtfc_key": "KEY", "corp_code": "007"})

    def test_013_returns_empty(self):
        # 013(무자료)만 빈 리스트
        sess = _FakeSession({"status": "013", "message": "no data"})
        self.assertEqual(fetch_dart_list("http://x", {}, "K", session=sess), [])

    def test_non_013_non_000_raises(self):
        for status in ("014", "020", "010", "011", "012", "901", "800", "900"):
            with self.subTest(status=status):
                sess = _FakeSession({"status": status, "message": "boom"})
                with self.assertRaises(DartAPIError) as cm:
                    fetch_dart_list("http://x", {}, "K", session=sess)
                self.assertEqual(cm.exception.status, status)

    def test_000_missing_list_raises(self):
        # 000인데 list 누락·비-리스트는 malformed → 빈 데이터로 위장 금지
        for payload in ({"status": "000"}, {"status": "000", "list": None},
                        {"status": "000", "list": "x"}):
            with self.subTest(payload=payload):
                with self.assertRaises(DartAPIError):
                    fetch_dart_list("http://x", {}, "K", session=_FakeSession(payload))

    def test_000_empty_list_ok(self):
        sess = _FakeSession({"status": "000", "list": []})
        self.assertEqual(fetch_dart_list("http://x", {}, "K", session=sess), [])

    def test_error_message_sanitizes_key(self):
        sess = _FakeSession({"status": "020", "message": "bad KEY-SECRET-123 here"})
        with self.assertRaises(DartAPIError) as cm:
            fetch_dart_list("http://x", {}, "KEY-SECRET-123", session=sess)
        self.assertNotIn("KEY-SECRET-123", str(cm.exception))

    def test_http_error_sanitizes_url_and_key(self):
        boom = RuntimeError("500 https://opendart.fss.or.kr/api/x.json?crtfc_key=KEY-SECRET-123")
        with self.assertRaises(DartAPIError) as cm:
            fetch_dart_list("http://x", {}, "KEY-SECRET-123", session=_BoomSession(boom))
        self.assertEqual(cm.exception.status, "http_error")
        self.assertNotIn("KEY-SECRET-123", str(cm.exception))
        self.assertNotIn("https://", str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)  # from None


if __name__ == "__main__":
    unittest.main()
