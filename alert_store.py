"""Persistencia local do historico de entregas de alertas."""
import json
import sqlite3
import threading
import time
from pathlib import Path


class AlertStore:
    """Armazena eventos e uma entrega independente por destinatario."""

    def __init__(self, database_path: Path):
        self._database_path = database_path
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._connection = sqlite3.connect(self._database_path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS alert_events (
                    id TEXT PRIMARY KEY,
                    extension TEXT NOT NULL,
                    change TEXT NOT NULL CHECK(change IN ('online', 'offline', 'test')),
                    kind TEXT NOT NULL CHECK(kind IN ('status', 'test')),
                    context_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alert_deliveries (
                    event_id TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(event_id, recipient),
                    FOREIGN KEY(event_id) REFERENCES alert_events(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_alert_events_created
                    ON alert_events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_alert_events_extension
                    ON alert_events(extension, kind, created_at DESC);
                """
            )
            event_columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(alert_events)")
            }
            if "context_json" not in event_columns:
                self._connection.execute(
                    "ALTER TABLE alert_events ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}'"
                )
            self._connection.commit()

    def create_event(self, event: dict) -> None:
        connection = self._require_connection()
        with self._lock:
            connection.execute(
                "INSERT INTO alert_events"
                "(id, extension, change, kind, context_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event["id"],
                    event["extension"],
                    event["change"],
                    event["kind"],
                    json.dumps(event.get("context") or {}, ensure_ascii=False),
                    event["created_at"],
                    event["updated_at"],
                ),
            )
            connection.executemany(
                "INSERT INTO alert_deliveries(event_id, recipient, status, attempts, last_error, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        event["id"],
                        recipient,
                        delivery["status"],
                        delivery["attempts"],
                        delivery["last_error"],
                        event["updated_at"],
                    )
                    for recipient, delivery in event["deliveries"].items()
                ],
            )
            connection.commit()

    def update_delivery(self, event: dict, recipient: str) -> None:
        connection = self._require_connection()
        delivery = event["deliveries"][recipient]
        with self._lock:
            connection.execute(
                "UPDATE alert_events SET updated_at = ? WHERE id = ?",
                (event["updated_at"], event["id"]),
            )
            connection.execute(
                "UPDATE alert_deliveries SET status = ?, attempts = ?, last_error = ?, updated_at = ? "
                "WHERE event_id = ? AND recipient = ?",
                (
                    delivery["status"],
                    delivery["attempts"],
                    delivery["last_error"],
                    event["updated_at"],
                    event["id"],
                    recipient,
                ),
            )
            connection.commit()

    def recent(self, limit: int = 200) -> list[dict]:
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                "SELECT id, extension, change, kind, context_json, created_at, updated_at "
                "FROM alert_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return self._hydrate(connection, rows)

    def latest_status_by_extension(self) -> dict[str, dict]:
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                """
                SELECT id, extension, change, kind, context_json, created_at, updated_at
                FROM alert_events AS current
                WHERE kind = 'status'
                  AND id = (
                    SELECT id FROM alert_events
                    WHERE extension = current.extension AND kind = 'status'
                    ORDER BY created_at DESC, rowid DESC LIMIT 1
                  )
                """
            ).fetchall()
            events = self._hydrate(connection, rows)
        return {event["extension"]: event for event in events}

    def get(self, event_id: str) -> dict | None:
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                "SELECT id, extension, change, kind, context_json, created_at, updated_at "
                "FROM alert_events WHERE id = ?",
                (event_id,),
            ).fetchall()
            events = self._hydrate(connection, rows)
        return events[0] if events else None

    def fail_pending_for_removed_recipients(self, active_recipients: set[str]) -> int:
        """Encerra entregas pendentes de integrações que saíram da configuração."""
        connection = self._require_connection()
        now = time.time()
        active = sorted(active_recipients)
        if active:
            placeholders = ",".join("?" for _ in active)
            predicate = f"recipient NOT IN ({placeholders})"
            parameters: tuple = tuple(active)
        else:
            predicate = "1 = 1"
            parameters = ()
        with self._lock:
            event_rows = connection.execute(
                "SELECT DISTINCT event_id FROM alert_deliveries "
                f"WHERE status NOT IN ('sent', 'failed') AND {predicate}",
                parameters,
            ).fetchall()
            event_ids = [row["event_id"] for row in event_rows]
            if not event_ids:
                return 0
            result = connection.execute(
                "UPDATE alert_deliveries SET status = 'failed', "
                "last_error = 'Destinatario removido da configuracao', updated_at = ? "
                f"WHERE status NOT IN ('sent', 'failed') AND {predicate}",
                (now, *parameters),
            )
            event_placeholders = ",".join("?" for _ in event_ids)
            connection.execute(
                f"UPDATE alert_events SET updated_at = ? WHERE id IN ({event_placeholders})",
                (now, *event_ids),
            )
            connection.commit()
            return result.rowcount

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _hydrate(self, connection: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
        if not rows:
            return []
        events = {
            row["id"]: {
                "id": row["id"],
                "extension": row["extension"],
                "change": row["change"],
                "kind": row["kind"],
                "context": self._decode_context(row["context_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "deliveries": {},
            }
            for row in rows
        }
        placeholders = ",".join("?" for _ in events)
        deliveries = connection.execute(
            f"SELECT event_id, recipient, status, attempts, last_error "
            f"FROM alert_deliveries WHERE event_id IN ({placeholders})",
            tuple(events),
        ).fetchall()
        for row in deliveries:
            events[row["event_id"]]["deliveries"][row["recipient"]] = {
                "status": row["status"],
                "attempts": row["attempts"],
                "last_error": row["last_error"],
            }
        return [events[row["id"]] for row in rows]

    @staticmethod
    def _decode_context(raw: str | None) -> dict:
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("AlertStore nao foi inicializado")
        return self._connection
