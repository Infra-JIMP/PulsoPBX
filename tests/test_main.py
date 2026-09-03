import asyncio
import unittest

from main import maintain_ami_connection


class _BlockingClient:
    def __init__(self):
        self.started = asyncio.Event()

    async def connect(self):
        self.started.set()
        await asyncio.Event().wait()


class AmiStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_can_run_without_blocking_other_tasks(self):
        client = _BlockingClient()
        task = asyncio.create_task(maintain_ami_connection(client))
        await asyncio.wait_for(client.started.wait(), timeout=1)

        marker = await asyncio.wait_for(
            asyncio.sleep(0, result="painel-livre"), timeout=1
        )

        self.assertEqual(marker, "painel-livre")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
