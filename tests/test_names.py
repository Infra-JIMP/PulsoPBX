import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import names


class LocalResponsibleStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.names_file = Path(self.temporary.name) / "ramais_nomes.json"
        self.patch = patch.object(names, "NAMES_FILE", self.names_file)
        self.patch.start()
        names._cache.update({"mtime": None, "data": {}})

    def tearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    def test_saves_email_without_losing_existing_identity(self):
        self.names_file.write_text(
            json.dumps({"1001": {"nome": "Ana", "setor": "Financeiro"}}),
            encoding="utf-8",
        )

        saved = names.save_email_override("1001", " ANA@EXAMPLE.COM ", False)

        self.assertEqual(saved["email"], "ana@example.com")
        self.assertFalse(saved["notificar"])
        self.assertEqual(names.load_names()["1001"]["nome"], "Ana")
        self.assertEqual(names.load_names()["1001"]["setor"], "Financeiro")

    def test_updates_sector_without_losing_email_or_name(self):
        self.names_file.write_text(
            json.dumps(
                {
                    "1001": {
                        "nome": "Ana",
                        "setor": "Financeiro",
                        "email": "ana@example.com",
                    }
                }
            ),
            encoding="utf-8",
        )

        saved = names.save_email_override(
            "1001", "ana@example.com", True, "  Televendas  "
        )

        self.assertEqual(saved["nome"], "Ana")
        self.assertEqual(saved["email"], "ana@example.com")
        self.assertEqual(saved["setor"], "Televendas")

    def test_clear_email_preserves_name_and_returns_to_inheritance(self):
        names.save_email_override("1001", "ana@example.com", True)
        raw = json.loads(self.names_file.read_text(encoding="utf-8"))
        raw["1001"]["nome"] = "Ana"
        self.names_file.write_text(json.dumps(raw), encoding="utf-8")

        changed = names.clear_email_override("1001")

        self.assertTrue(changed)
        stored = json.loads(self.names_file.read_text(encoding="utf-8"))["1001"]
        self.assertEqual(stored, {"nome": "Ana"})


if __name__ == "__main__":
    unittest.main()
