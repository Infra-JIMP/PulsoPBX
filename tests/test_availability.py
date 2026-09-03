import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from availability import AvailabilityStore
from incidents import IncidentStore
from work_calendar import WorkCalendar


class AvailabilityStoreTests(unittest.TestCase):
    def _calendar(self, directory: str) -> WorkCalendar:
        path = Path(directory) / "calendar.json"
        path.write_text(
            json.dumps(
                {
                    "timezone": "America/Sao_Paulo",
                    "week": {
                        name: [["09:00", "17:00"]]
                        for name in (
                            "monday",
                            "tuesday",
                            "wednesday",
                            "thursday",
                            "friday",
                        )
                    }
                    | {"saturday": [], "sunday": []},
                    "exceptions": {},
                }
            ),
            encoding="utf-8",
        )
        return WorkCalendar(path)

    def test_report_calculates_operational_metrics_and_daily_history(self):
        zone = ZoneInfo("America/Sao_Paulo")
        timestamp = lambda hour, minute=0: datetime(
            2026, 7, 13, hour, minute, tzinfo=zone
        ).timestamp()
        with tempfile.TemporaryDirectory() as directory:
            store = AvailabilityStore(Path(directory) / "history.db")
            store.initialize()
            profile = {"nome": "Ana", "setor": "Financeiro", "email": "ana@example.com"}
            store.record_event("1001", "online", timestamp(9), "baseline", profile)
            store.record_event("1001", "offline", timestamp(10), "transition", profile)
            store.record_event("1001", "online", timestamp(10, 30), "transition", profile)
            store.record_event("1001", "offline", timestamp(16), "transition", profile)
            store.record_event("1001", "online", timestamp(16, 10), "transition", profile)

            report = store.build_report(
                {"1001": profile},
                self._calendar(directory),
                days=1,
                now=timestamp(17),
                minimum_workdays=1,
            )
            metric = report["individual"][0]

            self.assertEqual(metric["availability_percent"], 91.7)
            self.assertEqual(metric["incident_count"], 2)
            self.assertEqual(metric["longest_outage_seconds"], 1800)
            self.assertEqual(metric["median_outage_seconds"], 1200)
            self.assertEqual(metric["daily_activity"][0]["first_connection"], "10:30")
            self.assertEqual(metric["daily_activity"][0]["last_disconnection"], "16:00")
            self.assertTrue(metric["data_sufficient"])
            self.assertEqual(report["sectors"][0]["sector"], "Financeiro")
            self.assertEqual(report["overall"]["email_configured_count"], 1)
            store.close()

    def test_existing_incidents_are_backfilled_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            incidents = IncidentStore(path)
            incidents.initialize()
            incidents.record_transition("1001", "offline", now=100)
            incidents.record_transition("1001", "online", now=160)
            incidents.close()

            store = AvailabilityStore(path)
            store.initialize()
            first_count = len(store._events_until(200))
            store.close()
            restarted = AvailabilityStore(path)
            restarted.initialize()
            second_count = len(restarted._events_until(200))

            self.assertEqual(first_count, 2)
            self.assertEqual(second_count, 2)
            restarted.close()

    def test_pending_notification_job_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            store = AvailabilityStore(path)
            store.initialize()
            store.schedule_offline(9, "1001", offline_at=100, due_at=220)
            store.close()

            restarted = AvailabilityStore(path)
            restarted.initialize()
            due = restarted.due_jobs(now=220)

            self.assertEqual(len(due), 1)
            self.assertEqual(due[0]["incident_id"], 9)
            restarted.close()

    def test_backfill_does_not_duplicate_events_already_recorded_live(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            incidents = IncidentStore(path)
            incidents.initialize()
            store = AvailabilityStore(path)
            store.initialize()
            incidents.record_transition("1001", "offline", now=100)
            store.record_event("1001", "offline", 100, "transition")
            incidents.record_transition("1001", "online", now=160)
            store.record_event("1001", "online", 160, "transition")
            store.close()
            incidents.close()

            restarted = AvailabilityStore(path)
            restarted.initialize()

            self.assertEqual(len(restarted._events_until(200)), 2)
            restarted.close()


if __name__ == "__main__":
    unittest.main()
