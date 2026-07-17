"""Geração das listas corporativas em Excel a partir do cadastro central."""

from __future__ import annotations

from collections import OrderedDict
from io import BytesIO

import xlsxwriter


BLUE = "#0070C0"
ORANGE = "#ED7D31"
WHITE = "#FFFFFF"
BLACK = "#000000"


def build_directory_workbook(
    kind: str,
    people: list[dict],
    quick_dials: list[dict] | None = None,
) -> bytes:
    active = [person for person in people if person.get("active")]
    grouped = _group_people(active)
    stream = BytesIO()
    workbook = xlsxwriter.Workbook(stream, {"in_memory": True})
    workbook.set_properties(
        {
            "title": "Lista corporativa atualizada",
            "subject": "Diretório de colaboradores da Joinville Implementos",
            "company": "Joinville Implementos",
            "comments": "Gerado automaticamente pelo PulsoPBX.",
        }
    )
    if kind == "extensions":
        _write_extensions_sheet(workbook, grouped, quick_dials or [])
    elif kind == "emails":
        _write_emails_sheet(workbook, grouped)
    else:
        workbook.close()
        raise ValueError("Tipo de lista inválido")
    workbook.close()
    return stream.getvalue()


def _group_people(people: list[dict]) -> list[tuple[str, str, list[dict]]]:
    ordered: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
    sorted_people = sorted(
        people,
        key=lambda person: (
            int(person.get("sector_sort") or 999999),
            int(person.get("sort_order") or 0),
            str(person.get("name") or "").casefold(),
        ),
    )
    for person in sorted_people:
        sector = str(person.get("sector") or "Sem setor")
        short = str(person.get("sector_short") or sector.upper())
        ordered.setdefault((sector, short), []).append(person)
    return [(sector, short, members) for (sector, short), members in ordered.items()]


def _base_format(workbook, *, blue: bool, border: int, align: str, font: str, size: int):
    return workbook.add_format(
        {
            "font_name": font,
            "font_size": size,
            "bold": True,
            "font_color": WHITE if blue else BLACK,
            "bg_color": BLUE if blue else WHITE,
            "border": border,
            "border_color": BLACK,
            "align": align,
            "valign": "vcenter",
        }
    )


def _write_extensions_sheet(workbook, grouped, quick_dials) -> None:
    sheet = workbook.add_worksheet("Ramais")
    sheet.hide_gridlines(2)
    sheet.set_portrait()
    sheet.set_paper(9)
    sheet.fit_to_pages(1, 0)
    sheet.set_margins(0.25, 0.25, 0.35, 0.35)
    sheet.set_column("A:A", 15)
    sheet.set_column("B:B", 25)
    sheet.set_column("C:C", 13)
    sheet.set_row(0, 22)
    header = workbook.add_format(
        {
            "font_name": "Aptos Narrow",
            "font_size": 11,
            "bold": True,
            "font_color": WHITE,
            "bg_color": BLUE,
            "border": 1,
            "align": "center",
            "valign": "vcenter",
        }
    )
    sheet.write_row(0, 0, ["SETOR", "USUÁRIO", "RAMAL"], header)
    row = 1
    for index, (_, short, members) in enumerate(grouped):
        blue = index % 2 == 1
        sector_format = _base_format(workbook, blue=blue, border=1, align="center", font="Aptos Narrow", size=11)
        name_format = _base_format(workbook, blue=blue, border=1, align="left", font="Aptos Narrow", size=11)
        extension_format = _base_format(workbook, blue=blue, border=1, align="center", font="Aptos Narrow", size=11)
        start = row
        for member in members:
            sheet.set_row(row, 20)
            sheet.write(row, 1, str(member.get("name") or "").upper(), name_format)
            sheet.write(row, 2, str(member.get("extension") or ""), extension_format)
            row += 1
        if len(members) == 1:
            sheet.write(start, 0, short.upper(), sector_format)
        else:
            sheet.merge_range(start, 0, row - 1, 0, short.upper(), sector_format)

    quick_format = workbook.add_format(
        {
            "font_name": "Aptos Narrow",
            "font_size": 11,
            "bold": True,
            "font_color": BLACK,
            "bg_color": ORANGE,
            "border": 1,
            "align": "center",
            "valign": "vcenter",
        }
    )
    for dial in quick_dials:
        sheet.set_row(row, 20)
        sheet.merge_range(row, 0, row, 1, str(dial.get("label") or "").upper(), quick_format)
        sheet.write(row, 2, str(dial.get("code") or ""), quick_format)
        row += 1
    sheet.autofilter(0, 0, max(0, row - 1), 2)
    sheet.freeze_panes(1, 0)
    sheet.print_area(0, 0, max(0, row - 1), 2)
    sheet.repeat_rows(0)


def _write_emails_sheet(workbook, grouped) -> None:
    sheet = workbook.add_worksheet("E-mail")
    sheet.hide_gridlines(2)
    sheet.set_landscape()
    sheet.set_paper(9)
    sheet.fit_to_pages(1, 0)
    sheet.set_margins(0.25, 0.25, 0.35, 0.35)
    sheet.set_column("A:A", 2.5)
    sheet.set_column("B:B", 17)
    sheet.set_column("C:C", 30)
    sheet.set_column("D:D", 58)
    sheet.set_row(0, 22)
    header = workbook.add_format(
        {
            "font_name": "Calibri",
            "font_size": 12,
            "bold": True,
            "font_color": BLACK,
            "bg_color": ORANGE,
            "border": 2,
            "align": "center",
            "valign": "vcenter",
        }
    )
    sheet.write_row(0, 1, ["SETOR", "USUÁRIO", "E-MAIL"], header)
    row = 1
    for index, (_, short, members) in enumerate(grouped):
        blue = index % 2 == 1
        sector_format = _base_format(workbook, blue=blue, border=2, align="center", font="Calibri", size=12)
        name_format = _base_format(workbook, blue=blue, border=2, align="left", font="Calibri", size=12)
        email_format = _base_format(workbook, blue=blue, border=2, align="left", font="Calibri", size=12)
        start = row
        for member in members:
            sheet.set_row(row, 22)
            sheet.write(row, 2, str(member.get("name") or ""), name_format)
            sheet.write(row, 3, str(member.get("email") or ""), email_format)
            row += 1
        if len(members) == 1:
            sheet.write(start, 1, short.upper(), sector_format)
        else:
            sheet.merge_range(start, 1, row - 1, 1, short.upper(), sector_format)
    sheet.autofilter(0, 1, max(0, row - 1), 3)
    sheet.freeze_panes(1, 1)
    sheet.print_area(0, 1, max(0, row - 1), 3)
    sheet.repeat_rows(0)
