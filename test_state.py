import unittest

from state import StateTracker


class StateTrackerTests(unittest.TestCase):
    def test_transition_is_only_confirmed_after_debounce(self):
        tracker = StateTracker(debounce_seconds=30)
        tracker.update("1001", True, now=100)
        tracker.update("1001", False, now=110)

        pending = tracker.snapshot(now=120)[0]
        self.assertTrue(pending["online"])
        self.assertEqual(pending["pending_status"], "offline")
        self.assertEqual(pending["confirmation_remaining_seconds"], 20)
        self.assertEqual(tracker.tick(now=139), [])

        self.assertEqual(tracker.tick(now=140), [("1001", "offline")])
        confirmed = tracker.snapshot(now=140)[0]
        self.assertFalse(confirmed["online"])
        self.assertIsNone(confirmed["pending_status"])

    def test_history_records_only_confirmed_transitions(self):
        tracker = StateTracker(debounce_seconds=10)
        tracker.update("1001", True, now=0)
        tracker.update("1001", False, now=5)
        tracker.tick(now=15)

        events = tracker.recent_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["extension"], "1001")
        self.assertEqual(events[0]["status"], "offline")
        self.assertEqual(events[0]["previous_status"], "online")


if __name__ == "__main__":
    unittest.main()
