"""Fila resiliente de entrega de alertas por e-mail."""
import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from alert_store import AlertStore
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DeliveryJob:
    event_id: str
    recipient: str


class AlertDispatcher:
    """Entrega alertas sem bloquear a AMI e preserva o estado no SQLite."""

    def __init__(
        self,
        notifier,
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

    @property
    def can_send_test(self) -> bool:
        return bool(self._notifier.recipients)

    def enqueue(
        self,
        extension: str,
        status: str,
        now: float | None = None,
        recipients: list[str] | None = None,
        context: dict | None = None,
    ) -> dict:
        """Agenda uma mudanca confirmada, ignorando repeticoes do mesmo estado."""
        if status not in {"online", "offline"}:
            raise ValueError(f"Status de ramal invalido: {status}")
        now = now if now is not None else time.time()
        context = dict(context or {})
        previous = self._latest_by_extension.get(extension)
        previous_incident = (previous or {}).get("context", {}).get("incident_id")
        current_incident = context.get("incident_id")
        same_incident = current_incident is None or previous_incident == current_incident
        if previous is not None and previous["change"] == status and same_incident:
            result = self._serialize(previous)
            result["deduplicated"] = True
            logger.info("Alerta duplicado do ramal %s (%s) ignorado", extension, status)
            return result

        event = self._create_event(
            extension,
            status,
            "status",
            now,
            recipients=recipients,
            context=context,
        )
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

    def _create_event(
        self,
        extension: str,
        change: str,
        kind: str,
        now: float,
        recipients: list[str] | None = None,
        context: dict | None = None,
    ) -> dict:
        selected_recipients = list(
            dict.fromkeys(self._notifier.recipients if recipients is None else recipients)
        )
        configured_recipients = set(self._notifier.recipients)
        can_deliver = getattr(
            self._notifier,
            "can_deliver_recipient",
            configured_recipients.__contains__,
        )
        invalid = [
            recipient
            for recipient in selected_recipients
            if not can_deliver(recipient)
        ]
        if invalid:
            raise ValueError(f"Destino(s) de notificacao invalido(s): {', '.join(invalid)}")
        event = {
            "id": uuid.uuid4().hex,
            "extension": extension,
            "change": change,
            "kind": kind,
            "context": dict(context or {}),
            "created_at": now,
            "updated_at": now,
            "deliveries": {
                recipient: {"status": "queued", "attempts": 0, "last_error": None}
                for recipient in selected_recipients
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

        delivery_deadline = event.get("context", {}).get("delivery_deadline")
        if (
            isinstance(delivery_deadline, (int, float))
            and not isinstance(delivery_deadline, bool)
            and time.time() >= float(delivery_deadline)
        ):
            delivery["status"] = "failed"
            delivery["last_error"] = "Entrega expirada no fim do expediente"
            event["updated_at"] = time.time()
            self._persist_delivery(event, job.recipient)
            logger.info(
                "Alerta %s para %s suprimido porque o expediente terminou",
                event["extension"],
                job.recipient,
            )
            return

        delivery["status"] = "sending"
        delivery["attempts"] += 1
        event["updated_at"] = time.time()
        self._persist_delivery(event, job.recipient)
        message_timestamp = event["created_at"]
        if event["change"] == "offline":
            message_timestamp = event.get("context", {}).get("offline_at", message_timestamp)
        timestamp = datetime.fromtimestamp(message_timestamp).strftime("%d/%m/%Y %H:%M:%S")
        try:
            await asyncio.to_thread(
                self._notifier.notify_recipient_change,
                job.recipient,
                event["extension"],
                event["change"],
                timestamp,
                event["kind"] == "test",
                event.get("context") or {},
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

    def get_event(self, event_id: str | None) -> dict | None:
        if not event_id:
            return None
        event = self._events_by_id.get(event_id)
        if event is None and self._store is not None:
            try:
                event = self._store.get(event_id)
            except Exception:
                logger.exception("Nao foi possivel consultar o alerta %s", event_id)
        return self._serialize(event) if event is not None else None

    def cancel_event(self, event_id: str, reason: str) -> dict | None:
        """Cancela entregas ainda nao iniciadas; uma entrega em curso termina normalmente."""
        event = self._events_by_id.get(event_id)
        if event is None:
            return self.get_event(event_id)
        changed = False
        for recipient, delivery in event["deliveries"].items():
            if delivery["status"] not in {"queued", "retrying"}:
                continue
            delivery["status"] = "failed"
            delivery["last_error"] = reason
            event["updated_at"] = time.time()
            self._persist_delivery(event, recipient)
            changed = True
        if changed:
            logger.info("Alerta %s cancelado: %s", event_id, reason)
        return self._serialize(event)

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
        can_deliver = getattr(
            self._notifier,
            "can_deliver_recipient",
            configured_recipients.__contains__,
        )
        for event in reversed(stored):
            for recipient, delivery in event["deliveries"].items():
                if delivery["status"] in {"sent", "failed"}:
                    continue
                if not can_deliver(recipient):
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
