"""Rastreia o estado online/offline de cada ramal com debounce, para evitar alarme falso
em quedas rapidas (ex.: MicroSIP reiniciando)."""
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class _ExtensionState:
    confirmed_online: bool
    confirmed_since: float
    pending_online: bool
    pending_since: float
    last_seen_at: float


class StateTracker:
    def __init__(self, debounce_seconds: float, history_limit: int = 200):
        self._debounce = debounce_seconds
        self._states: dict[str, _ExtensionState] = {}
        self._history: deque[dict] = deque(maxlen=history_limit)

    def update(self, extension: str, online: bool, now: float | None = None) -> None:
        """Registra a leitura mais recente (evento ou reconciliacao). Nao dispara alerta
        diretamente - isso e feito por tick(), depois que o debounce confirmar a mudanca."""
        now = now if now is not None else time.time()
        state = self._states.get(extension)
        if state is None:
            # Primeira vez que vemos este ramal: vira o baseline, sem alerta.
            self._states[extension] = _ExtensionState(
                confirmed_online=online,
                confirmed_since=now,
                pending_online=online,
                pending_since=now,
                last_seen_at=now,
            )
            return
        state.last_seen_at = now
        if state.pending_online != online:
            state.pending_online = online
            state.pending_since = now

    def tick(self, now: float | None = None) -> list[tuple[str, str]]:
        """Confirma mudancas que ja passaram do tempo de debounce. Retorna lista de
        (ramal, "online"|"offline") para cada transicao confirmada nesta chamada."""
        now = now if now is not None else time.time()
        changes: list[tuple[str, str]] = []
        for extension, state in self._states.items():
            if state.confirmed_online != state.pending_online and now - state.pending_since >= self._debounce:
                previous_online = state.confirmed_online
                previous_since = state.confirmed_since
                state.confirmed_online = state.pending_online
                state.confirmed_since = now
                status = "online" if state.pending_online else "offline"
                changes.append((extension, status))
                self._history.appendleft(
                    {
                        "extension": extension,
                        "status": status,
                        "at": now,
                        "previous_status": "online" if previous_online else "offline",
                        "previous_duration_seconds": max(0, now - previous_since),
                    }
                )
        return changes

    def known_extensions(self) -> list[str]:
        return list(self._states.keys())

    def retain_extensions(self, allowed_extensions) -> list[str]:
        """Remove estados fora da lista autoritativa e devolve os ramais removidos."""
        allowed = {str(extension) for extension in allowed_extensions}
        removed = sorted(extension for extension in self._states if extension not in allowed)
        for extension in removed:
            self._states.pop(extension, None)
        return removed

    def snapshot(self, now: float | None = None) -> list[dict]:
        """Retorna o estado confirmado e, quando houver, a transicao ainda em validacao."""
        now = now if now is not None else time.time()
        return [
            {
                "extension": extension,
                "online": state.confirmed_online,
                "since": state.confirmed_since,
                "last_seen_at": state.last_seen_at,
                "pending_status": (
                    ("online" if state.pending_online else "offline")
                    if state.pending_online != state.confirmed_online
                    else None
                ),
                "pending_since": state.pending_since if state.pending_online != state.confirmed_online else None,
                "confirmation_remaining_seconds": (
                    max(0, self._debounce - (now - state.pending_since))
                    if state.pending_online != state.confirmed_online
                    else 0
                ),
            }
            for extension, state in sorted(self._states.items())
        ]

    def recent_events(self, limit: int = 12) -> list[dict]:
        """Devolve transicoes confirmadas desde o inicio do processo (mais recente primeiro)."""
        return [dict(event) for event in list(self._history)[:limit]]
