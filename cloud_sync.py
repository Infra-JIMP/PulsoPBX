"""Envio do status ao vivo para o painel em nuvem (pulsopbx.vercel.app).

Regras para nao repetir o consumo excessivo da versao antiga:
- envia somente o status atual (ramal, nome, setor, online/offline e desde
  quando). Nada de historico, e-mail, cadastro ou relatorio;
- compara em memoria a cada CLOUD_SYNC_SECONDS (padrao 30s) e so envia quando
  algo mudou. Cada envio custa um unico comando no Redis;
- sem mudancas, envia apenas um sinal de vida a cada
  CLOUD_SYNC_HEARTBEAT_SECONDS (padrao 10 min), para o painel saber que o
  monitor continua no ar;
- falhas de rede nao travam o monitor e nao geram repeticao em rajada.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

import requests

from profiles import load_profiles

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 10
MAX_BACKOFF_SECONDS = 600


def ami_status(client) -> str:
    if client is None:
        return "not_configured"
    if not client.connected:
        return "disconnected"
    return "connected" if client.last_reconcile_at is not None else "connecting"


class CloudSync:
    def __init__(
        self,
        tracker,
        client,
        url: str,
        token: str,
        interval_seconds: float = 30,
        heartbeat_seconds: float = 600,
        profile_loader=load_profiles,
        poster=None,
    ):
        self._tracker = tracker
        self._client = client
        self._endpoint = url.rstrip("/") + "/api/push"
        self._token = token
        self._interval = interval_seconds
        self._heartbeat = heartbeat_seconds
        self._profile_loader = profile_loader
        self._post = poster or requests.post
        self._last_fingerprint: str | None = None
        self._last_sent_at: float | None = None
        self._failures = 0
        self._next_attempt_at = 0.0
        self.sent_count = 0

    def build_snapshot(self, now: float | None = None) -> dict:
        now = now if now is not None else time.time()
        profiles = self._profile_loader()
        extensions = []
        for item in self._tracker.snapshot(now):
            profile = profiles.get(item["extension"], {})
            extensions.append(
                {
                    "extension": item["extension"],
                    "nome": profile.get("nome", ""),
                    "setor": profile.get("setor", ""),
                    "online": bool(item["online"]),
                    "since": round(float(item["since"]), 1),
                    "pending_status": item.get("pending_status"),
                    "pending_since": (
                        round(float(item["pending_since"]), 1)
                        if item.get("pending_since") is not None
                        else None
                    ),
                }
            )
        last_reconcile = getattr(self._client, "last_reconcile_at", None)
        return {
            "generated_at": round(now, 1),
            "ami_status": ami_status(self._client),
            # Arredondado ao minuto: a reconciliacao a cada 60s nao deve, sozinha,
            # provocar um envio novo.
            "last_reconcile_minute": int(last_reconcile // 60) if last_reconcile else None,
            "debounce_seconds": getattr(self._tracker, "debounce_seconds", None),
            "extensions": extensions,
        }

    @staticmethod
    def fingerprint(snapshot: dict) -> str:
        # O horario de geracao muda sempre; ele nao pode, sozinho, provocar envio.
        relevant = {
            key: value
            for key, value in snapshot.items()
            if key not in {"generated_at", "last_reconcile_minute"}
        }
        raw = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def sync_once(self, now: float | None = None) -> bool:
        """Envia se houve mudanca ou se venceu o sinal de vida. Retorna True se enviou."""
        now = now if now is not None else time.time()
        if now < self._next_attempt_at:
            return False
        snapshot = self.build_snapshot(now)
        fingerprint = self.fingerprint(snapshot)
        heartbeat_due = (
            self._last_sent_at is None or now - self._last_sent_at >= self._heartbeat
        )
        if fingerprint == self._last_fingerprint and not heartbeat_due:
            return False
        try:
            response = self._post(
                self._endpoint,
                json=snapshot,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as exc:
            self._failures += 1
            delay = min(MAX_BACKOFF_SECONDS, self._interval * (2 ** min(self._failures, 6)))
            self._next_attempt_at = now + delay
            if self._failures == 1 or self._failures % 10 == 0:
                logger.warning(
                    "Falha ao enviar status ao painel em nuvem (%d seguida(s)); nova tentativa em %.0fs: %s",
                    self._failures,
                    delay,
                    exc,
                )
            return False
        if self._failures:
            logger.info("Envio ao painel em nuvem restabelecido apos %d falha(s)", self._failures)
        self._failures = 0
        self._next_attempt_at = 0.0
        self._last_fingerprint = fingerprint
        self._last_sent_at = now
        self.sent_count += 1
        return True

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.sync_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Falha inesperada no envio ao painel em nuvem")
            await asyncio.sleep(self._interval)
