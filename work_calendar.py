"""Calendario de expediente e excecoes usado por alertas e relatorios."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


class WorkCalendar:
    """Carrega um calendario local, recarregando-o quando o arquivo muda."""

    def __init__(self, path: Path, default_timezone: str = "America/Sao_Paulo"):
        self.path = path
        self.default_timezone = default_timezone
        self._mtime: float | None = None
        self._data: dict | None = None
        self._timezone = ZoneInfo(default_timezone)

    @property
    def configured(self) -> bool:
        self._reload_if_needed()
        return self._data is not None

    @property
    def timezone_name(self) -> str:
        self._reload_if_needed()
        return str(self._timezone.key)

    def is_working_time(self, timestamp: float) -> bool:
        if not self.configured:
            return False
        moment = datetime.fromtimestamp(timestamp, self._timezone)
        return any(start <= moment < end for start, end in self.intervals_for_date(moment.date()))

    def intervals_for_date(self, day: date) -> list[tuple[datetime, datetime]]:
        self._reload_if_needed()
        if self._data is None:
            return []
        exception = self._data.get("exceptions", {}).get(day.isoformat())
        raw_intervals = (
            exception.get("intervals", [])
            if isinstance(exception, dict)
            else self._data.get("week", {}).get(WEEKDAYS[day.weekday()], [])
        )
        intervals: list[tuple[datetime, datetime]] = []
        for raw in raw_intervals:
            if not isinstance(raw, list) or len(raw) != 2:
                continue
            start_time = self._parse_time(raw[0])
            end_time = self._parse_time(raw[1])
            start = datetime.combine(day, start_time, self._timezone)
            end = datetime.combine(day, end_time, self._timezone)
            if end > start:
                intervals.append((start, end))
        return intervals

    def working_intervals(self, start_timestamp: float, end_timestamp: float) -> list[tuple[float, float]]:
        if not self.configured or end_timestamp <= start_timestamp:
            return []
        start_local = datetime.fromtimestamp(start_timestamp, self._timezone)
        end_local = datetime.fromtimestamp(end_timestamp, self._timezone)
        current = start_local.date()
        last = end_local.date()
        result: list[tuple[float, float]] = []
        while current <= last:
            for start, end in self.intervals_for_date(current):
                clipped_start = max(start.timestamp(), start_timestamp)
                clipped_end = min(end.timestamp(), end_timestamp)
                if clipped_end > clipped_start:
                    result.append((clipped_start, clipped_end))
            current += timedelta(days=1)
        return result

    def exception_count(self, start_timestamp: float, end_timestamp: float) -> int:
        if not self.configured:
            return 0
        start_day = datetime.fromtimestamp(start_timestamp, self._timezone).date()
        end_day = datetime.fromtimestamp(end_timestamp, self._timezone).date()
        exceptions = self._data.get("exceptions", {}) if self._data else {}
        return sum(start_day <= date.fromisoformat(value) <= end_day for value in exceptions)

    def working_day_count(self, start_timestamp: float, end_timestamp: float) -> int:
        if not self.configured:
            return 0
        return len(
            {
                datetime.fromtimestamp(start, self._timezone).date()
                for start, _ in self.working_intervals(start_timestamp, end_timestamp)
            }
        )

    def summary(self) -> dict:
        self._reload_if_needed()
        return {
            "configured": self._data is not None,
            "timezone": self.timezone_name,
            "path": self.path.name,
        }

    def _reload_if_needed(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._mtime = None
            self._data = None
            self._timezone = ZoneInfo(self.default_timezone)
            return
        if self._mtime == mtime:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            timezone_name = str(data.get("timezone") or self.default_timezone)
            timezone = ZoneInfo(timezone_name)
            week = data.get("week")
            if not isinstance(week, dict):
                raise ValueError("campo 'week' ausente")
            self._validate_data(data)
            self._data = data
            self._timezone = timezone
            self._mtime = mtime
        except Exception:
            logger.exception("Calendario invalido em %s; alertas programados ficam suspensos", self.path)
            self._data = None
            self._mtime = mtime
            self._timezone = ZoneInfo(self.default_timezone)

    @staticmethod
    def _parse_time(value: str) -> time:
        return datetime.strptime(str(value), "%H:%M").time()

    @classmethod
    def _validate_data(cls, data: dict) -> None:
        week = data["week"]
        for weekday in WEEKDAYS:
            cls._validate_intervals(week.get(weekday, []), f"week.{weekday}")
        exceptions = data.get("exceptions", {})
        if not isinstance(exceptions, dict):
            raise ValueError("campo 'exceptions' deve ser um objeto")
        for day, exception in exceptions.items():
            date.fromisoformat(str(day))
            if not isinstance(exception, dict):
                raise ValueError(f"excecao {day} deve ser um objeto")
            cls._validate_intervals(
                exception.get("intervals", []),
                f"exceptions.{day}.intervals",
            )

    @classmethod
    def _validate_intervals(cls, raw_intervals, field: str) -> None:
        if not isinstance(raw_intervals, list):
            raise ValueError(f"{field} deve ser uma lista")
        for raw in raw_intervals:
            if not isinstance(raw, list) or len(raw) != 2:
                raise ValueError(f"intervalo invalido em {field}")
            start = cls._parse_time(raw[0])
            end = cls._parse_time(raw[1])
            if end <= start:
                raise ValueError(f"intervalo deve terminar depois de iniciar em {field}")
