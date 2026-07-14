"""Servidor web do painel de status dos ramais.

Roda no mesmo processo asyncio do monitor e le o estado em memoria diretamente
(StateTracker / AmiClient) - sem banco de dados, sem processo separado.
"""
import asyncio
import base64
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

import demo
import mikopbx_api
from alerts import AlertDispatcher
from names import load_names

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"
BRAND_LOGO_FILE = STATIC_DIR / "pulsopbx-logo.png"
FAVICON_FILE = STATIC_DIR / "favicon.ico"
TRACKER_KEY = web.AppKey("tracker", object)
CLIENT_KEY = web.AppKey("client", object)
CONFIG_KEY = web.AppKey("config", object)
ALERTS_KEY = web.AppKey("alerts", object)
INCIDENTS_KEY = web.AppKey("incidents", object)
ALERT_STORE_KEY = web.AppKey("alert_store", object)


def _unauthorized() -> web.Response:
    return web.Response(
        status=401,
        text="Autenticacao obrigatoria",
        headers={"WWW-Authenticate": 'Basic realm="PulsoPBX", charset="UTF-8"'},
    )


@web.middleware
async def _dashboard_auth_middleware(request: web.Request, handler):
    if request.path == "/api/health":
        return await handler(request)
    config = request.app[CONFIG_KEY]
    if not getattr(config, "dashboard_auth_enabled", False):
        return await handler(request)
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Basic "):
        return _unauthorized()
    try:
        decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return _unauthorized()
    if not (
        secrets.compare_digest(username, config.dashboard_username)
        and secrets.compare_digest(password, config.dashboard_password)
    ):
        return _unauthorized()
    return await handler(request)


async def _handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(INDEX_FILE)


async def _handle_brand_logo(request: web.Request) -> web.FileResponse:
    return web.FileResponse(
        BRAND_LOGO_FILE, headers={"Cache-Control": "public, max-age=86400"}
    )


async def _handle_favicon(request: web.Request) -> web.FileResponse:
    return web.FileResponse(
        FAVICON_FILE, headers={"Cache-Control": "public, max-age=86400"}
    )


async def _handle_health(request: web.Request) -> web.Response:
    client = request.app[CLIENT_KEY]
    config = request.app[CONFIG_KEY]
    if config.demo_mode:
        ami = "demo"
    elif client is None:
        ami = "not_configured"
    elif client.connected:
        ami = "connected"
    else:
        ami = "disconnected"
    return web.json_response(
        {"ready": True, "ami": ami},
        headers={"Cache-Control": "no-store"},
    )


async def _handle_status(request: web.Request) -> web.Response:
    tracker = request.app[TRACKER_KEY]
    client = request.app[CLIENT_KEY]  # AmiClient | None, se AMI nao estiver configurada
    config = request.app[CONFIG_KEY]
    alerts = request.app[ALERTS_KEY]
    incidents = request.app[INCIDENTS_KEY]
    alert_store = request.app[ALERT_STORE_KEY]

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

    reconcile_age = time.time() - last_reconcile_at if last_reconcile_at else None
    stale_after = max(config.reconcile_seconds * 2, config.reconcile_seconds + 30)
    if config.demo_mode:
        data_freshness = "demo"
    elif client is None:
        data_freshness = "not_available"
    elif not client.connected:
        data_freshness = "stale"
    elif last_reconcile_at is None:
        data_freshness = "syncing"
    elif reconcile_age > stale_after:
        data_freshness = "stale"
    else:
        data_freshness = "fresh"

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
    if alerts is not None:
        recent_alerts = alerts.recent_events()
    elif alert_store is not None:
        stored_alerts = await asyncio.to_thread(alert_store.recent, 12)
        recent_alerts = [AlertDispatcher.serialize_event(event) for event in stored_alerts]
    else:
        recent_alerts = []

    open_incidents = await asyncio.to_thread(incidents.open_by_extension) if incidents is not None else {}
    for ext in extensions:
        meta = names.get(ext["extension"], {})
        ext["nome"] = meta.get("nome", "")
        ext["setor"] = meta.get("setor", "")
        if alerts is None:
            ext["alert"] = {"status": "not_configured", "sent_count": 0, "total_recipients": 0}
        else:
            ext["alert"] = alerts.get_extension_status(
                ext["extension"], "online" if ext["online"] else "offline"
            ) or {
                "status": "idle",
                "sent_count": 0,
                "total_recipients": config.notification_target_count,
            }
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
            "last_reconcile_age_seconds": reconcile_age,
            "data_freshness": data_freshness,
            "notifications": {
                "configured": config.notifications_enabled,
                "target_count": config.notification_target_count,
                "test_available": alerts is not None,
                "test_cooldown_seconds": config.alert_test_cooldown_seconds,
                "latest_status": recent_alerts[0]["status"] if recent_alerts else None,
                "channels": [
                    {
                        "id": "email",
                        "label": "E-mail",
                        "enabled": config.email_enabled,
                        "target_count": len(config.email_recipients),
                    },
                ],
            },
            "generated_at": time.time(),
            "total": len(extensions),
            "online": online,
            "offline": len(extensions) - online,
            "confirming": confirming,
            "extensions": extensions,
            "recent_events": recent_events,
            "recent_alerts": recent_alerts,
            "incidents": recent_incidents,
        }
    )


async def _handle_test_alert(request: web.Request) -> web.Response:
    """Coloca um teste na fila sem aceitar destinatario ou mensagem arbitrarios."""
    alerts = request.app[ALERTS_KEY]
    if alerts is None:
        return web.json_response(
            {"ok": False, "error": "Canal de alerta nao configurado no servidor"}, status=409
        )
    if request.headers.get("X-PulsoPBX-Action") != "test-alert":
        return web.json_response({"ok": False, "error": "Acao nao autorizada"}, status=403)
    if request.content_type != "application/json":
        return web.json_response({"ok": False, "error": "Conteudo invalido"}, status=415)

    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc.lower() != request.host.lower():
        return web.json_response({"ok": False, "error": "Origem nao autorizada"}, status=403)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": "JSON invalido"}, status=400)
    if body.get("confirm") is not True:
        return web.json_response({"ok": False, "error": "Confirmacao obrigatoria"}, status=400)

    event = alerts.enqueue_test()
    return web.json_response(
        {
            "ok": True,
            "deduplicated": event.get("deduplicated", False),
            "event": event,
        },
        status=202,
        headers={"Cache-Control": "no-store"},
    )


def create_app(tracker, client, config, alerts=None, incidents=None, alert_store=None) -> web.Application:
    app = web.Application(middlewares=[_dashboard_auth_middleware])
    app[TRACKER_KEY] = tracker
    app[CLIENT_KEY] = client
    app[CONFIG_KEY] = config
    app[ALERTS_KEY] = alerts
    app[INCIDENTS_KEY] = incidents
    app[ALERT_STORE_KEY] = alert_store
    app.router.add_get("/", _handle_index)
    app.router.add_get("/assets/pulsopbx-logo.png", _handle_brand_logo)
    app.router.add_get("/favicon.ico", _handle_favicon)
    app.router.add_get("/api/health", _handle_health)
    app.router.add_get("/api/status", _handle_status)
    app.router.add_post("/api/alerts/test", _handle_test_alert)
    return app


async def run_dashboard(tracker, client, config, alerts=None, incidents=None, alert_store=None) -> None:
    app = create_app(tracker, client, config, alerts, incidents, alert_store)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.dashboard_host, config.dashboard_port)
    await site.start()
    logger.info("Painel disponivel em http://%s:%s/", config.dashboard_host, config.dashboard_port)
    await asyncio.Event().wait()  # mantem a task viva; o runner serve em background
