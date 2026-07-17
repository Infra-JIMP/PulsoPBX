import io
import unittest
import zipfile

from directory_exports import build_directory_workbook


PEOPLE = [
    {
        "id": 1,
        "name": "Julio",
        "role": "Diretor",
        "sector": "Diretoria",
        "sector_short": "DIRET.",
        "sector_sort": 10,
        "sort_order": 10,
        "extension": "3001",
        "email": "julio@example.com",
        "active": True,
    },
    {
        "id": 2,
        "name": "Paulo",
        "role": "Diretor",
        "sector": "Diretoria",
        "sector_short": "DIRET.",
        "sector_sort": 10,
        "sort_order": 20,
        "extension": "5003",
        "email": "paulo@example.com",
        "active": True,
    },
    {
        "id": 3,
        "name": "Rafaela",
        "role": "Recepcionista",
        "sector": "Recepção",
        "sector_short": "RECEPÇÃO",
        "sector_sort": 20,
        "sort_order": 30,
        "extension": "3006",
        "email": "televendas@example.com",
        "active": True,
    },
]


class DirectoryExportTests(unittest.TestCase):
    def test_extensions_workbook_contains_identity_colors_and_merges(self):
        content = build_directory_workbook(
            "extensions",
            PEOPLE,
            [{"code": "*3", "label": "TRANSF. LIGAÇÃO", "sort_order": 10}],
        )
        self.assertTrue(content.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            styles = archive.read("xl/styles.xml").decode("utf-8")
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            shared = archive.read("xl/sharedStrings.xml").decode("utf-8")
        self.assertIn("FF0070C0", styles)
        self.assertIn("FFED7D31", styles)
        self.assertIn('mergeCell ref="A2:A3"', sheet)
        self.assertIn("TRANSF. LIGAÇÃO", shared)

    def test_email_workbook_preserves_email_layout(self):
        content = build_directory_workbook("emails", PEOPLE)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            shared = archive.read("xl/sharedStrings.xml").decode("utf-8")
        self.assertIn('name="E-mail"', workbook)
        self.assertIn('mergeCell ref="B2:B3"', sheet)
        self.assertIn("televendas@example.com", shared)

    def test_rejects_unknown_list_type(self):
        with self.assertRaisesRegex(ValueError, "Tipo de lista inválido"):
            build_directory_workbook("unknown", PEOPLE)


if __name__ == "__main__":
    unittest.main()
