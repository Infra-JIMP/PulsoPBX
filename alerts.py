"""Fila resiliente de entrega de alertas WhatsApp."""
import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from alert_store import AlertStore
from notifier import WhatsAppNotifier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DeliveryJob:
    event_id: str
    recipient: str


class AlertDispatcher:
    """Entrega alertas sem bloquear a AMI e preserva o estado no SQLite."""

    def __init__(
        self,
        notifier: WhatsAppNotifier,
        max_attempts: int = 3,
        retry_base_seconds: float = 15,
        history_limit: int = 200,
        store: AlertStore | None = None,
        test_cooldown_seconds: float = 60,
    ):
        self._notifier = notifier
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._history_limit = history_limit
        self._store = store
        self._test_cooldown_seconds = test_cooldown_seconds
        self._queue: asyncio.Queue[_DeliveryJob] = asyncio.Queue()
        self._events: deque[dict] = deque(maxlen=history_limit)
        self._events_by_id: dict[str, dict] = {}
        self._latest_by_extension: dict[str, dict] = {}
        self._latest_test_event: dict | None = None
        self._restore_history()

    def enqueue(self, extension: str, status: str, now: float | None = None) -> dict:
        """Agenda uma mudanca confirmada, ignorando repeticoes do mesmo estado."""
        if status not in {"online", "offline"}:
            raise ValueError(f"Status de ramal invalido: {status}")
        now = now if now is not None else time.time()
        previous = self._latest_by_extension.get(extension)
        if previous is not None and previous["change"] == status:
            result = self._serialize(previous)
            result["deduplicated"] = True
            logger.info("Alerta duplicado do ramal %s (%s) ignorado", extension, status)
            return result

        event = self._create_event(extension, status, "status", now)
        self._latest_by_extension[extension] = event
        return self._serialize(event)

    def enqueue_test(self, now: float | None = None) -> dict:
        """Envia um teste pelo mesmo caminho usado pelos alertas reais."""
        now = now if now is not None else time.time()
        if (
            self._latest_test_event is not None
            and now - self._latest_test_event["created_at"] < self._test_cooldown_seconds
        ):
            result = self._serialize(self._latest_test_event)
            result["deduplicated"] = True
            return result

        event = self._create_event("TESTE", "test", "test", now)
        self._latest_test_event = event
        return self._serialize(event)

    def _create_event(self, extension: str, change: str, kind: str, now: float) -> dict:
        event = {
            "id": uuid.uuid4().hex,
            "extension": extension,
            "change": change,
            "kind": kind,
            "created_at": now,
            "updated_at": now,
            "deliveries": {
                recipient: {"status": "queued", "attempts": 0, "last_error": None}
                for recipient in self._notifier.recipients
            },
        }
        self._remember(event)
        self._persist_event(event)
        for recipient in event["deliveries"]:
            self._queue.put_nowait(_DeliveryJob(event["id"], recipient))
        logger.info(
            "Alerta %s (%s) colocado na fila para %d destinatario(s)",
            extension,
            change,
            len(event["deliveries"]),
        )
        return event

    async def run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._deliver(job)
            finally:
                self._queue.task_done()

    async def _deliver(self, job: _DeliveryJob) -> None:
        event = self._events_by_id.get(job.event_id)
        if event is None:
            return
        delivery = event["deliveries"].get(job.recipient)
        if delivery is None or delivery["status"] in {"sent", "failed"}:
            return

        delivery["status"] = "sending"
        delivery["attempts"] += 1
        event["updated_at"] = time.time()
        self._persist_delivery(event, job.recipient)
        timestamp = datetime.fromtimestamp(event["created_at"]).strftime("%d/%m/%Y %H:%M:%S")
        try:
            await asyncio.to_thread(
                self._notifier.notify_recipient_change,
                job.recipient,
                event["extension"],
                event["change"],
                timestamp,
                event["kind"] == "test",
            )
        except Exception as exc:
            delivery["last_error"] = str(exc)
            event["updated_at"] = time.time()
            if delivery["attempts"] >= self._max_attempts:
                delivery["status"] = "failed"
                self._persist_delivery(event, job.recipient)
                logger.error(
                    "Alerta %s para %s falhou apos %d tentativa(s): %s",
                    event["extension"],
                    job.recipient,
                    delivery["attempts"],
                    exc,
                )
                return

            delivery["status"] = "retrying"
            self._persist_delivery(event, job.recipient)
            delay = self._retry_base_seconds * (2 ** (delivery["attempts"] - 1))
            logger.warning(
                "Alerta %s para %s falhou; nova tentativa em %.0fs: %s",
                event["extension"],
                job.recipient,
                delay,
                exc,
            )
            asyncio.create_task(self._requeue_after(job, delay))
            return

        delivery["status"] = "sent"
        delivery["last_error"] = None
        event["updated_at"] = time.time()
        self._persist_delivery(event, job.recipient)

    async def _requeue_after(self, job: _DeliveryJob, delay: float) -> None:
        await asyncio.sleep(delay)
        if job.event_id in self._events_by_id:
            await self._queue.put(job)

    def get_extension_status(self, extension: str, current_status: str) -> dict | None:
        event = self._latest_by_extension.get(extension)
        if event is None or event["change"] != current_status:
            return None
        return self._serialize(event)

    def recent_events(self, limit: int = 12) -> list[dict]:
        return [self._serialize(event) for event in list(self._events)[:limit]]

    def _restore_history(self) -> None:
        if self._store is None:
            return
        try:
            stored = self._store.recent(self._history_limit)
            latest = self._store.latest_status_by_extension()
        except Exception:
            logger.exception("Nao foi possivel restaurar o historico de alertas")
            return

        self._events.extend(stored)
        self._events_by_id = {event["id"]: event for event in stored}
        self._latest_by_extension = {
            extension: self._events_by_id.get(event["id"], event)
            for extension, event in latest.items()
        }
        self._latest_test_event = next((event for event in stored if event["kind"] == "test"), None)

        recovered = 0
        configured_recipients = set(self._notifier.recipients)
        for event in reversed(stored):
            for recipient, delivery in event["deliveries"].items():
                if delivery["status"] in {"sent", "failed"}:
                    continue
                if recipient not in configured_recipients:
                    delivery["status"] = "failed"
                    delivery["last_error"] = "Destinatario removido da configuracao"
                    event["updated_at"] = time.time()
                    self._persist_delivery(event, recipient)
                    continue
                if delivery["attempts"] >= self._max_attempts:
                    delivery["status"] = "failed"
                    delivery["last_error"] = delivery["last_error"] or "Tentativas esgotadas antes do reinicio"
                    event["updated_at"] = time.time()
                    self._persist_delivery(event, recipient)
                    continue
                delivery["status"] = "queued"
                event["updated_at"] = time.time()
                self._persist_delivery(event, recipient)
                self._queue.put_nowait(_DeliveryJob(event["id"], recipient))
                recovered += 1
        if recovered:
            logger.warning("%d entrega(s) pendente(s) restaurada(s) do historico", recovered)

    def _remember(self, event: dict) -> None:
        if len(self._events) == self._events.maxlen:
            expired = self._events[-1]
            self._events_by_id.pop(expired["id"], None)
        self._events.appendleft(event)
        self._events_by_id[event["id"]] = event

    def _persist_event(self, event: dict) -> None:
        if self._store is None:
            return
        try:
            self._store.create_event(event)
        except Exception:
            logger.exception("Falha ao persistir o alerta %s", event["id"])

    def _persist_delivery(self, event: dict, recipient: str) -> None:
        if self._store is None:
            return
        try:
            self._store.update_delivery(event, recipient)
        except Exception:
            logger.exception("Falha ao persistir a entrega do alerta %s", event["id"])

    @staticmethod
    def serialize_event(event: dict) -> dict:
        return AlertDispatcher._serialize(event)

    @staticmethod
    def _serialize(event: dict) -> dict:
        deliveries = list(event["deliveries"].values())
        total = len(deliveries)
        sent = sum(delivery["status"] == "sent" for delivery in deliveries)
        failed = sum(delivery["status"] == "failed" for delivery in deliveries)
        retrying = sum(delivery["status"] == "retrying" for delivery in deliveries)
        sending = sum(delivery["status"] == "sending" for delivery in deliveries)
        if total == 0:
            status = "not_configured"
        elif sent == total:
            status = "sent"
        elif failed and sent + failed == total:
            status = "partial_failure" if sent else "failed"
        elif retrying:
            status = "retrying"
        elif sending:
            status = "sending"
        else:
            status = "queued"
        errors = [delivery["last_error"] for delivery in deliveries if delivery["last_error"]]
        return {
            "id": event["id"],
            "extension": event["extension"],
            "kind": event["kind"],
            "status": status,
            "change": event["change"],
            "created_at": event["created_at"],
            "updated_at": event["updated_at"],
            "sent_count": sent,
            "total_recipients": total,
            "failed_count": failed,
            "attempts": max((delivery["attempts"] for delivery in deliveries), default=0),
            "last_error": errors[0] if errors else None,
        }
