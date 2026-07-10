"""Cliente AMI do MikoPBX.

Estrategia:
- Em tempo real, consumimos os eventos `ExtensionStatus` da AMI: cada vez que um ramal
  registra/desregistra, o Asterisk emite esse evento com o estado atual do "hint"
  (Idle/InUse/... = online; Unavailable/Unknown = offline). Isso e instantaneo.
- Como rede de seguranca (e para o snapshot inicial), rodamos periodicamente a acao
  `ExtensionStateList`, que devolve o estado de todos os ramais de uma vez.

Obs.: usamos ExtensionStateList em vez de PJSIPShowEndpoints porque esta ultima exige
permissao de AMI mais alta (nesta versao do MikoPBX ela retorna "Permission denied" mesmo
com call/reporting/system-read). ExtensionStateList entrega o mesmo que precisamos (o ramal
esta registrado ou nao) com as permissoes que ja temos.
"""
import asyncio
import logging
import time
from typing import Awaitable, Callable

from panoramisk import Manager

logger = logging.getLogger(__name__)

OFFLINE_STATUS_TEXTS = {"unavailable", "unknown"}
ACTION_TIMEOUT_SECONDS = 15

SnapshotCallback = Callable[[str, bool], Awaitable[None]]


def _is_online(status_text: str | None) -> bool:
    return (status_text or "").strip().lower() not in OFFLINE_STATUS_TEXTS


class AmiClient:
    def __init__(self, host: str, port: int, username: str, secret: str, on_snapshot: SnapshotCallback):
        self._on_snapshot = on_snapshot
        self._manager = Manager(
            host=host,
            port=port,
            username=username,
            secret=secret,
            ping_delay=10,
            ping_interval=10,
            reconnect_timeout=2,
        )
        self._manager.on_connect = self._handle_connect
        self._manager.on_disconnect = self._handle_disconnect
        self._manager.register_event("ExtensionStatus", self._handle_extension_status)
        self._reconcile_task: asyncio.Task | None = None

        self.connected = False
        self.last_reconcile_at: float | None = None

    def _handle_connect(self, manager: Manager) -> None:
        logger.info("Conectado a AMI em %s:%s", manager.config["host"], manager.config["port"])
        self.connected = True
        # snapshot inicial assim que conecta
        self._reconcile_task = asyncio.ensure_future(self.reconcile())

    def _handle_disconnect(self, manager: Manager, exc: Exception) -> None:
        logger.warning("Desconectado da AMI (%s) - panoramisk vai tentar reconectar sozinho", exc)
        self.connected = False

    async def _handle_extension_status(self, manager: Manager, message) -> None:
        extension = message.Exten
        if extension:
            await self._on_snapshot(str(extension), _is_online(message.StatusText))

    async def reconcile(self) -> None:
        try:
            messages = await asyncio.wait_for(
                self._manager.send_action({"Action": "ExtensionStateList"}, as_list=True),
                timeout=ACTION_TIMEOUT_SECONDS,
            )
            for message in messages:
                if message.Event == "ExtensionStatus" and message.Exten:
                    await self._on_snapshot(str(message.Exten), _is_online(message.StatusText))
            self.last_reconcile_at = time.time()
        except asyncio.TimeoutError:
            logger.warning("Timeout aguardando ExtensionStateList da AMI")
        except Exception:
            logger.exception("Falha ao reconciliar estado dos ramais via AMI")

    async def connect(self) -> None:
        await self._manager.connect()

    async def periodic_reconcile(self, interval_seconds: float) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            if self.connected:
                await self.reconcile()

    def close(self) -> None:
        self._manager.close()
