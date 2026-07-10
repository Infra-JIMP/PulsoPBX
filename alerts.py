"""Fila de entrega de alertas WhatsApp.

O monitor nunca espera pela rede da Meta para continuar consumindo eventos da AMI.
Cada destinatário recebe sua própria tentativa, evitando reenvio duplicado a quem já
recebeu a mensagem quando outro número falha.
"""
import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from notifier import WhatsAppNotifier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DeliveryJob:
    event_id: str
    recipient: str


class AlertDispatcher:
    def __init__(
        self,
        notifier: WhatsAppNotifier,
        max_attempts: int = 3,
        retry_base_seconds: float = 15,
        history_limit: int = 200,
    ):
        self._notifier = notifier
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._queue: asyncio.Queue[_DeliveryJob] = asyncio.Queue()
        self._events: deque[dict] = deque(maxlen=history_limit)
        self._events_by_id: dict[str, dict] = {}
        self._latest_by_extension: dict[str, dict] = {}

    def enqueue(self, extension: str, status: str, now: float | None = None) -> dict:
        """Registra uma transição confirmada e agenda uma entrega por destinatário."""
        now = now if now is not None else time.time()
        event = {
            "id": uuid.uuid4().hex,
            "extension": extension,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "deliveries": {
                recipient: {"status": "queued", "attempts": 0, "last_error": None}
                for recipient in self._notifier.recipients
            },
        }
        if len(self._events) == self._events.maxlen:
            expired = self._events[-1]
            self._events_by_id.pop(expired["id"], None)
        self._events.appendleft(event)
        self._events_by_id[event["id"]] = event
        self._latest_by_extension[extension] = event
        for recipient in event["deliveries"]:
            self._queue.put_nowait(_DeliveryJob(event["id"], recipient))
        logger.info("Alerta do ramal %s (%s) colocado na fila para %d destinatario(s)", extension, status, len(event["deliveries"]))
        return self._serialize(event)

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
        timestamp = datetime.fromtimestamp(event["created_at"]).strftime("%d/%m/%Y %H:%M:%S")
        try:
            await asyncio.to_thread(
                self._notifier.notify_recipient_change,
                job.recipient,
                event["extension"],
                event["status"],
                timestamp,
            )
        except Exception as exc:
            delivery["last_error"] = str(exc)
            event["updated_at"] = time.time()
            if delivery["attempts"] >= self._max_attempts:
                delivery["status"] = "failed"
                logger.error(
                    "Alerta do ramal %s para %s falhou apos %d tentativa(s): %s",
                    event["extension"], job.recipient, delivery["attempts"], exc,
                )
                return

            delivery["status"] = "retrying"
            delay = self._retry_base_seconds * (2 ** (delivery["attempts"] - 1))
            logger.warning(
                "Alerta do ramal %s para %s falhou; nova tentativa em %.0fs: %s",
                event["extension"], job.recipient, delay, exc,
            )
            asyncio.create_task(self._requeue_after(job, delay))
            return

        delivery["status"] = "sent"
        delivery["last_error"] = None
        event["updated_at"] = time.time()

    async def _requeue_after(self, job: _DeliveryJob, delay: float) -> None:
        await asyncio.sleep(delay)
        if job.event_id in self._events_by_id:
            await self._queue.put(job)

    def get_extension_status(self, extension: str, current_status: str) -> dict | None:
        event = self._latest_by_extension.get(extension)
        if event is None or event["status"] != current_status:
            return None
        return self._serialize(event)

    def recent_events(self, limit: int = 12) -> list[dict]:
        return [self._serialize(event) for event in list(self._events)[:limit]]

    def _serialize(self, event: dict) -> dict:
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
            "status": status,
            "change": event["status"],
            "created_at": event["created_at"],
            "updated_at": event["updated_at"],
            "sent_count": sent,
            "total_recipients": total,
            "failed_count": failed,
            "attempts": max((delivery["attempts"] for delivery in deliveries), default=0),
            "last_error": errors[0] if errors else None,
        }
