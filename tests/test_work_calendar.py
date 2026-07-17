import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from work_calendar import WorkCalendar


class WorkCalendarTests(unittest.TestCase):
    def _calendar(self, directory: str) -> WorkCalendar:
        path = Path(directory) / "work_calendar.json"
        path.write_text(
            json.dumps(
                {
                    "timezone": "America/Sao_Paulo",
                    "week": {
                        "monday": [["08:00", "12:00"], ["13:00", "18:00"]],
                        "tuesday": [["08:00", "12:00"], ["13:00", "18:00"]],
                        "wednesday": [["08:00", "12:00"], ["13:00", "18:00"]],
                        "thursday": [["08:00", "12:00"], ["13:00", "18:00"]],
                        "friday": [["08:00", "12:00"], ["13:00", "17:00"]],
                        "saturday": [],
                        "sunday": [],
                    },
                    "exceptions": {
                        "2026-07-15": {"label": "Folga", "intervals": []},
                        "2026-07-18": {
                            "label": "Sabado especial",
                            "intervals": [["08:00", "12:00"]],
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        return WorkCalendar(path)

    def test_weekly_schedule_and_exceptions_are_applied(self):
        zone = ZoneInfo("America/Sao_Paulo")
        with tempfile.TemporaryDirectory() as directory:
            calendar = self._calendar(directory)

            self.assertTrue(calendar.configured)
            self.assertTrue(
                calendar.is_working_time(datetime(2026, 7, 14, 9, tzinfo=zone).timestamp())
            )
            self.assertFalse(
                calendar.is_working_time(datetime(2026, 7, 14, 12, 30, tzinfo=zone).timestamp())
            )
            self.assertFalse(
                calendar.is_working_time(datetime(2026, 7, 15, 9, tzinfo=zone).timestamp())
            )
            self.assertTrue(
                calendar.is_working_time(datetime(2026, 7, 18, 9, tzinfo=zone).timestamp())
            )

    def test_missing_calendar_safely_disables_working_time(self):
        with tempfile.TemporaryDirectory() as directory:
            calendar = WorkCalendar(Path(directory) / "missing.json")

            self.assertFalse(calendar.configured)
            self.assertFalse(calendar.is_working_time(0))
            self.assertEqual(calendar.working_intervals(0, 1000), [])

    def test_working_seconds_excludes_lunch_and_dates_use_calendar_timezone(self):
        zone = ZoneInfo("America/Sao_Paulo")
        with tempfile.TemporaryDirectory() as directory:
            calendar = self._calendar(directory)
            before_lunch = datetime(2026, 7, 14, 11, 30, tzinfo=zone).timestamp()
            after_lunch = datetime(2026, 7, 14, 13, 30, tzinfo=zone).timestamp()
            next_day = datetime(2026, 7, 16, 9, tzinfo=zone).timestamp()

            self.assertEqual(calendar.working_seconds(before_lunch, after_lunch), 3600)
            self.assertTrue(calendar.is_same_local_day(before_lunch, after_lunch))
            self.assertFalse(calendar.is_same_local_day(before_lunch, next_day))
            self.assertEqual(
                calendar.current_interval_end(before_lunch),
                datetime(2026, 7, 14, 12, tzinfo=zone).timestamp(),
            )
            self.assertIsNone(
                calendar.current_interval_end(
                    datetime(2026, 7, 14, 12, 30, tzinfo=zone).timestamp()
                )
            )

    def test_invalid_calendar_is_safely_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(
                json.dumps(
                    {
                        "week": {"monday": [["18:00", "08:00"]]},
                        "exceptions": {},
                    }
                ),
                encoding="utf-8",
            )
            calendar = WorkCalendar(path)

            with self.assertLogs("work_calendar", level="ERROR"):
                self.assertFalse(calendar.configured)


if __name__ == "__main__":
    unittest.main()
