"""Regras de notificacao individual com tolerancia, agenda e supressao coletiva."""

from __future__ import annotations

import asyncio
import logging
import time

from profiles import notification_target


logger = logging.getLogger(__name__)


class ResponsibleAlertScheduler:
    """Transforma incidentes confirmados em e-mails seguros para o responsavel."""

    def __init__(
        self,
        store,
        calendar,
        tracker,
        alerts,
        client,
        delay_seconds: float = 120,
        mass_outage_threshold: int = 5,
        mass_outage_window_seconds: float = 60,
        profile_resolver=notification_target,
        poll_seconds: float = 5,
    ):
        self._store = store
        self._calendar = calendar
        self._tracker = tracker
        self._alerts = alerts
        self._client = client
        self._delay_seconds = delay_seconds
        self._mass_outage_threshold = mass_outage_threshold
        self._mass_outage_window_seconds = mass_outage_window_seconds
        self._profile_resolver = profile_resolver
        self._poll_seconds = poll_seconds

    async def schedule_transition(
        self,
        extension: str,
        status: str,
        incident: dict | None,
        now: float | None = None,
    ) -> None:
        if incident is None:
            return
        now = now if now is not None else time.time()
        if status == "offline":
            due_at = max(now, float(incident["opened_at"]) + self._delay_seconds)
            await asyncio.to_thread(
                self._store.schedule_offline,
                int(incident["id"]),
                str(extension),
                float(incident["opened_at"]),
                due_at,
            )
            logger.info(
                "Aviso do ramal %s aguardara %.0fs de tolerancia",
                extension,
                max(0, due_at - now),
            )
            return

        job = await asyncio.to_thread(self._store.get_job, int(incident["id"]))
        if job is None:
            return
        if job["status"] == "pending":
            await asyncio.to_thread(
                self._store.update_job,
                int(incident["id"]),
                "cancelled",
                "reconnected_before_notification",
            )
            logger.info(
                "Aviso do ramal %s cancelado porque reconectou antes do envio",
                extension,
            )
        elif job["status"] == "dispatched":
            await asyncio.to_thread(
                self._store.update_job,
                int(incident["id"]),
                "recovered_pending",
                "checking_offline_delivery",
            )

    async def run(self) -> None:
        while True:
            try:
                await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Falha ao processar avisos dos responsaveis")
            await asyncio.sleep(self._poll_seconds)

    async def process_once(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        if not self._monitor_ready():
            return
        due_jobs = await asyncio.to_thread(self._store.due_jobs, now)
        for job in due_jobs:
            await self._process_due_job(job, now)
        dispatched = await asyncio.to_thread(
            self._store.jobs_with_status, "dispatched"
        )
        current_states = {
            str(item["extension"]): item for item in self._tracker.snapshot(now)
        }
        for job in dispatched:
            state = current_states.get(str(job["extension"]))
            if state is not None and state["online"]:
                await asyncio.to_thread(
                    self._store.update_job,
                    int(job["incident_id"]),
                    "recovered_pending",
                    "recovered_state_reconciled",
                )
        recoveries = await asyncio.to_thread(
            self._store.jobs_with_status, "recovered_pending"
        )
        for job in recoveries:
            await self._process_recovery(job, now)

    def _monitor_ready(self) -> bool:
        if self._client is None:
            return True
        return bool(self._client.connected and self._client.last_reconcile_at is not None)

    async def _process_due_job(self, job: dict, now: float) -> None:
        incident_id = int(job["incident_id"])
        extension = str(job["extension"])
        state = next(
            (
                item
                for item in self._tracker.snapshot(now)
                if str(item["extension"]) == extension
            ),
            None,
        )
        if state is None:
            # Durante a inicializacao o snapshot pode ainda estar incompleto. O job
            # permanece pendente e sera reavaliado apos a proxima reconciliacao.
            return
        if state["online"] or state.get("pending_status") == "online":
            await self._suppress(incident_id, "reconnected_before_notification")
            return
        if not self._calendar.configured:
            await self._suppress(incident_id, "calendar_not_configured")
            return
        if not self._calendar.is_working_time(float(job["offline_at"])):
            await self._suppress(incident_id, "outside_working_hours")
            return
        if not self._calendar.is_working_time(now):
            await self._suppress(incident_id, "outside_working_hours_at_delivery")
            return
        cohort = await asyncio.to_thread(
            self._store.cohort_size,
            float(job["offline_at"]),
            self._mass_outage_window_seconds,
        )
        if cohort >= self._mass_outage_threshold:
            await self._suppress(incident_id, "collective_outage")
            logger.warning(
                "Aviso individual do ramal %s suprimido: %d quedas na mesma janela",
                extension,
                cohort,
            )
            return
        target, profile = self._profile_resolver(extension)
        if target is None:
            await self._suppress(incident_id, "responsible_email_missing")
            return
        if self._alerts is None:
            await self._suppress(incident_id, "email_channel_not_configured")
            return

        context = {
            "incident_id": incident_id,
            "nome": profile.get("nome", ""),
            "setor": profile.get("setor", ""),
            "offline_at": float(job["offline_at"]),
        }
        event = self._alerts.enqueue(
            extension,
            "offline",
            now=now,
            recipients=[target],
            context=context,
        )
        await asyncio.to_thread(
            self._store.update_job,
            incident_id,
            "dispatched",
            "offline_notification_queued",
            target,
            event["id"],
        )

    async def _process_recovery(self, job: dict, now: float) -> None:
        incident_id = int(job["incident_id"])
        if self._alerts is None or not job.get("alert_event_id"):
            await asyncio.to_thread(
                self._store.update_job,
                incident_id,
                "completed",
                "offline_notification_unavailable",
            )
            return
        offline_event = self._alerts.get_event(job["alert_event_id"])
        if offline_event is None:
            await asyncio.to_thread(
                self._store.update_job,
                incident_id,
                "completed",
                "offline_notification_not_found",
            )
            return
        if offline_event["sent_count"]:
            _, profile = self._profile_resolver(str(job["extension"]))
            event = self._alerts.enqueue(
                str(job["extension"]),
                "online",
                now=now,
                recipients=[job["target"]],
                context={
                    "incident_id": incident_id,
                    "nome": profile.get("nome", ""),
                    "setor": profile.get("setor", ""),
                    "duration_seconds": max(0, now - float(job["offline_at"])),
                },
            )
            await asyncio.to_thread(
                self._store.update_job,
                incident_id,
                "completed",
                f"return_notification_queued:{event['id']}",
            )
            return
        if offline_event["status"] == "failed":
            await asyncio.to_thread(
                self._store.update_job,
                incident_id,
                "completed",
                "return_skipped_without_offline_email",
            )
            return
        cancelled = self._alerts.cancel_event(
            job["alert_event_id"],
            "Ramal reconectou antes da entrega do aviso de queda",
        )
        if cancelled and cancelled["status"] == "failed":
            await asyncio.to_thread(
                self._store.update_job,
                incident_id,
                "completed",
                "return_skipped_without_offline_email",
            )

    async def _suppress(self, incident_id: int, reason: str) -> None:
        await asyncio.to_thread(
            self._store.update_job,
            incident_id,
            "suppressed",
            reason,
        )
