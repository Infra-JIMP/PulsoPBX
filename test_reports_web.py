import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from availability import AvailabilityStore
from web import create_app
from work_calendar import WorkCalendar


class AvailabilityReportEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        calendar_path = root / "calendar.json"
        calendar_path.write_text(
            json.dumps(
                {
                    "timezone": "America/Sao_Paulo",
                    "week": {
                        name: [["08:00", "18:00"]]
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
        self.store = AvailabilityStore(root / "history.db")
        self.store.initialize()
        self.store.record_event(
            "1001",
            "online",
            source="baseline",
            profile={"nome": "Ana", "setor": "Financeiro"},
        )
        config = SimpleNamespace(
            dashboard_auth_enabled=False,
            demo_mode=False,
            report_minimum_workdays=20,
        )
        app = create_app(
            None,
            None,
            config,
            availability=self.store,
            calendar=WorkCalendar(calendar_path),
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.store.close()
        self.directory.cleanup()

    async def test_report_is_available_and_does_not_expose_email_addresses(self):
        response = await self.client.get("/api/reports/availability?days=30")
        payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertIn("overall", payload)
        self.assertIn("individual", payload)
        self.assertNotIn("@", json.dumps(payload))

    async def test_invalid_period_is_rejected(self):
        response = await self.client.get("/api/reports/availability?days=999")

        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
