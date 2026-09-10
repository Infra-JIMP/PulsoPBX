import asyncio
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from alert_store import AlertStore
from alerts import AlertDispatcher


class _FakeNotifier:
    recipients = ["email:ti@example.com"]

    def __init__(self, failures: int = 0):
        self.deliveries = []
        self.failures = failures

    def notify_recipient_change(
        self, recipient, extension, status, timestamp, is_test=False, context=None
    ):
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

    async def test_pending_delivery_for_removed_recipient_is_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AlertStore(Path(directory) / "alerts.db")
            store.initialize()
            dispatcher = AlertDispatcher(_FakeNotifier(), store=store)
            dispatcher.enqueue("1002", "offline", now=200)

            removed = store.fail_pending_for_removed_recipients({"email:novo@example.com"})
            event = AlertDispatcher.serialize_event(store.recent(1)[0])

            self.assertEqual(removed, 1)
            self.assertEqual(event["status"], "failed")
            self.assertEqual(event["last_error"], "Destinatario removido da configuracao")
            store.close()

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

    async def test_dynamic_recipient_and_context_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            store = AlertStore(path)
            store.initialize()
            notifier = _FakeNotifier()
            notifier.recipients = []
            notifier.can_deliver_recipient = lambda recipient: recipient.startswith("email:")
            dispatcher = AlertDispatcher(notifier, store=store)

            queued = dispatcher.enqueue(
                "1001",
                "offline",
                now=100,
                recipients=["email:ana@example.com"],
                context={"incident_id": 7, "nome": "Ana"},
            )
            stored = store.get(queued["id"])

            self.assertEqual(stored["context"]["incident_id"], 7)
            self.assertIn("email:ana@example.com", stored["deliveries"])
            store.close()

    async def test_same_status_is_allowed_for_a_new_incident(self):
        notifier = _FakeNotifier()
        dispatcher = AlertDispatcher(notifier)

        first = dispatcher.enqueue("1001", "offline", now=100, context={"incident_id": 1})
        second = dispatcher.enqueue("1001", "offline", now=200, context={"incident_id": 2})

        self.assertNotEqual(first["id"], second["id"])

    async def test_delivery_expires_when_working_interval_has_ended(self):
        notifier = _FakeNotifier()
        dispatcher = AlertDispatcher(notifier)
        dispatcher.enqueue(
            "1001",
            "offline",
            context={"delivery_deadline": time.time() - 1},
        )

        worker = await self._deliver_all(dispatcher)
        try:
            status = dispatcher.get_extension_status("1001", "offline")
            self.assertEqual(status["status"], "failed")
            self.assertEqual(
                status["last_error"],
                "Entrega expirada no fim do expediente",
            )
            self.assertEqual(notifier.deliveries, [])
        finally:
            await self._stop_worker(worker)

    async def test_pending_delivery_is_cancelled_when_extension_is_paused(self):
        notifier = _FakeNotifier()
        dispatcher = AlertDispatcher(notifier)
        dispatcher.enqueue("1010", "offline", now=100)

        cancelled = dispatcher.cancel_pending_for_extension(
            "1010", "Monitoramento temporariamente pausado"
        )
        status = dispatcher.get_extension_status("1010", "offline")

        self.assertEqual(cancelled, 1)
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["last_error"], "Monitoramento temporariamente pausado")


class DailyAlertLimitTests(unittest.IsolatedAsyncioTestCase):
    """Ramais instaveis param de gerar e-mail, mas continuam no historico."""

    @staticmethod
    def _today_at(hour: int) -> float:
        # Ancora no dia corrente para que a restauracao do limite (que usa a data
        # de hoje) enxergue os eventos gravados pelo teste.
        return datetime.now().replace(
            hour=hour, minute=0, second=0, microsecond=0
        ).timestamp()

    async def _drain(self, dispatcher: AlertDispatcher) -> None:
        worker = asyncio.create_task(dispatcher.run())
        try:
            await asyncio.wait_for(dispatcher._queue.join(), timeout=1)
        finally:
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker

    async def test_flapping_extension_stops_sending_after_the_daily_limit(self):
        notifier = _FakeNotifier()
        dispatcher = AlertDispatcher(notifier, daily_limit_per_extension=2)
        base = self._today_at(9)

        first = dispatcher.enqueue("1001", "offline", now=base)
        second = dispatcher.enqueue("1001", "online", now=base + 60)
        third = dispatcher.enqueue("1001", "offline", now=base + 120)
        fourth = dispatcher.enqueue("1001", "online", now=base + 180)

        # Os dois primeiros entram na fila; os seguintes ja nascem suprimidos.
        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "queued")
        # Passado o teto, o evento existe e fica no historico, sem entrega.
        self.assertEqual(third["status"], "suppressed_daily_limit")
        self.assertEqual(third["notification_suppressed"], "daily_limit")
        self.assertEqual(third["total_recipients"], 0)
        self.assertEqual(fourth["status"], "suppressed_daily_limit")

        await self._drain(dispatcher)

        # Somente os dois primeiros viraram e-mail.
        self.assertEqual(len(notifier.deliveries), 2)
        self.assertEqual(
            [delivery[1:3] for delivery in notifier.deliveries],
            [("1001", "offline"), ("1001", "online")],
        )
        self.assertEqual(dispatcher.daily_notification_count("1001", base), 2)
        # Os quatro eventos continuam disponiveis para os relatorios.
        self.assertEqual(len(dispatcher.recent_events(limit=10)), 4)

    async def test_limit_is_per_extension_and_resets_on_the_next_day(self):
        notifier = _FakeNotifier()
        dispatcher = AlertDispatcher(notifier, daily_limit_per_extension=2)
        base = self._today_at(9)

        dispatcher.enqueue("1001", "offline", now=base)
        dispatcher.enqueue("1001", "online", now=base + 60)
        blocked = dispatcher.enqueue("1001", "offline", now=base + 120)
        # Outro ramal tem orcamento proprio.
        other = dispatcher.enqueue("2002", "offline", now=base + 130)
        # No dia seguinte o ramal volta a notificar.
        tomorrow = dispatcher.enqueue("1001", "online", now=base + 86_400)

        await self._drain(dispatcher)

        self.assertEqual(blocked["status"], "suppressed_daily_limit")
        # Ramal vizinho e o dia seguinte seguem notificando normalmente.
        self.assertEqual(other["status"], "queued")
        self.assertEqual(tomorrow["status"], "queued")
        self.assertEqual(
            [delivery[1:3] for delivery in notifier.deliveries],
            [
                ("1001", "offline"),
                ("1001", "online"),
                ("2002", "offline"),
                ("1001", "online"),
            ],
        )

    async def test_limit_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            store = AlertStore(path)
            store.initialize()
            notifier = _FakeNotifier()
            base = self._today_at(9)

            first = AlertDispatcher(
                notifier, store=store, daily_limit_per_extension=2, retry_base_seconds=0.01
            )
            first.enqueue("1001", "offline", now=base)
            first.enqueue("1001", "online", now=base + 60)
            await self._drain(first)
            store.close()

            # O monitor reinicia no mesmo dia: o teto ja foi consumido.
            resumed_store = AlertStore(path)
            resumed_store.initialize()
            resumed_notifier = _FakeNotifier()
            resumed = AlertDispatcher(
                resumed_notifier,
                store=resumed_store,
                daily_limit_per_extension=2,
                retry_base_seconds=0.01,
            )
            try:
                self.assertEqual(resumed.daily_notification_count("1001", base), 2)
                blocked = resumed.enqueue("1001", "offline", now=base + 120)
                await self._drain(resumed)

                self.assertEqual(blocked["status"], "suppressed_daily_limit")
                self.assertEqual(len(notifier.deliveries), 2)
                self.assertEqual(resumed_notifier.deliveries, [])
            finally:
                resumed_store.close()

    async def test_welcome_and_missed_call_do_not_consume_the_limit(self):
        notifier = _FakeNotifier()
        dispatcher = AlertDispatcher(notifier, daily_limit_per_extension=2)
        base = self._today_at(9)
        target = ["email:ti@example.com"]

        dispatcher.enqueue_welcome("1001", target, now=base)
        dispatcher.enqueue_missed_call("1001", target, now=base + 30)
        first = dispatcher.enqueue("1001", "offline", now=base + 60)
        second = dispatcher.enqueue("1001", "online", now=base + 90)

        await self._drain(dispatcher)

        # Boas-vindas e chamada perdida nao sao alarmes de conexao: os dois
        # avisos de queda/retorno seguintes ainda cabem no teto do dia.
        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "queued")
        self.assertEqual(dispatcher.daily_notification_count("1001", base), 2)
        self.assertEqual(len(notifier.deliveries), 4)

    async def test_zero_disables_the_limit(self):
        notifier = _FakeNotifier()
        dispatcher = AlertDispatcher(notifier, daily_limit_per_extension=0)
        base = self._today_at(9)

        for index in range(6):
            status = "offline" if index % 2 == 0 else "online"
            dispatcher.enqueue("1001", status, now=base + index * 60)

        await self._drain(dispatcher)

        self.assertEqual(len(notifier.deliveries), 6)


if __name__ == "__main__":
    unittest.main()
