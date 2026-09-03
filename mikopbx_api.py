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
_profiles_cache: dict[str, dict[str, str]] = {}
_cache_ready = False
_cache_lock = threading.Lock()


def get_cached_names() -> dict[str, str]:
    """Retorna {ramal: nome_do_funcionario}, conforme a ultima busca bem-sucedida."""
    with _cache_lock:
        return dict(_cache)


def get_cached_profiles() -> dict[str, dict[str, str]]:
    """Retorna nome e e-mail conhecidos sem expor a resposta bruta da API."""
    with _cache_lock:
        return {extension: dict(profile) for extension, profile in _profiles_cache.items()}


def is_cache_ready() -> bool:
    """Indica se ao menos uma consulta valida ja definiu a lista autoritativa."""
    with _cache_lock:
        return _cache_ready


def _fetch_profiles(base_url: str, api_key: str, verify_tls: bool) -> dict[str, dict[str, str]]:
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
        str(emp["number"]): {
            "nome": str(emp["user_username"]).strip(),
            "email": str(emp.get("user_email") or "").strip().lower(),
        }
        for emp in employees
        if emp.get("number") and emp.get("user_username") and not emp.get("disabled")
    }


def _fetch(base_url: str, api_key: str, verify_tls: bool) -> dict[str, str]:
    """Compatibilidade: devolve apenas o mapa de nomes."""
    return {
        extension: profile["nome"]
        for extension, profile in _fetch_profiles(base_url, api_key, verify_tls).items()
    }


def refresh(base_url: str, api_key: str, verify_tls: bool = False) -> dict[str, str] | None:
    """Atualiza o cache atomicamente e retorna a lista; em falha, retorna None."""
    global _cache, _profiles_cache, _cache_ready
    try:
        profiles = _fetch_profiles(base_url, api_key, verify_tls)
        names = {extension: profile["nome"] for extension, profile in profiles.items()}
        with _cache_lock:
            _cache = dict(names)
            _profiles_cache = {
                extension: dict(profile) for extension, profile in profiles.items()
            }
            _cache_ready = True
        email_count = sum(bool(profile["email"]) for profile in profiles.values())
        logger.info(
            "Perfis de %d ramais atualizados via API do MikoPBX (%d com e-mail)",
            len(names),
            email_count,
        )
        return dict(names)
    except Exception:
        logger.exception("Falha ao buscar funcionarios na API do MikoPBX - mantendo cache anterior")
        return None
