"""Historico cronologico, agendamento de alertas e relatorios de disponibilidade."""

from __future__ import annotations

import sqlite3
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


WEEKDAY_LABELS = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom")


class AvailabilityStore:
    def __init__(self, database_path: Path):
        self._database_path = database_path
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            connection = sqlite3.connect(self._database_path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS availability_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    extension TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('online', 'offline')),
                    occurred_at REAL NOT NULL,
                    source TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    sector TEXT NOT NULL DEFAULT '',
                    UNIQUE(extension, status, occurred_at, source)
                );
                CREATE INDEX IF NOT EXISTS idx_availability_events_extension_time
                    ON availability_events(extension, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_availability_events_time
                    ON availability_events(occurred_at);

                CREATE TABLE IF NOT EXISTS responsible_notification_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER NOT NULL UNIQUE,
                    extension TEXT NOT NULL,
                    offline_at REAL NOT NULL,
                    due_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    target TEXT,
                    alert_event_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_responsible_jobs_due
                    ON responsible_notification_jobs(status, due_at);
                """
            )
            self._connection = connection
            self._backfill_incidents_locked()
            connection.commit()

    def record_event(
        self,
        extension: str,
        status: str,
        occurred_at: float | None = None,
        source: str = "transition",
        profile: dict | None = None,
    ) -> None:
        if status not in {"online", "offline"}:
            raise ValueError(f"Status invalido: {status}")
        profile = profile or {}
        occurred_at = occurred_at if occurred_at is not None else time.time()
        connection = self._require_connection()
        with self._lock:
            connection.execute(
                "INSERT OR IGNORE INTO availability_events"
                "(extension, status, occurred_at, source, name, sector) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(extension),
                    status,
                    occurred_at,
                    source,
                    str(profile.get("nome") or ""),
                    str(profile.get("setor") or ""),
                ),
            )
            connection.commit()

    def schedule_offline(self, incident_id: int, extension: str, offline_at: float, due_at: float) -> dict:
        now = time.time()
        connection = self._require_connection()
        with self._lock:
            connection.execute(
                "INSERT OR IGNORE INTO responsible_notification_jobs"
                "(incident_id, extension, offline_at, due_at, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (incident_id, str(extension), offline_at, due_at, now, now),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM responsible_notification_jobs WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        return dict(row)

    def due_jobs(self, now: float | None = None) -> list[dict]:
        now = now if now is not None else time.time()
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                "SELECT * FROM responsible_notification_jobs "
                "WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id",
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def jobs_with_status(self, *statuses: str) -> list[dict]:
        if not statuses:
            return []
        connection = self._require_connection()
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            rows = connection.execute(
                f"SELECT * FROM responsible_notification_jobs WHERE status IN ({placeholders}) "
                "ORDER BY updated_at, id",
                tuple(statuses),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_job(self, incident_id: int) -> dict | None:
        connection = self._require_connection()
        with self._lock:
            row = connection.execute(
                "SELECT * FROM responsible_notification_jobs WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_job(
        self,
        incident_id: int,
        status: str,
        reason: str | None = None,
        target: str | None = None,
        alert_event_id: str | None = None,
    ) -> None:
        connection = self._require_connection()
        with self._lock:
            connection.execute(
                "UPDATE responsible_notification_jobs SET status = ?, reason = ?, "
                "target = COALESCE(?, target), alert_event_id = COALESCE(?, alert_event_id), "
                "updated_at = ? WHERE incident_id = ?",
                (status, reason, target, alert_event_id, time.time(), incident_id),
            )
            connection.commit()

    def cohort_size(self, offline_at: float, window_seconds: float) -> int:
        connection = self._require_connection()
        with self._lock:
            row = connection.execute(
                "SELECT COUNT(*) FROM responsible_notification_jobs "
                "WHERE offline_at BETWEEN ? AND ?",
                (offline_at - window_seconds, offline_at + window_seconds),
            ).fetchone()
        return int(row[0])

    def notification_summary(self) -> dict:
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM responsible_notification_jobs "
                "GROUP BY status"
            ).fetchall()
        counts = {row["status"]: row["total"] for row in rows}
        return {
            "pending": counts.get("pending", 0),
            "sent": counts.get("dispatched", 0),
            "suppressed": sum(
                total for status, total in counts.items() if status == "suppressed"
            ),
            "total": sum(counts.values()),
        }

    def suppress_pending_for_extensions(self, extensions, reason: str = "extension_removed") -> int:
        values = sorted({str(extension) for extension in extensions})
        if not values:
            return 0
        connection = self._require_connection()
        placeholders = ",".join("?" for _ in values)
        with self._lock:
            result = connection.execute(
                "UPDATE responsible_notification_jobs SET status = 'suppressed', reason = ?, "
                "updated_at = ? WHERE status = 'pending' "
                f"AND extension IN ({placeholders})",
                (reason, time.time(), *values),
            )
            connection.commit()
        return result.rowcount

    def build_report(
        self,
        profiles: dict[str, dict],
        calendar,
        days: int = 30,
        now: float | None = None,
        minimum_workdays: int = 20,
    ) -> dict:
        now = now if now is not None else time.time()
        start = now - max(1, min(int(days), 366)) * 86_400
        events = self._events_until(now)
        by_extension: dict[str, list[dict]] = defaultdict(list)
        for event in events:
            by_extension[event["extension"]].append(event)
        extensions = sorted(set(profiles) | set(by_extension), key=lambda value: (len(value), value))
        work_intervals = calendar.working_intervals(start, now)
        expected_seconds = sum(end - begin for begin, end in work_intervals)
        working_days = calendar.working_day_count(start, now)
        timezone = ZoneInfo(calendar.timezone_name)
        weekday_totals = defaultdict(lambda: {"online": 0.0, "offline": 0.0})
        individual = []
        for extension in extensions:
            metric = self._extension_metric(
                extension,
                by_extension.get(extension, []),
                profiles.get(extension, {}),
                start,
                now,
                work_intervals,
                expected_seconds,
                timezone,
                weekday_totals,
                working_days,
                minimum_workdays,
                calendar.configured,
            )
            individual.append(metric)

        sectors: dict[str, dict] = {}
        grouped = defaultdict(list)
        for item in individual:
            grouped[item["sector"] or "Sem setor"].append(item)
        for sector, items in sorted(grouped.items()):
            sectors[sector] = self._aggregate(items)
            sectors[sector]["sector"] = sector

        overall = self._aggregate(individual)
        overall["data_sufficient"] = bool(
            calendar.configured
            and working_days >= minimum_workdays
            and overall.get("coverage_percent", 0) >= 80
        )
        for item in individual:
            item.pop("_outage_durations", None)
        heatmap = []
        for weekday, label in enumerate(WEEKDAY_LABELS):
            total = weekday_totals[weekday]
            monitored = total["online"] + total["offline"]
            heatmap.append(
                {
                    "weekday": label,
                    "availability_percent": round(total["online"] / monitored * 100, 1)
                    if monitored
                    else None,
                }
            )
        return {
            "generated_at": now,
            "period": {"start": start, "end": now, "days": days},
            "calendar": {
                **calendar.summary(),
                "working_days": working_days,
                "minimum_workdays": minimum_workdays,
                "exception_count": calendar.exception_count(start, now),
            },
            "overall": overall,
            "sectors": list(sectors.values()),
            "individual": individual,
            "weekday_availability": heatmap,
            "event_count": sum(len(value) for value in by_extension.values()),
        }

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _events_until(self, end: float) -> list[dict]:
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                "SELECT extension, status, occurred_at, source, name, sector "
                "FROM availability_events WHERE occurred_at <= ? "
                "ORDER BY extension, occurred_at, id",
                (end,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _extension_metric(
        self,
        extension: str,
        events: list[dict],
        profile: dict,
        start: float,
        end: float,
        work_intervals: list[tuple[float, float]],
        expected_seconds: float,
        timezone: ZoneInfo,
        weekday_totals: dict,
        working_days: int,
        minimum_workdays: int,
        calendar_configured: bool,
    ) -> dict:
        state = None
        cursor = start
        intervals: list[tuple[float, float, str]] = []
        period_events = []
        for event in events:
            occurred_at = float(event["occurred_at"])
            if occurred_at <= start:
                state = event["status"]
                continue
            if occurred_at > end:
                break
            if state is not None and occurred_at > cursor:
                intervals.append((cursor, occurred_at, state))
            state = event["status"]
            cursor = occurred_at
            period_events.append(event)
        if state is not None and cursor < end:
            intervals.append((cursor, end, state))

        online = 0.0
        offline = 0.0
        for interval_start, interval_end, interval_state in intervals:
            for work_start, work_end in work_intervals:
                overlap = max(0.0, min(interval_end, work_end) - max(interval_start, work_start))
                if not overlap:
                    continue
                if interval_state == "online":
                    online += overlap
                else:
                    offline += overlap
                weekday = datetime.fromtimestamp(max(interval_start, work_start), timezone).weekday()
                weekday_totals[weekday][interval_state] += overlap

        monitored = online + offline
        availability = round(online / monitored * 100, 1) if monitored else None
        coverage = round(monitored / expected_seconds * 100, 1) if expected_seconds else 0.0
        outages = []
        offline_started = None
        connection_minutes: list[int] = []
        disconnection_minutes: list[int] = []
        daily = defaultdict(lambda: {"connections": [], "disconnections": []})
        weekly = defaultdict(lambda: {"connections": [], "disconnections": []})
        for event in period_events:
            occurred = datetime.fromtimestamp(float(event["occurred_at"]), timezone)
            minutes = occurred.hour * 60 + occurred.minute
            is_observed_transition = event.get("source") != "baseline"
            if event["status"] == "offline":
                offline_started = float(event["occurred_at"])
                if is_observed_transition:
                    disconnection_minutes.append(minutes)
                    daily[occurred.date().isoformat()]["disconnections"].append(minutes)
                    weekly[occurred.weekday()]["disconnections"].append(minutes)
            else:
                if is_observed_transition:
                    connection_minutes.append(minutes)
                    daily[occurred.date().isoformat()]["connections"].append(minutes)
                    weekly[occurred.weekday()]["connections"].append(minutes)
                if offline_started is not None:
                    outages.append(max(0.0, float(event["occurred_at"]) - offline_started))
                    offline_started = None
        if offline_started is not None:
            outages.append(max(0.0, end - offline_started))
        daily_activity = [
            {
                "date": day,
                "first_connection": self._format_minutes(min(values["connections"]))
                if values["connections"]
                else None,
                "last_disconnection": self._format_minutes(max(values["disconnections"]))
                if values["disconnections"]
                else None,
            }
            for day, values in sorted(daily.items())
        ]
        weekly_pattern = [
            {
                "weekday": WEEKDAY_LABELS[weekday],
                "typical_connection": self._format_minutes(
                    statistics.median(weekly[weekday]["connections"])
                )
                if weekly[weekday]["connections"]
                else None,
                "typical_disconnection": self._format_minutes(
                    statistics.median(weekly[weekday]["disconnections"])
                )
                if weekly[weekday]["disconnections"]
                else None,
            }
            for weekday in range(7)
        ]
        return {
            "extension": extension,
            "name": profile.get("nome", ""),
            "sector": profile.get("setor", ""),
            "email_configured": bool(profile.get("email")),
            "availability_percent": availability,
            "coverage_percent": min(100.0, coverage),
            "online_seconds": round(online),
            "offline_seconds": round(offline),
            "incident_count": len(
                [
                    event
                    for event in period_events
                    if event["status"] == "offline" and event.get("source") != "baseline"
                ]
            ),
            "longest_outage_seconds": round(max(outages, default=0)),
            "average_outage_seconds": round(statistics.mean(outages)) if outages else 0,
            "median_outage_seconds": round(statistics.median(outages)) if outages else 0,
            "average_reconnect_seconds": round(statistics.mean(outages)) if outages else 0,
            "typical_connection": self._format_minutes(statistics.median(connection_minutes)) if connection_minutes else None,
            "typical_disconnection": self._format_minutes(statistics.median(disconnection_minutes)) if disconnection_minutes else None,
            "daily_activity": daily_activity,
            "weekly_pattern": weekly_pattern,
            "data_sufficient": bool(
                calendar_configured
                and working_days >= minimum_workdays
                and expected_seconds
                and coverage >= 80
            ),
            "_outage_durations": outages,
        }

    @staticmethod
    def _aggregate(items: list[dict]) -> dict:
        online = sum(item["online_seconds"] for item in items)
        offline = sum(item["offline_seconds"] for item in items)
        monitored = online + offline
        coverages = [item["coverage_percent"] for item in items]
        outages = [duration for item in items for duration in item.get("_outage_durations", [])]
        return {
            "extension_count": len(items),
            "availability_percent": round(online / monitored * 100, 1) if monitored else None,
            "coverage_percent": round(statistics.mean(coverages), 1) if coverages else 0.0,
            "online_seconds": online,
            "offline_seconds": offline,
            "incident_count": sum(item["incident_count"] for item in items),
            "email_configured_count": sum(item["email_configured"] for item in items),
            "longest_outage_seconds": max((item["longest_outage_seconds"] for item in items), default=0),
            "average_outage_seconds": round(statistics.mean(outages)) if outages else 0,
            "median_outage_seconds": round(statistics.median(outages)) if outages else 0,
            "average_reconnect_seconds": round(statistics.mean(outages)) if outages else 0,
        }

    @staticmethod
    def _format_minutes(value: float) -> str:
        minutes = int(round(value)) % (24 * 60)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    def _backfill_incidents_locked(self) -> None:
        connection = self._connection
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "incidents" not in tables:
            return
        connection.execute(
            "INSERT OR IGNORE INTO availability_events(extension, status, occurred_at, source) "
            "SELECT incident.extension, 'offline', incident.opened_at, 'incident_backfill' "
            "FROM incidents AS incident WHERE NOT EXISTS ("
            "SELECT 1 FROM availability_events AS event "
            "WHERE event.extension = incident.extension AND event.status = 'offline' "
            "AND ABS(event.occurred_at - incident.opened_at) < 0.001)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO availability_events(extension, status, occurred_at, source) "
            "SELECT incident.extension, 'online', incident.resolved_at, 'incident_backfill' "
            "FROM incidents AS incident "
            "WHERE incident.resolved_at IS NOT NULL AND incident.resolution_reason = 'online' "
            "AND NOT EXISTS (SELECT 1 FROM availability_events AS event "
            "WHERE event.extension = incident.extension AND event.status = 'online' "
            "AND ABS(event.occurred_at - incident.resolved_at) < 0.001)"
        )

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("AvailabilityStore nao foi inicializado")
        return self._connection
