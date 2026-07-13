"""Historico persistente de indisponibilidades confirmadas dos ramais."""
import sqlite3
import threading
import time
from pathlib import Path


class IncidentStore:
    """Armazena incidentes localmente sem depender de banco ou servico externo."""

    def __init__(self, database_path: Path):
        self._database_path = database_path
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._connection = sqlite3.connect(self._database_path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    extension TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open', 'resolved')),
                    opened_at REAL NOT NULL,
                    resolved_at REAL,
                    duration_seconds REAL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_incidents_extension_status ON incidents(extension, status)"
            )
            self._connection.commit()

    def record_transition(self, extension: str, status: str, now: float | None = None) -> dict | None:
        """Abre um incidente ao cair e fecha o incidente aberto quando o ramal retorna."""
        if status not in {"online", "offline"}:
            raise ValueError(f"Status de ramal invalido: {status}")
        now = now if now is not None else time.time()
        connection = self._require_connection()

        with self._lock:
            open_row = connection.execute(
                "SELECT id, extension, status, opened_at, resolved_at, duration_seconds "
                "FROM incidents WHERE extension = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                (extension,),
            ).fetchone()
            if status == "offline":
                if open_row is not None:
                    return self._serialize(open_row, now)
                cursor = connection.execute(
                    "INSERT INTO incidents(extension, status, opened_at) VALUES (?, 'open', ?)",
                    (extension, now),
                )
                connection.commit()
                row = connection.execute(
                    "SELECT id, extension, status, opened_at, resolved_at, duration_seconds FROM incidents WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
                return self._serialize(row, now)

            if open_row is None:
                return None
            duration = max(0, now - open_row["opened_at"])
            connection.execute(
                "UPDATE incidents SET status = 'resolved', resolved_at = ?, duration_seconds = ? WHERE id = ?",
                (now, duration, open_row["id"]),
            )
            connection.commit()
            row = connection.execute(
                "SELECT id, extension, status, opened_at, resolved_at, duration_seconds FROM incidents WHERE id = ?",
                (open_row["id"],),
            ).fetchone()
            return self._serialize(row, now)

    def recent(self, limit: int = 12, now: float | None = None) -> list[dict]:
        """Devolve incidentes abertos primeiro e, depois, os resolvidos mais recentes."""
        now = now if now is not None else time.time()
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                """
                SELECT id, extension, status, opened_at, resolved_at, duration_seconds
                FROM incidents
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,
                         COALESCE(resolved_at, opened_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._serialize(row, now) for row in rows]

    def open_by_extension(self, now: float | None = None) -> dict[str, dict]:
        now = now if now is not None else time.time()
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                "SELECT id, extension, status, opened_at, resolved_at, duration_seconds "
                "FROM incidents WHERE status = 'open'"
            ).fetchall()
        return {row["extension"]: self._serialize(row, now) for row in rows}

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("IncidentStore nao foi inicializado")
        return self._connection

    @staticmethod
    def _serialize(row: sqlite3.Row, now: float) -> dict:
        duration = row["duration_seconds"]
        if duration is None:
            duration = max(0, now - row["opened_at"])
        return {
            "id": row["id"],
            "extension": row["extension"],
            "status": row["status"],
            "opened_at": row["opened_at"],
            "resolved_at": row["resolved_at"],
            "duration_seconds": duration,
        }
