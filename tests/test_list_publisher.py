import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from directory import DirectoryStore
from list_publisher import LIST_FILES, ListPublisher
from web import create_app


def _sheet_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("xl/sharedStrings.xml").decode("utf-8")


class FakeDirectory:
    def __init__(self):
        self.people = [
            {
                "name": "Julio",
                "email": "julio@example.com",
                "extension": "3001",
                "sector": "Diretoria",
                "sector_short": "DIRET.",
                "sector_sort": 10,
                "sort_order": 10,
                "active": True,
            }
        ]
        self.quick_dials = [{"code": "*3", "label": "Estacionar"}]
        self.reads = 0

    def list_people(self, include_inactive=True):
        self.reads += 1
        return [p for p in self.people if include_inactive or p["active"]]

    def list_quick_dials(self):
        return list(self.quick_dials)


class ListPublisherTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output = Path(self._tmp.name) / "listas"
        self.directory = FakeDirectory()
        self.publisher = ListPublisher(self.directory, self.output)

    def _mtimes(self):
        return {n: (self.output / n).stat().st_mtime_ns for n in LIST_FILES.values()}

    def test_first_run_writes_both_lists(self):
        self.assertTrue(self.publisher.publish_if_changed())
        for filename in LIST_FILES.values():
            content = (self.output / filename).read_bytes()
            self.assertTrue(content.startswith(b"PK"), filename)
        self.assertEqual([p.suffix for p in self.output.iterdir()], [".xlsx", ".xlsx"])

    def test_unchanged_directory_does_not_rewrite_files(self):
        self.publisher.publish_if_changed()
        before = self._mtimes()
        self.assertFalse(self.publisher.publish_if_changed())
        self.assertEqual(self._mtimes(), before)

    def test_changed_directory_rewrites_files(self):
        self.publisher.publish_if_changed()
        self.directory.people[0]["email"] = "novo@example.com"
        self.assertTrue(self.publisher.publish_if_changed())

    def test_inactive_people_do_not_trigger_rewrite(self):
        self.publisher.publish_if_changed()
        self.directory.people.append({**self.directory.people[0], "name": "Ex", "active": False})
        self.assertFalse(self.publisher.publish_if_changed())

    def test_missing_file_is_recreated_even_without_changes(self):
        self.publisher.publish_if_changed()
        (self.output / LIST_FILES["emails"]).unlink()
        self.assertTrue(self.publisher.publish_if_changed())
        self.assertTrue((self.output / LIST_FILES["emails"]).is_file())


    def test_publisher_has_no_background_check(self):
        self.assertFalse(hasattr(self.publisher, "run"))


class _FakeTracker:
    def snapshot(self, now=None):
        return []

    def retain_extensions(self, allowed):
        return []


class DirectoryChangesRegenerateListsTests(unittest.IsolatedAsyncioTestCase):
    """Contratacao, troca de setor, troca de dominio e desligamento pela tela."""

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.directory = DirectoryStore(root / "pulsopbx.db")
        self.directory.initialize()
        self.output = root / "listas"
        self.publisher = ListPublisher(self.directory, self.output)
        config = SimpleNamespace(
            dashboard_auth_enabled=False,
            demo_mode=False,
            responsibles_admin_enabled=True,
            responsibles_admin_password="senha-administrativa-segura",
        )
        app = create_app(
            _FakeTracker(), None, config,
            directory=self.directory, list_publisher=self.publisher,
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.headers = {
            "X-PulsoPBX-Admin": "senha-administrativa-segura",
            "X-PulsoPBX-Action": "manage-responsibles",
        }

    async def asyncTearDown(self):
        await self.client.close()
        self.directory.close()
        self.temporary.cleanup()

    def _emails(self) -> str:
        return _sheet_text(self.output / LIST_FILES["emails"])

    def _extensions(self) -> str:
        return _sheet_text(self.output / LIST_FILES["extensions"])

    async def test_full_employee_lifecycle_keeps_lists_current(self):
        # Contratacao: as listas nascem com a pessoa e o e-mail dela.
        response = await self.client.post(
            "/api/admin/directory",
            json={
                "name": "Marina Souza", "role": "Compradora", "sector": "Compras",
                "extension": "8010", "email": "marina@antigo.com.br",
                "active": True, "notify": True,
            },
            headers=self.headers,
        )
        created = await response.json()
        self.assertEqual(response.status, 201)
        self.assertTrue(created["lists_updated"])
        self.assertIn("marina@antigo.com.br", self._emails())
        self.assertIn("8010", self._extensions())
        person = created["person"]

        # Troca de setor/cargo: a pessoa muda de grupo na lista.
        response = await self.client.put(
            f"/api/admin/directory/{person['id']}",
            json={**person, "sector": "Financeiro", "role": "Analista"},
            headers=self.headers,
        )
        person = (await response.json())["person"]
        # A lista usa a sigla cadastrada do setor ("FINAN.").
        self.assertIn("FINAN.", self._extensions())

        # Troca de dominio: a lista de e-mails acompanha o endereco novo.
        response = await self.client.put(
            f"/api/admin/directory/{person['id']}",
            json={**person, "email": "marina@novo.com.br"},
            headers=self.headers,
        )
        person = (await response.json())["person"]
        self.assertIn("marina@novo.com.br", self._emails())
        self.assertNotIn("marina@antigo.com.br", self._emails())

        # Mudanca que nao aparece na lista nao regrava os arquivos.
        response = await self.client.put(
            f"/api/admin/directory/{person['id']}",
            json={**person, "notify": False},
            headers=self.headers,
        )
        payload = await response.json()
        self.assertFalse(payload["lists_updated"])
        person = payload["person"]

        # Desligamento: some das duas listas.
        response = await self.client.put(
            f"/api/admin/directory/{person['id']}",
            json={**person, "active": False},
            headers=self.headers,
        )
        self.assertTrue((await response.json())["lists_updated"])
        self.assertNotIn("marina@novo.com.br", self._emails())
        self.assertNotIn("8010", self._extensions())


if __name__ == "__main__":
    unittest.main()
