"""Busca o nome dos funcionarios vinculados a cada ramal via API REST v3 do MikoPBX
(GET /employees). Mantem um cache em memoria atualizado periodicamente - se a chamada
falhar (rede, chave invalida, etc.), mantem o ultimo resultado valido em vez de apagar
os nomes ja exibidos no painel.
"""
import logging

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10

_cache: dict[str, str] = {}


def get_cached_names() -> dict[str, str]:
    """Retorna {ramal: nome_do_funcionario}, conforme a ultima busca bem-sucedida."""
    return dict(_cache)


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


def refresh(base_url: str, api_key: str, verify_tls: bool = False) -> None:
    """Busca a lista atual de funcionarios e atualiza o cache em memoria."""
    try:
        names = _fetch(base_url, api_key, verify_tls)
        _cache.clear()
        _cache.update(names)
        logger.info("Nomes de %d ramais atualizados via API do MikoPBX", len(names))
    except Exception:
        logger.exception("Falha ao buscar funcionarios na API do MikoPBX - mantendo cache anterior")
