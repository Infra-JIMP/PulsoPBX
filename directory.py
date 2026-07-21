"""Cadastro central de colaboradores, setores e ramais do PulsoPBX."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import unicodedata
from email.utils import parseaddr
from pathlib import Path


DEFAULT_SECTORS = (
    ("Diretoria", "DIRET.", 10),
    ("Recepção", "RECEPÇÃO", 20),
    ("Comercial", "COMERC.", 30),
    ("T.I.", "T.I.", 40),
    ("PCP", "PCP", 50),
    ("Engenharia", "ENG.", 60),
    ("Compras", "COMP.", 70),
    ("Fiscal", "FISCAL", 80),
    ("Financeiro", "FINAN.", 90),
    ("Qualidade", "QUALI.", 100),
    ("Processos", "PROCES.", 110),
    ("Oficina", "OFICINA", 120),
    ("RH", "RH", 130),
    ("Almoxarifado", "ALMOX.", 140),
    ("Segurança do Trabalho", "TEC. SEG.", 150),
    ("Entrega Técnica", "ENTREGA TEC.", 160),
    ("Controladoria", "CONTROL.", 170),
)

DEFAULT_QUICK_DIALS = (
    ("*3", "TRANSF. LIGAÇÃO", 10),
    ("*8", "PUXAR LIGAÇÃO", 20),
)


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFD", str(value or ""))
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()


SECTOR_ALIASES = {
    "diret": "Diretoria",
    "diretoria": "Diretoria",
    "recepcao": "Recepção",
    "comerc": "Comercial",
    "comercial": "Comercial",
    "t i": "T.I.",
    "ti": "T.I.",
    "pcp": "PCP",
    "eng": "Engenharia",
    "engenharia": "Engenharia",
    "comp": "Compras",
    "compras": "Compras",
    "fiscal": "Fiscal",
    "finan": "Financeiro",
    "financeiro": "Financeiro",
    "quali": "Qualidade",
    "qualidade": "Qualidade",
    "proces": "Processos",
    "processos": "Processos",
    "oficina": "Oficina",
    "rh": "RH",
    "almox": "Almoxarifado",
    "almoxarifado": "Almoxarifado",
    "tec seg": "Segurança do Trabalho",
    "seguranca do trabalho": "Segurança do Trabalho",
    "entrega tec": "Entrega Técnica",
    "entrega tecnica": "Entrega Técnica",
    "controler": "Controladoria",
    "controladoria": "Controladoria",
}


def split_mikopbx_identity(value: str) -> tuple[str, str]:
    """Separa os nomes no padrão ``Pessoa - Setor`` ou ``Setor - Pessoa``."""
    original = " ".join(str(value or "").split())
    parts = [part.strip() for part in re.split(r"\s+-\s+|\s*-\s+", original, maxsplit=1)]
    if len(parts) != 2 or not all(parts):
        return original, ""
    left_sector = SECTOR_ALIASES.get(_key(parts[0]))
    right_sector = SECTOR_ALIASES.get(_key(parts[1]))
    if left_sector and not right_sector:
        return parts[1], left_sector
    if right_sector and not left_sector:
        return parts[0], right_sector
    return original, ""


def _valid_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not email:
        return ""
    _, parsed = parseaddr(email)
    local, separator, domain = parsed.partition("@")
    if parsed != email or not separator or not local or "." not in domain:
        return ""
    return email


class DirectoryStore:
    """Persistência SQLite para o diretório corporativo e seu histórico."""

    def __init__(self, database_path: Path):
        self._database_path = Path(database_path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            connection = sqlite3.connect(self._database_path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS directory_sectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    short_name TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS directory_people (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT '',
                    sector_id INTEGER REFERENCES directory_sectors(id),
                    extension TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    notify INTEGER NOT NULL DEFAULT 1,
                    name_source TEXT NOT NULL DEFAULT 'manual',
                    email_source TEXT NOT NULL DEFAULT 'manual',
                    sector_source TEXT NOT NULL DEFAULT 'manual',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    archived_at REAL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS directory_people_active_extension
                ON directory_people(extension)
                WHERE extension <> '' AND active = 1;

                CREATE TABLE IF NOT EXISTS directory_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id INTEGER,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    changed_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS directory_quick_dials (
                    code TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS directory_suppressed_extensions (
                    extension TEXT PRIMARY KEY,
                    person_id INTEGER,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS directory_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            now = time.time()
            connection.executemany(
                """
                INSERT INTO directory_sectors(name, short_name, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                [(name, short, order, now, now) for name, short, order in DEFAULT_SECTORS],
            )
            connection.executemany(
                """
                INSERT INTO directory_quick_dials(code, label, sort_order, active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(code) DO NOTHING
                """,
                DEFAULT_QUICK_DIALS,
            )
            connection.commit()
            self._connection = connection

    def import_legacy_overrides(self, overrides: dict[str, dict]) -> int:
        """Migra uma única vez os cadastros do antigo ``ramais_nomes.json``."""
        with self._lock:
            connection = self._require_connection()
            marker = connection.execute(
                "SELECT 1 FROM directory_meta WHERE key = 'legacy_overrides_v1'"
            ).fetchone()
            if marker is not None:
                return 0
            imported = 0
            now = time.time()
            try:
                for extension, override in sorted(overrides.items()):
                    extension = str(extension).strip()
                    if not extension or not extension.isdigit() or not isinstance(override, dict):
                        continue
                    name = " ".join(str(override.get("nome") or "").split())
                    sector = " ".join(str(override.get("setor") or "").split())
                    email = _valid_email(override.get("email", ""))
                    notify = override.get("notificar", True) is not False
                    row = connection.execute(
                        "SELECT * FROM directory_people WHERE extension = ? ORDER BY active DESC, id DESC LIMIT 1",
                        (extension,),
                    ).fetchone()
                    if row is None:
                        sector_id = self._sector_id(connection, sector, now) if sector else None
                        cursor = connection.execute(
                            """
                            INSERT INTO directory_people(
                                name, role, sector_id, extension, email, active, notify,
                                name_source, email_source, sector_source, sort_order,
                                created_at, updated_at
                            ) VALUES (?, '', ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                name or f"Ramal {extension}",
                                sector_id,
                                extension,
                                email,
                                int(notify),
                                "manual" if name else "mikopbx",
                                "manual" if email else "mikopbx",
                                "manual" if sector else "mikopbx",
                                self._next_people_order(connection),
                                now,
                                now,
                            ),
                        )
                        person_id = int(cursor.lastrowid)
                        before = None
                    else:
                        person_id = int(row["id"])
                        before = self._person(connection, person_id)
                        updates: dict[str, object] = {"notify": int(notify), "updated_at": now}
                        if name and row["name_source"] != "manual":
                            updates.update(name=name, name_source="manual")
                        if email and row["email_source"] != "manual":
                            updates.update(email=email, email_source="manual")
                        if sector and row["sector_source"] != "manual":
                            updates.update(
                                sector_id=self._sector_id(connection, sector, now),
                                sector_source="manual",
                            )
                        assignments = ", ".join(f"{column} = ?" for column in updates)
                        connection.execute(
                            f"UPDATE directory_people SET {assignments} WHERE id = ?",
                            [*updates.values(), person_id],
                        )
                    after = self._person(connection, person_id)
                    self._record_change(
                        connection,
                        person_id,
                        "import_legacy",
                        "system",
                        before,
                        after,
                        now,
                    )
                    imported += 1
                connection.execute(
                    "INSERT INTO directory_meta(key, value) VALUES ('legacy_overrides_v1', ?)",
                    (str(now),),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return imported

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def synchronize_mikopbx(self, profiles: dict[str, dict]) -> int:
        """Cria ou atualiza somente os campos que continuam herdando do MikoPBX."""
        changed = 0
        with self._lock:
            connection = self._require_connection()
            now = time.time()
            for extension, profile in sorted(profiles.items()):
                extension = str(extension).strip()
                if not extension:
                    continue
                suppressed = connection.execute(
                    "SELECT 1 FROM directory_suppressed_extensions WHERE extension = ?",
                    (extension,),
                ).fetchone()
                if suppressed is not None:
                    continue
                name, inferred_sector = split_mikopbx_identity(profile.get("nome", ""))
                name = name or f"Ramal {extension}"
                email = _valid_email(profile.get("email", ""))
                row = connection.execute(
                    "SELECT * FROM directory_people WHERE extension = ? ORDER BY active DESC, id DESC LIMIT 1",
                    (extension,),
                ).fetchone()
                if row is None:
                    sector_id = self._sector_id(connection, inferred_sector, now) if inferred_sector else None
                    sort_order = self._next_people_order(connection)
                    cursor = connection.execute(
                        """
                        INSERT INTO directory_people(
                            name, role, sector_id, extension, email, active, notify,
                            name_source, email_source, sector_source, sort_order,
                            created_at, updated_at
                        ) VALUES (?, '', ?, ?, ?, 1, 1, 'mikopbx', 'mikopbx', ?, ?, ?, ?)
                        """,
                        (
                            name,
                            sector_id,
                            extension,
                            email,
                            "mikopbx" if inferred_sector else "manual",
                            sort_order,
                            now,
                            now,
                        ),
                    )
                    person_id = int(cursor.lastrowid)
                    self._record_change(connection, person_id, "import_mikopbx", "system", None, self._person(connection, person_id), now)
                    changed += 1
                    continue

                if not row["active"]:
                    continue

                updates: dict[str, object] = {}
                if row["name_source"] == "mikopbx" and row["name"] != name:
                    updates["name"] = name
                if row["email_source"] == "mikopbx" and row["email"] != email:
                    updates["email"] = email
                if inferred_sector and row["sector_source"] == "mikopbx":
                    inferred_sector_id = self._sector_id(connection, inferred_sector, now)
                    if row["sector_id"] != inferred_sector_id:
                        updates["sector_id"] = inferred_sector_id
                if updates:
                    before = self._person(connection, int(row["id"]))
                    updates["updated_at"] = now
                    assignments = ", ".join(f"{column} = ?" for column in updates)
                    connection.execute(
                        f"UPDATE directory_people SET {assignments} WHERE id = ?",
                        [*updates.values(), int(row["id"])],
                    )
                    after = self._person(connection, int(row["id"]))
                    self._record_change(connection, int(row["id"]), "sync_mikopbx", "system", before, after, now)
                    changed += 1
            connection.commit()
        return changed

    def list_people(self, include_inactive: bool = True) -> list[dict]:
        with self._lock:
            connection = self._require_connection()
            where = "" if include_inactive else "WHERE p.active = 1"
            rows = connection.execute(
                f"""
                SELECT p.*, s.name AS sector, s.short_name AS sector_short,
                       COALESCE(s.sort_order, 999999) AS sector_sort
                FROM directory_people p
                LEFT JOIN directory_sectors s ON s.id = p.sector_id
                {where}
                ORDER BY p.active DESC, sector_sort, p.sort_order, p.name COLLATE NOCASE
                """
            ).fetchall()
            return [self._serialize_person(row) for row in rows]

    def list_sectors(self) -> list[dict]:
        with self._lock:
            rows = self._require_connection().execute(
                "SELECT id, name, short_name, sort_order FROM directory_sectors ORDER BY sort_order, name"
            ).fetchall()
            return [dict(row) for row in rows]

    def list_quick_dials(self) -> list[dict]:
        with self._lock:
            rows = self._require_connection().execute(
                "SELECT code, label, sort_order FROM directory_quick_dials WHERE active = 1 ORDER BY sort_order"
            ).fetchall()
            return [dict(row) for row in rows]

    def profile_overrides(self) -> dict[str, dict]:
        """Retorna a camada corporativa que complementa o MikoPBX.

        Registros inativos tambem sao retornados para que o monitor e as
        notificacoes consigam suprimir o ramal, em vez de voltar a herdar o
        e-mail do MikoPBX. Quando houver um registro ativo e um historico
        inativo para o mesmo ramal, o ativo tem prioridade.
        """
        profiles: dict[str, dict] = {}
        for row in self.list_people(include_inactive=True):
            extension = str(row.get("extension") or "").strip()
            if not extension:
                continue
            current = profiles.get(extension)
            if current is not None and current["ativo"]:
                continue
            profiles[extension] = {
                "nome": row["name"],
                "cargo": row["role"],
                "setor": row["sector"],
                "email": row["email"],
                "notificar": row["notify"],
                "ativo": row["active"],
            }
        return profiles

    def suppressed_extensions(self) -> set[str]:
        """Ramais removidos ou desativados manualmente no diretorio."""
        with self._lock:
            rows = self._require_connection().execute(
                "SELECT extension FROM directory_suppressed_extensions"
            ).fetchall()
        return {str(row["extension"]) for row in rows if row["extension"]}

    def save_person(self, payload: dict, person_id: int | None = None, actor: str = "administrator") -> dict:
        name = " ".join(str(payload.get("name") or "").split())
        role = " ".join(str(payload.get("role") or "").split())
        sector = " ".join(str(payload.get("sector") or "").split())
        extension = str(payload.get("extension") or "").strip()
        email_input = str(payload.get("email") or "").strip().lower()
        email = _valid_email(email_input)
        active = payload.get("active", True)
        notify = payload.get("notify", True)
        if not name or len(name) > 120:
            raise ValueError("Informe um nome com até 120 caracteres")
        if len(role) > 120:
            raise ValueError("O cargo deve ter no máximo 120 caracteres")
        if len(sector) > 80:
            raise ValueError("O setor deve ter no máximo 80 caracteres")
        if extension and (not extension.isdigit() or len(extension) > 10):
            raise ValueError("Informe um ramal numérico válido")
        if email_input and not email:
            raise ValueError("Informe um e-mail válido")
        if not isinstance(active, bool) or not isinstance(notify, bool):
            raise ValueError("Status do cadastro inválido")

        with self._lock:
            connection = self._require_connection()
            now = time.time()
            try:
                sector_id = self._sector_id(connection, sector, now) if sector else None
                before = None
                action = "create"
                if person_id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO directory_people(
                            name, role, sector_id, extension, email, active, notify,
                            name_source, email_source, sector_source, sort_order,
                            created_at, updated_at, archived_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', 'manual', 'manual', ?, ?, ?, ?)
                        """,
                        (
                            name,
                            role,
                            sector_id,
                            extension,
                            email,
                            int(active),
                            int(notify),
                            self._next_people_order(connection),
                            now,
                            now,
                            None if active else now,
                        ),
                    )
                    person_id = int(cursor.lastrowid)
                else:
                    before = self._person(connection, person_id)
                    if before is None:
                        raise ValueError("Colaborador não encontrado")
                    connection.execute(
                        """
                        UPDATE directory_people
                        SET name = ?, role = ?, sector_id = ?, extension = ?, email = ?,
                            active = ?, notify = ?, name_source = 'manual',
                            email_source = 'manual', sector_source = 'manual',
                            updated_at = ?, archived_at = ?
                        WHERE id = ?
                        """,
                        (
                            name,
                            role,
                            sector_id,
                            extension,
                            email,
                            int(active),
                            int(notify),
                            now,
                            None if active else now,
                            person_id,
                        ),
                    )
                    action = "update" if active else "archive"
                previous_extension = str((before or {}).get("extension") or "")
                if previous_extension and (not active or previous_extension != extension):
                    connection.execute(
                        """
                        INSERT INTO directory_suppressed_extensions(extension, person_id, reason, created_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(extension) DO UPDATE SET
                            person_id = excluded.person_id,
                            reason = excluded.reason,
                            created_at = excluded.created_at
                        """,
                        (
                            previous_extension,
                            person_id,
                            "archived" if not active else "extension_changed",
                            now,
                        ),
                    )
                if extension:
                    if active:
                        connection.execute(
                            "DELETE FROM directory_suppressed_extensions WHERE extension = ?",
                            (extension,),
                        )
                    else:
                        connection.execute(
                            """
                            INSERT INTO directory_suppressed_extensions(extension, person_id, reason, created_at)
                            VALUES (?, ?, 'archived', ?)
                            ON CONFLICT(extension) DO UPDATE SET
                                person_id = excluded.person_id,
                                reason = excluded.reason,
                                created_at = excluded.created_at
                            """,
                            (extension, person_id, now),
                        )
                after = self._person(connection, person_id)
                self._record_change(connection, person_id, action, actor, before, after, now)
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ValueError("Este ramal já está vinculado a outro colaborador ativo") from exc
            except Exception:
                connection.rollback()
                raise
            connection.commit()
            return after

    def reset_email_to_mikopbx(self, person_id: int, profiles: dict[str, dict]) -> dict:
        with self._lock:
            connection = self._require_connection()
            before = self._person(connection, person_id)
            if before is None:
                raise ValueError("Colaborador não encontrado")
            extension = before.get("extension", "")
            email = _valid_email(profiles.get(extension, {}).get("email", ""))
            now = time.time()
            connection.execute(
                "UPDATE directory_people SET email = ?, email_source = 'mikopbx', updated_at = ? WHERE id = ?",
                (email, now, person_id),
            )
            after = self._person(connection, person_id)
            self._record_change(connection, person_id, "reset_email_mikopbx", "administrator", before, after, now)
            connection.commit()
            return after

    def recent_changes(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._require_connection().execute(
                """
                SELECT id, person_id, action, actor, before_json, after_json, changed_at
                FROM directory_changes ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["before"] = json.loads(item.pop("before_json")) if item["before_json"] else None
                item["after"] = json.loads(item.pop("after_json")) if item["after_json"] else None
                result.append(item)
            return result

    def _person(self, connection: sqlite3.Connection, person_id: int) -> dict | None:
        row = connection.execute(
            """
            SELECT p.*, s.name AS sector, s.short_name AS sector_short,
                   COALESCE(s.sort_order, 999999) AS sector_sort
            FROM directory_people p
            LEFT JOIN directory_sectors s ON s.id = p.sector_id
            WHERE p.id = ?
            """,
            (person_id,),
        ).fetchone()
        return self._serialize_person(row) if row is not None else None

    @staticmethod
    def _serialize_person(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["active"] = bool(data["active"])
        data["notify"] = bool(data["notify"])
        data["sector"] = data.get("sector") or ""
        data["sector_short"] = data.get("sector_short") or ""
        return data

    def _sector_id(self, connection: sqlite3.Connection, name: str, now: float) -> int:
        row = connection.execute(
            "SELECT id FROM directory_sectors WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
        short_name = name.upper() if len(name) <= 14 else name[:12].upper() + "."
        order = connection.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM directory_sectors"
        ).fetchone()[0]
        cursor = connection.execute(
            "INSERT INTO directory_sectors(name, short_name, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, short_name, order, now, now),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _next_people_order(connection: sqlite3.Connection) -> int:
        return int(
            connection.execute("SELECT COALESCE(MAX(sort_order), 0) + 10 FROM directory_people").fetchone()[0]
        )

    @staticmethod
    def _record_change(
        connection: sqlite3.Connection,
        person_id: int,
        action: str,
        actor: str,
        before: dict | None,
        after: dict | None,
        changed_at: float,
    ) -> None:
        connection.execute(
            """
            INSERT INTO directory_changes(person_id, action, actor, before_json, after_json, changed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                person_id,
                action,
                actor,
                json.dumps(before, ensure_ascii=False, sort_keys=True) if before is not None else None,
                json.dumps(after, ensure_ascii=False, sort_keys=True) if after is not None else None,
                changed_at,
            ),
        )

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("DirectoryStore não inicializado")
        return self._connection
