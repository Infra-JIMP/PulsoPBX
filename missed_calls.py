"""E-mail para chamadas internas nao atendidas via CDR do MikoPBX."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests

from profiles import load_profiles, notification_target

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 10
LOOKBACK_SECONDS = 180
HISTORY_DAYS = 90


class MissedCallMonitor:
    """Consulta CDR, ignora o historico inicial e envia uma notificacao por chamada."""

    def __init__(self, config, alerts, database_path: Path):
        self._config = config
        self._alerts = alerts
        self._database_path = database_path
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._baselined = False
        self._availability_events_available = False

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._connection = sqlite3.connect(self._database_path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS missed_call_records (
                    record_hash TEXT PRIMARY KEY,
                    extension TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    notified_at REAL
                )
                """
            )
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS internal_call_history (
                    record_hash TEXT PRIMARY KEY,
                    linkedid TEXT NOT NULL DEFAULT '',
                    started_at REAL NOT NULL,
                    source_extension TEXT NOT NULL,
                    destination_extension TEXT NOT NULL,
                    source_name TEXT NOT NULL DEFAULT '',
                    destination_name TEXT NOT NULL DEFAULT '',
                    disposition TEXT NOT NULL,
                    talk_seconds INTEGER NOT NULL DEFAULT 0,
                    total_seconds INTEGER NOT NULL DEFAULT 0,
                    observed_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_internal_calls_started
                    ON internal_call_history(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_internal_calls_destination
                    ON internal_call_history(destination_extension, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_internal_calls_source
                    ON internal_call_history(source_extension, started_at DESC);
                """
            )
            self._availability_events_available = bool(
                self._connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'availability_events'"
                ).fetchone()
            )
            self._connection.commit()

    async def run(self) -> None:
        try:
            await asyncio.to_thread(self.backfill_history, HISTORY_DAYS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Falha ao importar historico de chamadas; coleta atual continuara ativa")
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Falha ao consultar chamadas perdidas no MikoPBX")
            await asyncio.sleep(self._config.missed_calls_poll_seconds)

    async def poll_once(self) -> int:
        records = await asyncio.to_thread(self._fetch_records)
        profiles = load_profiles()
        internal = [item for item in records if self._is_internal(item, profiles)]
        await asyncio.to_thread(self._store_calls, internal, profiles)
        candidates = [item for item in internal if self._is_missed(item)]
        if not self._baselined:
            for record in candidates:
                self._remember(self._record_hash(record), str(record.get("dst_num") or ""), None)
            self._baselined = True
            logger.info("Monitor de chamadas internas preparado com %d registro(s) recente(s)", len(candidates))
            return 0

        sent = 0
        for record in candidates:
            record_hash = self._record_hash(record)
            extension = str(record.get("dst_num") or "").strip()
            if self._already_seen(record_hash):
                continue
            target, profile = notification_target(extension)
            if target is None:
                logger.warning("Chamada perdida no ramal %s sem e-mail ativo; registro guardado", extension)
                self._remember(record_hash, extension, None)
                continue
            context = {
                "event_timestamp": self._display_time(record.get("start")),
                "caller": f"Ramal {str(record.get('src_num') or '').strip()}",
                "caller_name": str(record.get("src_name") or "").strip(),
                "nome": profile.get("nome") or "",
                "setor": profile.get("setor") or "Nao informado",
                "call_scope": "internal",
            }
            event = self._alerts.enqueue_missed_call(extension, [target], context)
            self._remember(record_hash, extension, time.time())
            sent += 1
            logger.info("Chamada interna nao atendida no ramal %s enfileirada como alerta %s", extension, event["id"])
        return sent

    def backfill_history(self, days: int = HISTORY_DAYS) -> int:
        """Importa o CDR historico sem disparar notificacoes antigas."""
        since = datetime.now() - timedelta(days=max(1, min(int(days), 366)))
        offset = 0
        imported = 0
        while True:
            records, pagination = self._fetch_page(since, offset)
            profiles = load_profiles()
            internal = [item for item in records if self._is_internal(item, profiles)]
            self._store_calls(internal, profiles)
            for record in internal:
                if self._is_missed(record):
                    self._remember(
                        self._record_hash(record),
                        str(record.get("dst_num") or ""),
                        None,
                    )
            imported += len(internal)
            if not pagination.get("hasMore") or not records:
                break
            offset += len(records)
        logger.info(
            "Historico de chamadas internas sincronizado: %d registro(s) em ate %d dias",
            imported,
            days,
        )
        return imported

    def _fetch_records(self) -> list[dict]:
        since = datetime.now() - timedelta(seconds=LOOKBACK_SECONDS)
        records, _ = self._fetch_page(since, 0)
        return records

    def _fetch_page(self, since: datetime, offset: int) -> tuple[list[dict], dict]:
        response = requests.get(
            f"{self._config.mikopbx_api_url}/cdr",
            headers={"Authorization": f"Bearer {self._config.mikopbx_api_key}"},
            params={
                "limit": 100,
                "offset": max(0, int(offset)),
                "dateFrom": since.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
            verify=self._config.mikopbx_verify_tls,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("result"):
            raise RuntimeError("MikoPBX recusou a consulta de CDR")
        data = payload.get("data") or {}
        if isinstance(data, dict):
            records = data.get("data") or data.get("records") or []
            pagination = data.get("pagination") or {}
        else:
            records = data
            pagination = {}
        return [record for record in records if isinstance(record, dict)], pagination

    def _is_internal(self, record: dict, profiles: dict[str, dict] | None = None) -> bool:
        disposition = str(record.get("disposition") or "").upper().replace(" ", "")
        extension = str(record.get("dst_num") or "").strip()
        source = str(record.get("src_num") or "").strip()
        if disposition not in {"ANSWERED", "NOANSWER", "BUSY", "FAILED", "CHANUNAVAIL"}:
            return False
        profiles = profiles or load_profiles()
        # Ambas as pontas precisam corresponder a colaboradores ativos.
        return self._is_active_internal(profiles.get(source)) and self._is_active_internal(
            profiles.get(extension)
        )

    @staticmethod
    def _is_missed(record: dict) -> bool:
        return str(record.get("disposition") or "").upper().replace(" ", "") == "NOANSWER"

    @staticmethod
    def _is_active_internal(profile: dict | None) -> bool:
        return bool(profile) and profile.get("ativo") is not False

    @staticmethod
    def _record_hash(record: dict) -> str:
        raw = "|".join(str(record.get(name) or "") for name in ("linkedid", "start", "src_num", "dst_num", "disposition"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _timestamp(value) -> float:
        raw = str(value or "").strip()
        if not raw:
            return time.time()
        normalized = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            for pattern in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
                try:
                    return datetime.strptime(raw, pattern).timestamp()
                except ValueError:
                    continue
        logger.warning("Horario de CDR desconhecido; usando horario de observacao")
        return time.time()

    @staticmethod
    def _seconds(value) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0

    def _store_calls(self, records: list[dict], profiles: dict[str, dict]) -> int:
        if not records:
            return 0
        connection = self._require_connection()
        now = time.time()
        values = []
        for record in records:
            source = str(record.get("src_num") or "").strip()
            destination = str(record.get("dst_num") or "").strip()
            source_profile = profiles.get(source, {})
            destination_profile = profiles.get(destination, {})
            values.append(
                (
                    self._record_hash(record),
                    str(record.get("linkedid") or "").strip(),
                    self._timestamp(record.get("start")),
                    source,
                    destination,
                    str(source_profile.get("nome") or record.get("src_name") or "").strip(),
                    str(destination_profile.get("nome") or record.get("dst_name") or "").strip(),
                    str(record.get("disposition") or "").upper().replace(" ", ""),
                    self._seconds(record.get("totalBillsec")),
                    self._seconds(record.get("totalDuration")),
                    now,
                )
            )
        with self._lock:
            connection.executemany(
                """
                INSERT INTO internal_call_history(
                    record_hash, linkedid, started_at, source_extension,
                    destination_extension, source_name, destination_name,
                    disposition, talk_seconds, total_seconds, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_hash) DO UPDATE SET
                    source_name = excluded.source_name,
                    destination_name = excluded.destination_name,
                    disposition = excluded.disposition,
                    talk_seconds = excluded.talk_seconds,
                    total_seconds = excluded.total_seconds,
                    observed_at = excluded.observed_at
                """,
                values,
            )
            connection.commit()
        return len(values)

    def history(self, days: int = 30, limit: int = 200) -> dict:
        days = max(1, min(int(days), 366))
        limit = max(1, min(int(limit), 500))
        start = time.time() - days * 86_400
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                """
                SELECT * FROM internal_call_history
                WHERE started_at >= ?
                ORDER BY started_at DESC
                """,
                (start,),
            ).fetchall()
            all_calls = [self._serialize_call_locked(connection, row) for row in rows]
        calls = all_calls[:limit]
        disposition_counts = Counter(call["disposition"] for call in all_calls)
        return {
            "days": days,
            "summary": self._summary(disposition_counts, all_calls),
            "calls": calls,
            "generated_at": time.time(),
        }

    def extension_history(self, extension: str, days: int = 30) -> dict:
        days = max(1, min(int(days), 366))
        extension = str(extension).strip()
        start = time.time() - days * 86_400
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                """
                SELECT * FROM internal_call_history
                WHERE destination_extension = ? AND started_at >= ?
                ORDER BY started_at DESC
                """,
                (extension, start),
            ).fetchall()
            all_calls = [self._serialize_call_locked(connection, row) for row in rows]
        counts = Counter(call["disposition"] for call in all_calls)
        daily_map: dict[str, dict] = {}
        for call in all_calls:
            day = datetime.fromtimestamp(call["started_at"]).strftime("%Y-%m-%d")
            item = daily_map.setdefault(day, {"date": day, "received": 0, "answered": 0})
            item["received"] += 1
            item["answered"] += int(call["disposition"] == "ANSWERED")
        daily = []
        today = datetime.now().date()
        for distance in range(days - 1, -1, -1):
            key = (today - timedelta(days=distance)).isoformat()
            daily.append(daily_map.get(key, {"date": key, "received": 0, "answered": 0}))
        profile = load_profiles().get(extension, {})
        summary = self._summary(counts, all_calls)
        answered_durations = [
            call["talk_seconds"] for call in all_calls if call["disposition"] == "ANSWERED"
        ]
        summary["average_talk_seconds"] = (
            round(sum(answered_durations) / len(answered_durations))
            if answered_durations
            else 0
        )
        return {
            "extension": extension,
            "name": str(profile.get("nome") or ""),
            "sector": str(profile.get("setor") or ""),
            "days": days,
            "summary": summary,
            "daily": daily,
            "calls": all_calls[:200],
            "generated_at": time.time(),
        }

    @staticmethod
    def _summary(counts: Counter, calls: list[dict]) -> dict:
        total = sum(counts.values())
        answered = counts.get("ANSWERED", 0)
        return {
            "total": total,
            "answered": answered,
            "not_answered": counts.get("NOANSWER", 0),
            "busy": counts.get("BUSY", 0),
            "failed": counts.get("FAILED", 0) + counts.get("CHANUNAVAIL", 0),
            "answer_rate_percent": round(answered / total * 100, 1) if total else None,
            "instability_count": sum(call["connection_status"] != "stable" for call in calls),
        }

    def _serialize_call_locked(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        status, event_count = self._connection_status_locked(connection, row)
        return {
            "id": row["record_hash"],
            "started_at": row["started_at"],
            "source_extension": row["source_extension"],
            "destination_extension": row["destination_extension"],
            "source_name": row["source_name"],
            "destination_name": row["destination_name"],
            "disposition": row["disposition"],
            "talk_seconds": row["talk_seconds"],
            "total_seconds": row["total_seconds"],
            "ring_seconds": max(0, row["total_seconds"] - row["talk_seconds"]),
            "connection_status": status,
            "connection_event_count": event_count,
        }

    def _connection_status_locked(self, connection: sqlite3.Connection, row: sqlite3.Row) -> tuple[str, int]:
        if not self._availability_events_available:
            return "stable", 0
        start = float(row["started_at"])
        end = start + max(1, int(row["total_seconds"] or 0))
        extensions = (row["source_extension"], row["destination_extension"])
        prior_states = connection.execute(
            """
            SELECT extension, status FROM availability_events AS current
            WHERE extension IN (?, ?) AND occurred_at <= ?
              AND id = (
                SELECT id FROM availability_events
                WHERE extension = current.extension AND occurred_at <= ?
                ORDER BY occurred_at DESC, id DESC LIMIT 1
              )
            """,
            (*extensions, start, start),
        ).fetchall()
        events = connection.execute(
            """
            SELECT status, occurred_at FROM availability_events
            WHERE extension IN (?, ?) AND occurred_at > ? AND occurred_at <= ?
            ORDER BY occurred_at, id
            """,
            (*extensions, start, end),
        ).fetchall()
        if any(item["status"] == "offline" for item in prior_states):
            return "drop", sum(item["status"] == "offline" for item in events)
        offline_events = [item for item in events if item["status"] == "offline"]
        if not offline_events:
            return "stable", 0
        last_offline_at = float(offline_events[-1]["occurred_at"])
        restored = any(
            item["status"] == "online" and float(item["occurred_at"]) > last_offline_at
            for item in events
        )
        return ("oscillation" if restored else "drop"), len(offline_events)

    @staticmethod
    def _display_time(value) -> str:
        raw = str(value or "").strip()
        return raw if raw else datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    def _already_seen(self, record_hash: str) -> bool:
        connection = self._require_connection()
        with self._lock:
            return connection.execute("SELECT 1 FROM missed_call_records WHERE record_hash = ?", (record_hash,)).fetchone() is not None

    def _remember(self, record_hash: str, extension: str, notified_at: float | None) -> None:
        connection = self._require_connection()
        with self._lock:
            connection.execute(
                "INSERT OR IGNORE INTO missed_call_records(record_hash, extension, observed_at, notified_at) VALUES (?, ?, ?, ?)",
                (record_hash, extension, time.time(), notified_at),
            )
            connection.commit()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Monitor de chamadas perdidas nao inicializado")
        return self._connection
