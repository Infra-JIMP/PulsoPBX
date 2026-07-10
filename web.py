"""Servidor web do painel de status dos ramais.

Roda no mesmo processo asyncio do monitor e le o estado em memoria diretamente
(StateTracker / AmiClient) - sem banco de dados, sem processo separado.
"""
import asyncio
import logging
import time
from pathlib import Path

from aiohttp import web

import demo
import mikopbx_api
from names import load_names

logger = logging.getLogger(__name__)

INDEX_FILE = Path(__file__).parent / "static" / "index.html"
FAVICON_FILE = Path(__file__).parent / "static" / "favicon.ico"


async def _handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(INDEX_FILE)


async def _handle_favicon(request: web.Request) -> web.FileResponse:
    return web.FileResponse(FAVICON_FILE, headers={"Cache-Control": "public, max-age=86400"})


async def _handle_status(request: web.Request) -> web.Response:
    tracker = request.app["tracker"]
    client = request.app["client"]  # AmiClient | None, se AMI nao estiver configurada
    config = request.app["config"]
    alerts = request.app["alerts"]
    incidents = request.app["incidents"]

    if config.demo_mode:
        ami_status = "demo"
        last_reconcile_at = None
    elif client is None:
        ami_status = "not_configured"
        last_reconcile_at = None
    elif client.connected:
        ami_status = "connected"
        last_reconcile_at = client.last_reconcile_at
    else:
        ami_status = "disconnected"
        last_reconcile_at = client.last_reconcile_at

    if config.demo_mode:
        names = demo.DEMO_NAMES
    else:
        # A API do MikoPBX e a fonte primaria do nome (ela nao tem conceito de "setor" -
        # o setor, quando existe, ja vem embutido no proprio nome, ex.: "Engenharia - Edson").
        # O ramais_nomes.json manual so complementa: pode sobrescrever o nome ou adicionar
        # um setor separado, ramal a ramal.
        names = {ext: {"nome": nome, "setor": ""} for ext, nome in mikopbx_api.get_cached_names().items()}
        for ext, override in load_names().items():
            merged = names.setdefault(ext, {"nome": "", "setor": ""})
            if override.get("nome"):
                merged["nome"] = override["nome"]
            if override.get("setor"):
                merged["setor"] = override["setor"]

    extensions = tracker.snapshot()
    open_incidents = await asyncio.to_thread(incidents.open_by_extension) if incidents is not None else {}
    for ext in extensions:
        meta = names.get(ext["extension"], {})
        ext["nome"] = meta.get("nome", "")
        ext["setor"] = meta.get("setor", "")
        ext["alert"] = (
            alerts.get_extension_status(ext["extension"], "online" if ext["online"] else "offline")
            if alerts is not None
            else {"status": "not_configured", "sent_count": 0, "total_recipients": 0}
        )
        ext["incident"] = open_incidents.get(ext["extension"])

    online = sum(1 for e in extensions if e["online"])
    confirming = sum(1 for e in extensions if e["pending_status"] is not None)
    recent_events = tracker.recent_events()
    for event in recent_events:
        meta = names.get(event["extension"], {})
        event["nome"] = meta.get("nome", "")
        event["setor"] = meta.get("setor", "")
    recent_incidents = await asyncio.to_thread(incidents.recent) if incidents is not None else []
    for incident in recent_incidents:
        meta = names.get(incident["extension"], {})
        incident["nome"] = meta.get("nome", "")
        incident["setor"] = meta.get("setor", "")

    return web.json_response(
        {
            "ami_status": ami_status,
            "last_reconcile_at": last_reconcile_at,
            "whatsapp_enabled": config.whatsapp_enabled,
            "generated_at": time.time(),
            "total": len(extensions),
            "online": online,
            "offline": len(extensions) - online,
            "confirming": confirming,
            "extensions": extensions,
            "recent_events": recent_events,
            "recent_alerts": alerts.recent_events() if alerts is not None else [],
            "incidents": recent_incidents,
        }
    )


def create_app(tracker, client, config, alerts=None, incidents=None) -> web.Application:
    app = web.Application()
    app["tracker"] = tracker
    app["client"] = client
    app["config"] = config
    app["alerts"] = alerts
    app["incidents"] = incidents
    app.router.add_get("/", _handle_index)
    app.router.add_get("/favicon.ico", _handle_favicon)
    app.router.add_get("/api/status", _handle_status)
    return app


async def run_dashboard(tracker, client, config, alerts=None, incidents=None) -> None:
    app = create_app(tracker, client, config, alerts, incidents)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.dashboard_host, config.dashboard_port)
    await site.start()
    logger.info("Painel disponivel em http://%s:%s/", config.dashboard_host, config.dashboard_port)
    await asyncio.Event().wait()  # mantem a task viva; o runner serve em background
