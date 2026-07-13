import asyncio
import tempfile
import unittest
from pathlib import Path

from alert_store import AlertStore
from alerts import AlertDispatcher


class _FakeNotifier:
    recipients = ["5511999999999"]

    def __init__(self, failures: int = 0):
        self.deliveries = []
        self.failures = failures

    def notify_recipient_change(self, recipient, extension, status, timestamp, is_test=False):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("falha simulada")
        self.deliveries.append((recipient, extension, status, timestamp, is_test))


class AlertDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def _deliver_all(self, dispatcher: AlertDispatcher) -> asyncio.Task:
        worker = asyncio.create_task(dispatcher.run())
        await asyncio.wait_for(dispatcher._queue.join(), timeout=1)
        return worker

    async def _stop_worker(self, worker: asyncio.Task) -> None:
        worker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker

    async def test_confirmed_change_is_delivered_without_blocking_the_caller(self):
        notifier = _FakeNotifier()
        dispatcher = AlertDispatcher(notifier, max_attempts=2, retry_base_seconds=0.01)
        queued = dispatcher.enqueue("1001", "offline", now=100)
        self.assertEqual(queued["status"], "queued")

        worker = await self._deliver_all(dispatcher)
        try:
            status = dispatcher.get_extension_status("1001", "offline")
            self.assertEqual(status["status"], "sent")
            self.assertEqual(status["sent_count"], 1)
            self.assertEqual(notifier.deliveries[0][1:3], ("1001", "offline"))
        finally:
            await self._stop_worker(worker)

    async def test_history_survives_restart_and_duplicate_state_is_not_resent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            store = AlertStore(path)
            store.initialize()
            notifier = _FakeNotifier()
            first_dispatcher = AlertDispatcher(notifier, store=store, retry_base_seconds=0.01)
            first = first_dispatcher.enqueue("1001", "offline", now=100)
            worker = await self._deliver_all(first_dispatcher)
            await self._stop_worker(worker)
            store.close()

            restarted_store = AlertStore(path)
            restarted_store.initialize()
            restarted_notifier = _FakeNotifier()
            restarted = AlertDispatcher(restarted_notifier, store=restarted_store)
            try:
                duplicate = restarted.enqueue("1001", "offline", now=1000)
                self.assertEqual(duplicate["id"], first["id"])
                self.assertTrue(duplicate["deduplicated"])
                self.assertEqual(duplicate["status"], "sent")
                self.assertTrue(restarted._queue.empty())
                self.assertEqual(restarted_notifier.deliveries, [])
            finally:
                restarted_store.close()

    async def test_pending_delivery_is_recovered_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            store = AlertStore(path)
            store.initialize()
            first = AlertDispatcher(_FakeNotifier(), store=store)
            queued = first.enqueue("1002", "offline", now=200)
            store.close()

            restarted_store = AlertStore(path)
            restarted_store.initialize()
            notifier = _FakeNotifier()
            restarted = AlertDispatcher(notifier, store=restarted_store, retry_base_seconds=0.01)
            worker = await self._deliver_all(restarted)
            try:
                recovered = restarted.recent_events(1)[0]
                self.assertEqual(recovered["id"], queued["id"])
                self.assertEqual(recovered["status"], "sent")
                self.assertEqual(len(notifier.deliveries), 1)
            finally:
                await self._stop_worker(worker)
                restarted_store.close()

    async def test_manual_test_uses_cooldown_and_is_identified_as_test(self):
        notifier = _FakeNotifier()
        dispatcher = AlertDispatcher(notifier, test_cooldown_seconds=60)
        first = dispatcher.enqueue_test(now=100)
        duplicate = dispatcher.enqueue_test(now=130)
        later = dispatcher.enqueue_test(now=161)

        self.assertEqual(first["kind"], "test")
        self.assertEqual(duplicate["id"], first["id"])
        self.assertTrue(duplicate["deduplicated"])
        self.assertNotEqual(later["id"], first["id"])

        worker = await self._deliver_all(dispatcher)
        try:
            self.assertTrue(all(delivery[-1] for delivery in notifier.deliveries))
        finally:
            await self._stop_worker(worker)


if __name__ == "__main__":
    unittest.main()
