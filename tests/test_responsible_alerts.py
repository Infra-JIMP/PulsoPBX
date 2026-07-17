import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from availability import AvailabilityStore
from responsible_alerts import ResponsibleAlertScheduler
from state import StateTracker
from work_calendar import WorkCalendar


class _FakeAlerts:
    def __init__(self, offline_sent=True):
        self.offline_sent = offline_sent
        self.events = {}
        self.changes = []

    def enqueue(self, extension, status, now=None, recipients=None, context=None):
        event_id = f"event-{len(self.events) + 1}"
        sent = status == "offline" and self.offline_sent
        event = {
            "id": event_id,
            "status": "sent" if sent else "queued",
            "sent_count": 1 if sent else 0,
            "total_recipients": len(recipients or []),
        }
        self.events[event_id] = event
        self.changes.append((extension, status, list(recipients or []), dict(context or {})))
        return event

    def get_event(self, event_id):
        return self.events.get(event_id)

    def cancel_event(self, event_id, reason):
        event = self.events[event_id]
        if not event["sent_count"]:
            event["status"] = "failed"
        return event


class ResponsibleAlertSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = AvailabilityStore(Path(self.directory.name) / "history.db")
        self.store.initialize()
        self.calendar = self._calendar()

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def _calendar(self):
        path = Path(self.directory.name) / "calendar.json"
        path.write_text(
            json.dumps(
                {
                    "timezone": "America/Sao_Paulo",
                    "week": {
                        name: [["00:00", "23:59"]]
                        for name in (
                            "monday",
                            "tuesday",
                            "wednesday",
                            "thursday",
                            "friday",
                            "saturday",
                            "sunday",
                        )
                    },
                    "exceptions": {},
                }
            ),
            encoding="utf-8",
        )
        return WorkCalendar(path)

    @staticmethod
    def _resolver(extension):
        return f"email:{extension}@example.com", {
            "nome": f"Pessoa {extension}",
            "setor": "TI",
            "email": f"{extension}@example.com",
        }

    def _scheduler(self, tracker, alerts, threshold=5):
        return ResponsibleAlertScheduler(
            self.store,
            self.calendar,
            tracker,
            alerts,
            client=None,
            delay_seconds=120,
            mass_outage_threshold=threshold,
            mass_outage_window_seconds=60,
            profile_resolver=self._resolver,
        )

    def _limited_calendar(self):
        path = Path(self.directory.name) / "limited-calendar.json"
        path.write_text(
            json.dumps(
                {
                    "timezone": "America/Sao_Paulo",
                    "week": {
                        name: [["08:00", "17:00"]]
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

    def _split_calendar(self):
        path = Path(self.directory.name) / "split-calendar.json"
        path.write_text(
            json.dumps(
                {
                    "timezone": "America/Sao_Paulo",
                    "week": {
                        name: [["08:00", "12:00"], ["13:00", "17:00"]]
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

    async def test_reconnection_during_grace_period_cancels_email(self):
        tracker = StateTracker(0)
        tracker.update("1001", False, now=100)
        alerts = _FakeAlerts()
        scheduler = self._scheduler(tracker, alerts)
        incident = {"id": 1, "opened_at": 100}

        await scheduler.schedule_transition("1001", "offline", incident, now=100)
        await scheduler.schedule_transition("1001", "online", incident, now=150)

        self.assertEqual(self.store.get_job(1)["status"], "cancelled")
        self.assertEqual(alerts.changes, [])

    async def test_due_alert_goes_only_to_the_extension_responsible(self):
        tracker = StateTracker(0)
        tracker.update("1001", False, now=100)
        alerts = _FakeAlerts()
        scheduler = self._scheduler(tracker, alerts)
        incident = {"id": 1, "opened_at": 100}

        await scheduler.schedule_transition("1001", "offline", incident, now=100)
        await scheduler.process_once(now=220)

        self.assertEqual(self.store.get_job(1)["status"], "dispatched")
        self.assertEqual(alerts.changes[0][1], "offline")
        self.assertEqual(alerts.changes[0][2], ["email:1001@example.com"])

    async def test_collective_outage_suppresses_all_individual_emails(self):
        tracker = StateTracker(0)
        alerts = _FakeAlerts()
        scheduler = self._scheduler(tracker, alerts, threshold=5)
        for index in range(5):
            extension = str(1001 + index)
            tracker.update(extension, False, now=100 + index)
            await scheduler.schedule_transition(
                extension,
                "offline",
                {"id": index + 1, "opened_at": 100 + index},
                now=100 + index,
            )

        await scheduler.process_once(now=224)

        self.assertEqual(alerts.changes, [])
        self.assertTrue(
            all(self.store.get_job(index + 1)["reason"] == "collective_outage" for index in range(5))
        )

    async def test_event_outside_working_hours_never_sends_email(self):
        zone = ZoneInfo("America/Sao_Paulo")
        opened_at = datetime(2026, 7, 13, 22, tzinfo=zone).timestamp()
        tracker = StateTracker(0)
        tracker.update("1001", False, now=opened_at)
        alerts = _FakeAlerts()
        scheduler = self._scheduler(tracker, alerts)
        scheduler._calendar = self._limited_calendar()

        await scheduler.schedule_transition(
            "1001", "offline", {"id": 8, "opened_at": opened_at}, now=opened_at
        )
        await scheduler.process_once(now=opened_at + 120)

        self.assertEqual(alerts.changes, [])
        self.assertEqual(self.store.get_job(8)["reason"], "outside_working_hours")

    async def test_return_is_sent_only_after_offline_email_was_delivered(self):
        for incident_id, offline_sent, expected_changes in ((1, True, 2), (2, False, 1)):
            with self.subTest(offline_sent=offline_sent):
                tracker = StateTracker(0)
                extension = str(1000 + incident_id)
                tracker.update(extension, False, now=100)
                alerts = _FakeAlerts(offline_sent=offline_sent)
                scheduler = self._scheduler(tracker, alerts)
                incident = {"id": incident_id, "opened_at": 100}

                await scheduler.schedule_transition(extension, "offline", incident, now=100)
                await scheduler.process_once(now=220)
                await scheduler.schedule_transition(extension, "online", incident, now=250)
                await scheduler.process_once(now=250)

                self.assertEqual(len(alerts.changes), expected_changes)
                if offline_sent:
                    self.assertEqual(alerts.changes[-1][1], "online")
                self.assertEqual(self.store.get_job(incident_id)["status"], "completed")

    async def test_recovered_state_is_reconciled_after_service_restart(self):
        tracker = StateTracker(0)
        tracker.update("1010", False, now=100)
        alerts = _FakeAlerts(offline_sent=True)
        scheduler = self._scheduler(tracker, alerts)
        incident = {"id": 10, "opened_at": 100}
        await scheduler.schedule_transition("1010", "offline", incident, now=100)
        await scheduler.process_once(now=220)

        tracker.update("1010", True, now=250)
        tracker.tick(now=250)
        await scheduler.process_once(now=250)

        self.assertEqual([change[1] for change in alerts.changes], ["offline", "online"])
        self.assertEqual(self.store.get_job(10)["status"], "completed")

    async def test_recovery_after_working_hours_is_recorded_without_email(self):
        zone = ZoneInfo("America/Sao_Paulo")
        opened_at = datetime(2026, 7, 13, 16, 50, tzinfo=zone).timestamp()
        recovered_at = datetime(2026, 7, 13, 17, 5, tzinfo=zone).timestamp()
        tracker = StateTracker(0)
        tracker.update("1001", False, now=opened_at)
        alerts = _FakeAlerts(offline_sent=True)
        scheduler = self._scheduler(tracker, alerts)
        scheduler._calendar = self._limited_calendar()
        incident = {"id": 20, "opened_at": opened_at}

        await scheduler.schedule_transition("1001", "offline", incident, now=opened_at)
        await scheduler.process_once(now=opened_at + 120)
        await scheduler.schedule_transition("1001", "online", incident, now=recovered_at)
        await scheduler.process_once(now=recovered_at)

        self.assertEqual([change[1] for change in alerts.changes], ["offline"])
        job = self.store.get_job(20)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["reason"], "return_skipped_outside_working_hours")

    async def test_recovery_on_next_workday_is_recorded_without_email(self):
        zone = ZoneInfo("America/Sao_Paulo")
        opened_at = datetime(2026, 7, 13, 16, 50, tzinfo=zone).timestamp()
        recovered_at = datetime(2026, 7, 14, 8, 5, tzinfo=zone).timestamp()
        tracker = StateTracker(0)
        tracker.update("1001", False, now=opened_at)
        alerts = _FakeAlerts(offline_sent=True)
        scheduler = self._scheduler(tracker, alerts)
        scheduler._calendar = self._limited_calendar()
        incident = {"id": 21, "opened_at": opened_at}

        await scheduler.schedule_transition("1001", "offline", incident, now=opened_at)
        await scheduler.process_once(now=opened_at + 120)
        await scheduler.schedule_transition("1001", "online", incident, now=recovered_at)
        await scheduler.process_once(now=recovered_at)

        self.assertEqual([change[1] for change in alerts.changes], ["offline"])
        job = self.store.get_job(21)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["reason"], "return_skipped_next_workday")

    async def test_same_day_recovery_counts_only_working_minutes(self):
        zone = ZoneInfo("America/Sao_Paulo")
        opened_at = datetime(2026, 7, 13, 11, 50, tzinfo=zone).timestamp()
        recovered_at = datetime(2026, 7, 13, 13, 10, tzinfo=zone).timestamp()
        tracker = StateTracker(0)
        tracker.update("1001", False, now=opened_at)
        alerts = _FakeAlerts(offline_sent=True)
        scheduler = self._scheduler(tracker, alerts)
        scheduler._calendar = self._split_calendar()
        incident = {"id": 22, "opened_at": opened_at}

        await scheduler.schedule_transition("1001", "offline", incident, now=opened_at)
        await scheduler.process_once(now=opened_at + 120)
        await scheduler.schedule_transition("1001", "online", incident, now=recovered_at)
        await scheduler.process_once(now=recovered_at)

        self.assertEqual([change[1] for change in alerts.changes], ["offline", "online"])
        self.assertEqual(alerts.changes[-1][3]["duration_seconds"], 20 * 60)


if __name__ == "__main__":
    unittest.main()
