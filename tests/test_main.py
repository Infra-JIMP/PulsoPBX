import asyncio
import tempfile
import unittest
from pathlib import Path

from directory import DirectoryStore
from main import _apply_employee_snapshot, maintain_ami_connection
from state import StateTracker


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

    async def test_directory_is_used_when_mikopbx_snapshot_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = DirectoryStore(Path(temporary) / "pulsopbx.db")
            try:
                directory.initialize()
                directory.save_person(
                    {
                        "name": "Pessoa Conhecida",
                        "role": "Analista",
                        "sector": "T.I.",
                        "extension": "8008",
                        "email": "pessoa@example.com",
                        "active": True,
                        "notify": True,
                    }
                )
                tracker = StateTracker(30)
                tracker.update("8008", True)
                tracker.update("2200100", True)

                await _apply_employee_snapshot(tracker, None, {}, directory=directory)

                self.assertEqual(
                    [item["extension"] for item in tracker.snapshot()],
                    ["8008"],
                )
            finally:
                directory.close()


if __name__ == "__main__":
    unittest.main()
