"""Sincroniza o estado do PulsoPBX local com o painel hospedado no Vercel."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

LOGGER = logging.getLogger("cloud_sync")


def _local_auth(config) -> aiohttp.BasicAuth | None:
    if not config.dashboard_auth_enabled:
        return None
    return aiohttp.BasicAuth(config.dashboard_username, config.dashboard_password)


async def _read_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    auth: aiohttp.BasicAuth | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    async with session.get(url, auth=auth, headers=headers) as response:
        response.raise_for_status()
        return await response.json()


async def _collect_snapshot(session: aiohttp.ClientSession, config) -> dict[str, Any]:
    base = f"http://127.0.0.1:{config.dashboard_port}"
    auth = _local_auth(config)
    admin_headers = None
    if config.responsibles_admin_enabled:
        admin_headers = {
            "X-PulsoPBX-Admin": config.responsibles_admin_password,
            "X-PulsoPBX-Action": "manage-responsibles",
        }

    status, directory, calls = await asyncio.gather(
        _read_json(session, f"{base}/api/status", auth=auth),
        _read_json(session, f"{base}/api/directory", auth=auth),
        _read_json(session, f"{base}/api/calls/history?days=90", auth=auth),
    )
    reports_list = await asyncio.gather(
        *(
            _read_json(session, f"{base}/api/reports/availability?days={days}", auth=auth)
            for days in (7, 30, 90)
        )
    )
    admin_directory = None
    if admin_headers:
        admin_directory = await _read_json(
            session,
            f"{base}/api/admin/directory",
            auth=auth,
            headers=admin_headers,
        )
    return {
        "source": "pulsopbx-local",
        "generated_at": time.time(),
        "status": status,
        "directory": directory,
        "admin_directory": admin_directory,
        "reports": {str(days): report for days, report in zip((7, 30, 90), reports_list)},
        "calls": calls,
    }


async def _apply_command(
    session: aiohttp.ClientSession,
    config,
    command: dict[str, Any],
) -> dict[str, Any]:
    command_id = command.get("id")
    method = str(command.get("method") or "").upper()
    path = str(command.get("path") or "")
    allowed = (
        path == "/api/alerts/test"
        or path == "/api/admin/directory"
        or path.startswith("/api/admin/directory/")
    )
    if method not in {"POST", "PUT", "DELETE"} or not allowed:
        return {"id": command_id, "ok": False, "status": 400, "error": "Comando recusado"}

    headers = {"Content-Type": "application/json"}
    if path == "/api/alerts/test":
        headers["X-PulsoPBX-Action"] = "test-alert"
    else:
        if not config.responsibles_admin_enabled:
            return {"id": command_id, "ok": False, "status": 503, "error": "Administracao local desativada"}
        headers.update(
            {
                "X-PulsoPBX-Admin": config.responsibles_admin_password,
                "X-PulsoPBX-Action": "manage-responsibles",
            }
        )
    url = f"http://127.0.0.1:{config.dashboard_port}{path}"
    try:
        async with session.request(
            method,
            url,
            auth=_local_auth(config),
            headers=headers,
            json=command.get("body") or {},
        ) as response:
            try:
                payload = await response.json()
            except (aiohttp.ContentTypeError, ValueError):
                payload = {"message": await response.text()}
            return {
                "id": command_id,
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "result": payload,
            }
    except Exception as exc:
        LOGGER.exception("Falha ao aplicar comando %s recebido da nuvem", command_id)
        return {"id": command_id, "ok": False, "status": 502, "error": str(exc)}


async def run_cloud_sync(config) -> None:
    """Mantem o Vercel atualizado e executa comandos administrativos autenticados."""
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=config.cloud_sync_verify_tls)
    pending_results: list[dict[str, Any]] = []
    headers = {
        "Authorization": f"Bearer {config.cloud_sync_token}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        while True:
            try:
                snapshot = await _collect_snapshot(session, config)
                snapshot["command_results"] = pending_results
                async with session.post(
                    f"{config.cloud_sync_url}/api/sync",
                    headers=headers,
                    json=snapshot,
                ) as response:
                    response.raise_for_status()
                    reply = await response.json()
                commands = reply.get("commands") or []
                pending_results = [
                    await _apply_command(session, config, command)
                    for command in commands[:20]
                ]
                LOGGER.info(
                    "Painel Vercel sincronizado (%d comando(s) recebido(s))",
                    len(commands),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Falha temporaria na sincronizacao com o Vercel")
            await asyncio.sleep(config.cloud_sync_interval_seconds)
