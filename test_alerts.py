import asyncio
import unittest

from alerts import AlertDispatcher


class _FakeNotifier:
    recipients = ["5511999999999"]

    def __init__(self):
        self.deliveries = []

    def notify_recipient_change(self, recipient, extension, status, timestamp):
        self.deliveries.append((recipient, extension, status, timestamp))


class AlertDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_change_is_delivered_without_blocking_the_caller(self):
        notifier = _FakeNotifier()
        dispatcher = AlertDispatcher(notifier, max_attempts=2, retry_base_seconds=1)
        worker = asyncio.create_task(dispatcher.run())
        try:
            queued = dispatcher.enqueue("1001", "offline", now=100)
            self.assertEqual(queued["status"], "queued")
            await asyncio.wait_for(dispatcher._queue.join(), timeout=1)

            status = dispatcher.get_extension_status("1001", "offline")
            self.assertEqual(status["status"], "sent")
            self.assertEqual(status["sent_count"], 1)
            self.assertEqual(notifier.deliveries[0][1:3], ("1001", "offline"))
        finally:
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker


if __name__ == "__main__":
    unittest.main()
