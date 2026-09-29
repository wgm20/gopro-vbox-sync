"""Keep export reports made by tests out of the real user's app data."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch


class ExportTestCase(TestCase):
    def run(self, result=None):
        with TemporaryDirectory() as folder:
            with patch("goprovbox.reports.data_directory", return_value=Path(folder)):
                return super().run(result)
