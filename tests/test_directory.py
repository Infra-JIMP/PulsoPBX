import tempfile
import unittest
from pathlib import Path

from directory import DirectoryStore, split_mikopbx_identity


class DirectoryIdentityTests(unittest.TestCase):
    def test_splits_sector_before_or_after_name(self):
        self.assertEqual(split_mikopbx_identity("Financeiro - Thayse"), ("Thayse", "Financeiro"))
        self.assertEqual(split_mikopbx_identity("Eduardo - TI"), ("Eduardo", "T.I."))
        self.assertEqual(split_mikopbx_identity("Scurra - José"), ("Scurra - José", ""))


class DirectoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = DirectoryStore(Path(self.directory.name) / "pulsopbx.db")
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_initializes_sectors_and_quick_dials(self):
        self.assertGreaterEqual(len(self.store.list_sectors()), 15)
        self.assertEqual(
            [item["code"] for item in self.store.list_quick_dials()],
            ["*3", "*8"],
        )

    def test_imports_legacy_overrides_once_and_preserves_them_from_mikopbx(self):
        overrides = {
            "1001": {
                "nome": "Thayse",
                "setor": "Financeiro",
                "email": "thayse@example.com",
                "notificar": True,
            }
        }
        self.assertEqual(self.store.import_legacy_overrides(overrides), 1)
        self.assertEqual(self.store.import_legacy_overrides(overrides), 0)

        self.store.synchronize_mikopbx(
            {"1001": {"nome": "Outro Nome - Comercial", "email": "miko@example.com"}}
        )
        person = self.store.list_people(False)[0]
        self.assertEqual(person["name"], "Thayse")
        self.assertEqual(person["sector"], "Financeiro")
        self.assertEqual(person["email"], "thayse@example.com")

    def test_synchronizes_mikopbx_and_exposes_profile_overrides(self):
        changed = self.store.synchronize_mikopbx(
            {
                "1001": {
                    "nome": "Financeiro - Thayse",
                    "email": "financeiro@example.com",
                },
                "8008": {
                    "nome": "Eduardo - TI",
                    "email": "assistente@example.com",
                },
            }
        )
        self.assertEqual(changed, 2)
        profiles = self.store.profile_overrides()
        self.assertEqual(profiles["1001"]["nome"], "Thayse")
        self.assertEqual(profiles["1001"]["setor"], "Financeiro")
        self.assertEqual(profiles["8008"]["setor"], "T.I.")

    def test_manual_update_wins_over_later_mikopbx_sync(self):
        self.store.synchronize_mikopbx(
            {"8008": {"nome": "Eduardo - TI", "email": "old@example.com"}}
        )
        person = self.store.list_people(False)[0]
        updated = self.store.save_person(
            {
                "name": "Eduardo Porangaba",
                "role": "Assistente de TI Júnior",
                "sector": "T.I.",
                "extension": "8008",
                "email": "new@example.com",
                "active": True,
                "notify": True,
            },
            person["id"],
        )
        self.assertEqual(updated["role"], "Assistente de TI Júnior")
        self.store.synchronize_mikopbx(
            {"8008": {"nome": "Outro nome - TI", "email": "miko@example.com"}}
        )
        current = self.store.list_people(False)[0]
        self.assertEqual(current["name"], "Eduardo Porangaba")
        self.assertEqual(current["email"], "new@example.com")
        self.assertGreaterEqual(len(self.store.recent_changes()), 2)

    def test_archives_without_deleting_history(self):
        person = self.store.save_person(
            {
                "name": "Pessoa Teste",
                "role": "Analista",
                "sector": "T.I.",
                "extension": "9999",
                "email": "pessoa@example.com",
                "active": True,
                "notify": True,
            }
        )
        archived = self.store.save_person({**person, "active": False}, person["id"])
        self.assertFalse(archived["active"])
        self.assertEqual(self.store.list_people(False), [])
        self.assertEqual(len(self.store.list_people(True)), 1)
        self.assertEqual(self.store.recent_changes()[0]["action"], "archive")

        self.store.synchronize_mikopbx(
            {"9999": {"nome": "Pessoa Teste - TI", "email": "miko@example.com"}}
        )
        self.assertEqual(self.store.list_people(False), [])
        self.assertEqual(len(self.store.list_people(True)), 1)

    def test_changed_extension_is_not_recreated_from_stale_mikopbx_data(self):
        self.store.synchronize_mikopbx(
            {"1001": {"nome": "Thayse - Financeiro", "email": "old@example.com"}}
        )
        person = self.store.list_people(False)[0]
        self.store.save_person(
            {
                **person,
                "extension": "1002",
                "email": "new@example.com",
            },
            person["id"],
        )

        self.store.synchronize_mikopbx(
            {"1001": {"nome": "Thayse - Financeiro", "email": "old@example.com"}}
        )
        current = self.store.list_people(False)
        self.assertEqual([row["extension"] for row in current], ["1002"])

    def test_rejects_duplicate_active_extension(self):
        payload = {
            "name": "Primeira Pessoa",
            "sector": "RH",
            "extension": "1001",
            "email": "one@example.com",
            "active": True,
            "notify": True,
        }
        self.store.save_person(payload)
        with self.assertRaisesRegex(ValueError, "já está vinculado"):
            self.store.save_person(
                {**payload, "name": "Segunda Pessoa", "sector": "Setor inválido"}
            )
        self.assertNotIn("Setor inválido", [item["name"] for item in self.store.list_sectors()])


if __name__ == "__main__":
    unittest.main()
