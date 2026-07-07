import unittest

from new_etf_insight.dart_client import fetch_dart_list


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


class TestFetchDartList(unittest.TestCase):
    def test_injects_key_and_returns_list_on_000(self):
        sess = _FakeSession({"status": "000", "list": [{"a": 1}]})
        rows = fetch_dart_list("http://x/api.json", {"corp_code": "007"}, "KEY", session=sess)
        self.assertEqual(rows, [{"a": 1}])
        # crtfc_key 주입 + 원본 params 병합
        self.assertEqual(sess.last_params, {"crtfc_key": "KEY", "corp_code": "007"})

    def test_non_000_returns_empty(self):
        # 013(무자료)·기타 에러코드는 빈 리스트
        sess = _FakeSession({"status": "013", "message": "no data"})
        self.assertEqual(fetch_dart_list("http://x", {}, "K", session=sess), [])

    def test_000_missing_list_returns_empty(self):
        sess = _FakeSession({"status": "000"})
        self.assertEqual(fetch_dart_list("http://x", {}, "K", session=sess), [])


if __name__ == "__main__":
    unittest.main()
