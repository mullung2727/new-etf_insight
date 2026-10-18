import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from new_etf_insight import data_paths


class DataRootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def _patch_no_env(self):
        """os.environ 에서 키를 제거한 patcher. REPO_ROOT 는 호출부가 따로 patch."""
        p = patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop(data_paths.ENV_KEY, None)
        return p

    def test_no_env_no_dotenv_uses_legacy_paths(self):
        self._patch_no_env()
        with patch.object(data_paths, "REPO_ROOT", self.tmp_path):
            self.assertIsNone(data_paths.data_root())
            self.assertEqual(data_paths.db_dir(), data_paths.ETL_DIR / "db")
            self.assertEqual(data_paths.exports_dir(), data_paths.ETL_DIR / "exports")
            self.assertEqual(data_paths.runs_dir(), data_paths.ETL_DIR / "runs")
            self.assertEqual(data_paths.orderbook_dir(), Path("D:/orderbook"))
            self.assertEqual(data_paths.sec_edgar_dir(), Path("D:/sec_edgar"))
            self.assertEqual(data_paths.ken_french_dir(), Path("D:/ken_french"))
            self.assertIsNone(data_paths.code_backup_dir())

    def test_dotenv_read_without_os_environ(self):
        data = self.tmp_path / "data"
        data.mkdir()
        (self.tmp_path / ".env").write_text(
            f"{data_paths.ENV_KEY}={data}\n", encoding="utf-8"
        )
        self._patch_no_env()
        with patch.object(data_paths, "REPO_ROOT", self.tmp_path):
            self.assertEqual(data_paths.db_dir(), data / "db")
            self.assertEqual(data_paths.exports_dir(), data / "exports")
            self.assertEqual(data_paths.runs_dir(), data / "runs")
            self.assertEqual(data_paths.orderbook_dir(), data / "orderbook")
            self.assertEqual(data_paths.sec_edgar_dir(), data / "sec_edgar")
            self.assertEqual(data_paths.ken_french_dir(), data / "ken_french")
            self.assertEqual(data_paths.code_backup_dir(), data / "code_backup")

    def test_dotenv_parsing_quotes_spaces_comments(self):
        env_file = self.tmp_path / ".env"
        env_file.write_text(
            "# 주석 줄은 무시\n"
            "OTHER_KEY=xxx\n"
            "\n"
            r'NEW_ETF_DATA_ROOT = "C:\x"' + "\n",
            encoding="utf-8",
        )
        self.assertEqual(data_paths._read_env_file(env_file), r"C:\x")

        env_file.write_text("NEW_ETF_DATA_ROOT='D:/data'\n", encoding="utf-8")
        self.assertEqual(data_paths._read_env_file(env_file), "D:/data")

        env_file.write_text("  NEW_ETF_DATA_ROOT=E:/d  \n", encoding="utf-8")
        self.assertEqual(data_paths._read_env_file(env_file), "E:/d")

        self.assertIsNone(data_paths._read_env_file(self.tmp_path / "no_such_file"))

    def test_os_environ_beats_dotenv(self):
        env_data = self.tmp_path / "env_data"
        env_data.mkdir()
        dotenv_data = self.tmp_path / "dotenv_data"
        dotenv_data.mkdir()
        (self.tmp_path / ".env").write_text(
            f"{data_paths.ENV_KEY}={dotenv_data}\n", encoding="utf-8"
        )
        with patch.dict(os.environ, {data_paths.ENV_KEY: str(env_data)}):
            with patch.object(data_paths, "REPO_ROOT", self.tmp_path):
                self.assertEqual(data_paths.data_root(), env_data)
                self.assertEqual(data_paths.db_dir(), env_data / "db")

    def test_missing_folder_raises_without_fallback(self):
        missing = self.tmp_path / "no_such_dir"
        with patch.dict(os.environ, {data_paths.ENV_KEY: str(missing)}):
            with patch.object(data_paths, "REPO_ROOT", self.tmp_path):
                with self.assertRaisesRegex(RuntimeError, "폴백하지 않음"):
                    data_paths.data_root()

    def test_missing_folder_in_dotenv_raises(self):
        missing = self.tmp_path / "no_such_dir"
        (self.tmp_path / ".env").write_text(
            f"{data_paths.ENV_KEY}={missing}\n", encoding="utf-8"
        )
        self._patch_no_env()
        with patch.object(data_paths, "REPO_ROOT", self.tmp_path):
            with self.assertRaisesRegex(RuntimeError, "폴백하지 않음"):
                data_paths.db_dir()


class HardcodedPathTest(unittest.TestCase):
    def test_no_hardcoded_data_dirs(self):
        etl_dir = Path(__file__).resolve().parents[1]
        targets = sorted((etl_dir / "scripts").rglob("*.py")) + sorted(
            (etl_dir / "src").rglob("*.py")
        )
        allowed = {"data_paths.py", "backup_db.py"}
        patterns = ('/ "db"', '/ "exports"', '/ "runs"', '"D:/')
        violations = []
        for path in targets:
            if path.name in allowed:
                continue
            in_docstring = False
            lines = path.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                if '"""' in stripped or "'''" in stripped:
                    if (stripped.count('"""') + stripped.count("'''")) % 2 == 1:
                        in_docstring = not in_docstring
                    continue
                if in_docstring:
                    continue
                if stripped.startswith("#"):
                    continue
                if "data-paths: ok" in line:
                    continue
                for pat in patterns:
                    if pat in line:
                        violations.append(f"{path}:{lineno}: {pat} :: {stripped}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
