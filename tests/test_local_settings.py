import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.__main__ import load_local_settings


class LocalSettingsTests(unittest.TestCase):
    def test_reads_key_and_model_with_shell_precedence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / ".env"
            path.write_text(
                'OPENAI_API_KEY="dummy-key"\nOPENAI_MODEL=file-model\nOTHER=ignored\n',
                encoding="utf-8-sig",
            )
            load_local_settings(path)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "dummy-key")
            self.assertEqual(os.environ["OPENAI_MODEL"], "file-model")
            self.assertNotIn("OTHER", os.environ)

            os.environ["OPENAI_API_KEY"] = "shell-key"
            load_local_settings(path)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "shell-key")

    def test_missing_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            load_local_settings(Path(directory) / ".env")
