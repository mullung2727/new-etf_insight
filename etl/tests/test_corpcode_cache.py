import os
import tempfile
import time
import unittest
from pathlib import Path

from scripts.build_financial_indicators import _corpcode_zip


class TestCorpcodeCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name) / "corpcode.zip"
        self.calls = 0

    def tearDown(self):
        self._tmp.cleanup()

    def _dl(self, key):
        self.calls += 1
        return b"ZIPBYTES"

    def test_miss_downloads_and_writes(self):
        data = _corpcode_zip("K", cache_path=self.cache, ttl_sec=3600, downloader=self._dl)
        self.assertEqual(data, b"ZIPBYTES")
        self.assertEqual(self.calls, 1)
        self.assertTrue(self.cache.exists())

    def test_fresh_cache_skips_download(self):
        self.cache.write_bytes(b"CACHED")
        data = _corpcode_zip("K", cache_path=self.cache, ttl_sec=3600, downloader=self._dl)
        self.assertEqual(data, b"CACHED")
        self.assertEqual(self.calls, 0)  # 다운로더 미호출

    def test_expired_cache_redownloads(self):
        self.cache.write_bytes(b"OLD")
        old = time.time() - 7200
        os.utime(self.cache, (old, old))
        data = _corpcode_zip("K", cache_path=self.cache, ttl_sec=3600, downloader=self._dl)
        self.assertEqual(data, b"ZIPBYTES")
        self.assertEqual(self.calls, 1)


if __name__ == "__main__":
    unittest.main()
