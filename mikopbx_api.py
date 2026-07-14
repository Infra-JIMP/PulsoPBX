"""Busca o nome dos funcionarios vinculados a cada ramal via API REST v3 do MikoPBX
(GET /employees). Mantem um cache em memoria atualizado periodicamente - se a chamada
falhar (rede, chave invalida, etc.), mantem o ultimo resultado valido em vez de apagar
os nomes ja exibidos no painel.
"""
import logging
import threading

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10

_cache: dict[str, str] = {}
_cache_ready = False
_cache_lock = threading.Lock()


def get_cached_names() -> dict[str, str]:
    """Retorna {ramal: nome_do_funcionario}, conforme a ultima busca bem-sucedida."""
    with _cache_lock:
        return dict(_cache)


def is_cache_ready() -> bool:
    """Indica se ao menos uma consulta valida ja definiu a lista autoritativa."""
    with _cache_lock:
        return _cache_ready


def _fetch(base_url: str, api_key: str, verify_tls: bool) -> dict[str, str]:
    response = requests.get(
        f"{base_url}/employees",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
        verify=verify_tls,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("result"):
        raise RuntimeError(f"MikoPBX API retornou result=false: {payload.get('messages')}")

    employees = payload["data"]["data"]
    return {
        str(emp["number"]): emp["user_username"]
        for emp in employees
        if emp.get("number") and emp.get("user_username") and not emp.get("disabled")
    }


def refresh(base_url: str, api_key: str, verify_tls: bool = False) -> dict[str, str] | None:
    """Atualiza o cache atomicamente e retorna a lista; em falha, retorna None."""
    global _cache, _cache_ready
    try:
        names = _fetch(base_url, api_key, verify_tls)
        with _cache_lock:
            _cache = dict(names)
            _cache_ready = True
        logger.info("Nomes de %d ramais atualizados via API do MikoPBX", len(names))
        return dict(names)
    except Exception:
        logger.exception("Falha ao buscar funcionarios na API do MikoPBX - mantendo cache anterior")
        return None
