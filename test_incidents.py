import tempfile
import unittest
from pathlib import Path

from incidents import IncidentStore


class IncidentStoreTests(unittest.TestCase):
    def test_incident_is_opened_resolved_and_kept_in_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "incidents.db"
            store = IncidentStore(database_path)
            store.initialize()
            try:
                opened = store.record_transition("1001", "offline", now=100)
                self.assertEqual(opened["status"], "open")
                self.assertEqual(opened["duration_seconds"], 0)

                resolved = store.record_transition("1001", "online", now=145)
                self.assertEqual(resolved["status"], "resolved")
                self.assertEqual(resolved["duration_seconds"], 45)
            finally:
                store.close()

            restarted_store = IncidentStore(database_path)
            restarted_store.initialize()
            try:
                history = restarted_store.recent(now=145)
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["id"], opened["id"])
            finally:
                restarted_store.close()

    def test_repeated_offline_event_does_not_duplicate_open_incident(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IncidentStore(Path(directory) / "incidents.db")
            store.initialize()
            try:
                first = store.record_transition("1001", "offline", now=100)
                repeated = store.record_transition("1001", "offline", now=110)
                self.assertEqual(repeated["id"], first["id"])
                self.assertEqual(len(store.open_by_extension(now=110)), 1)
            finally:
                store.close()
