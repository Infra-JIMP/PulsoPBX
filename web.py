"""Servidor web do painel de status dos ramais.

Roda no mesmo processo asyncio do monitor e le o estado em memoria diretamente
(StateTracker / AmiClient) - sem banco de dados, sem processo separado.
"""
import asyncio
import base64
import ipaddress
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

import demo
import mikopbx_api
from alerts import AlertDispatcher
from names import clear_email_override, load_names, save_email_override
from profiles import load_profiles, validate_email

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
AVAILABILITY_KEY = web.AppKey("availability", object)
CALENDAR_KEY = web.AppKey("calendar", object)
PUBLIC_PROXY_HEADERS = ("CF-Connecting-IP", "CF-Ray", "CF-Visitor")


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


def _is_internal_management_request(request: web.Request) -> bool:
    if any(request.headers.get(name) for name in PUBLIC_PROXY_HEADERS):
        return False
    try:
        address = ipaddress.ip_address((request.remote or "").split("%", 1)[0])
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def _responsibles_management_available(request: web.Request) -> bool:
    config = request.app[CONFIG_KEY]
    return bool(
        _is_internal_management_request(request)
        and not getattr(config, "demo_mode", False)
        and getattr(config, "responsibles_admin_enabled", False)
    )


def _management_error(request: web.Request) -> web.Response | None:
    if not _is_internal_management_request(request):
        return web.json_response({"ok": False, "error": "Recurso indisponivel"}, status=404)
    config = request.app[CONFIG_KEY]
    if getattr(config, "demo_mode", False):
        return web.json_response({"ok": False, "error": "Indisponivel no modo demonstracao"}, status=403)
    expected = getattr(config, "responsibles_admin_password", None)
    if not expected:
        return web.json_response(
            {"ok": False, "error": "Senha administrativa nao configurada"},
            status=503,
        )
    supplied = request.headers.get("X-PulsoPBX-Admin", "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        return web.json_response({"ok": False, "error": "Senha administrativa invalida"}, status=401)
    if request.headers.get("X-PulsoPBX-Action") != "manage-responsibles":
        return web.json_response({"ok": False, "error": "Acao nao autorizada"}, status=403)
    return None


def _origin_allowed(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    return not origin or urlsplit(origin).netloc.lower() == request.host.lower()


def _responsible_rows(tracker) -> list[dict]:
    extensions = tracker.snapshot() if tracker is not None else []
    profiles = load_profiles()
    local = load_names()
    mikopbx = mikopbx_api.get_cached_profiles()
    rows = []
    for state in sorted(
        extensions,
        key=lambda item: (len(str(item.get("extension", ""))), str(item.get("extension", ""))),
    ):
        extension = str(state.get("extension", "")).strip()
        if not extension:
            continue
        profile = profiles.get(extension, {})
        local_profile = local.get(extension, {})
        local_email = validate_email(local_profile.get("email", ""))
        miko_email = validate_email(mikopbx.get(extension, {}).get("email", ""))
        source = "local" if local_email else ("mikopbx" if miko_email else "none")
        rows.append(
            {
                "extension": extension,
                "name": str(profile.get("nome") or ""),
                "sector": str(profile.get("setor") or ""),
                "email": str(profile.get("email") or ""),
                "notify": profile.get("notificar") is not False,
                "source": source,
                "online": bool(state.get("online")),
            }
        )
    return rows


async def _handle_responsibles_list(request: web.Request) -> web.Response:
    error = _management_error(request)
    if error is not None:
        return error
    rows = await asyncio.to_thread(_responsible_rows, request.app[TRACKER_KEY])
    return web.json_response(
        {"ok": True, "responsibles": rows},
        headers={"Cache-Control": "no-store"},
    )


def _known_extension(request: web.Request, extension: str) -> bool:
    tracker = request.app[TRACKER_KEY]
    return tracker is not None and any(
        str(item.get("extension", "")) == extension for item in tracker.snapshot()
    )


async def _handle_responsible_save(request: web.Request) -> web.Response:
    error = _management_error(request)
    if error is not None:
        return error
    if not _origin_allowed(request):
        return web.json_response({"ok": False, "error": "Origem nao autorizada"}, status=403)
    if request.content_type != "application/json":
        return web.json_response({"ok": False, "error": "Conteudo invalido"}, status=415)
    extension = request.match_info["extension"].strip()
    if not extension.isdigit() or not 1 <= len(extension) <= 10:
        return web.json_response({"ok": False, "error": "Ramal invalido"}, status=400)
    if not _known_extension(request, extension):
        return web.json_response({"ok": False, "error": "Ramal nao monitorado"}, status=404)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": "JSON invalido"}, status=400)
    email = str(body.get("email") or "").strip().lower()
    if len(email) > 254 or validate_email(email) != email:
        return web.json_response({"ok": False, "error": "Informe um e-mail valido"}, status=400)
    notify = body.get("notify", True)
    if not isinstance(notify, bool):
        return web.json_response({"ok": False, "error": "Opcao de notificacao invalida"}, status=400)
    sector = None
    if "sector" in body:
        sector = " ".join(str(body.get("sector") or "").split())
        if len(sector) > 80:
            return web.json_response(
                {"ok": False, "error": "O setor deve ter no maximo 80 caracteres"},
                status=400,
            )
    try:
        await asyncio.to_thread(save_email_override, extension, email, notify, sector)
    except (OSError, ValueError):
        logger.exception("Falha ao salvar responsavel do ramal %s", extension)
        return web.json_response({"ok": False, "error": "Nao foi possivel salvar o cadastro"}, status=500)
    return web.json_response(
        {"ok": True, "extension": extension, "sector": sector},
        headers={"Cache-Control": "no-store"},
    )


async def _handle_responsible_clear(request: web.Request) -> web.Response:
    error = _management_error(request)
    if error is not None:
        return error
    if not _origin_allowed(request):
        return web.json_response({"ok": False, "error": "Origem nao autorizada"}, status=403)
    extension = request.match_info["extension"].strip()
    if not extension.isdigit() or not _known_extension(request, extension):
        return web.json_response({"ok": False, "error": "Ramal nao monitorado"}, status=404)
    try:
        changed = await asyncio.to_thread(clear_email_override, extension)
    except (OSError, ValueError):
        logger.exception("Falha ao limpar responsavel do ramal %s", extension)
        return web.json_response({"ok": False, "error": "Nao foi possivel remover o cadastro"}, status=500)
    return web.json_response(
        {"ok": True, "extension": extension, "changed": changed},
        headers={"Cache-Control": "no-store"},
    )


async def _handle_status(request: web.Request) -> web.Response:
    tracker = request.app[TRACKER_KEY]
    client = request.app[CLIENT_KEY]  # AmiClient | None, se AMI nao estiver configurada
    config = request.app[CONFIG_KEY]
    alerts = request.app[ALERTS_KEY]
    incidents = request.app[INCIDENTS_KEY]
    alert_store = request.app[ALERT_STORE_KEY]
    availability = request.app[AVAILABILITY_KEY]
    calendar = request.app[CALENDAR_KEY]

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
        profiles = {
            extension: {**profile, "email": "", "notificar": True}
            for extension, profile in demo.DEMO_NAMES.items()
        }
    else:
        profiles = load_profiles()

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
        meta = profiles.get(ext["extension"], {})
        ext["nome"] = meta.get("nome", "")
        ext["setor"] = meta.get("setor", "")
        ext["responsible_email_configured"] = bool(meta.get("email"))
        if alerts is None:
            ext["alert"] = {"status": "not_configured", "sent_count": 0, "total_recipients": 0}
        else:
            ext["alert"] = alerts.get_extension_status(
                ext["extension"], "online" if ext["online"] else "offline"
            ) or {
                "status": "idle",
                "sent_count": 0,
                "total_recipients": int(bool(meta.get("email") and config.email_enabled)),
            }
        ext["incident"] = open_incidents.get(ext["extension"])

    online = sum(1 for e in extensions if e["online"])
    confirming = sum(1 for e in extensions if e["pending_status"] is not None)
    recent_events = tracker.recent_events()
    for event in recent_events:
        meta = profiles.get(event["extension"], {})
        event["nome"] = meta.get("nome", "")
        event["setor"] = meta.get("setor", "")
    recent_incidents = await asyncio.to_thread(incidents.recent) if incidents is not None else []
    for incident in recent_incidents:
        meta = profiles.get(incident["extension"], {})
        incident["nome"] = meta.get("nome", "")
        incident["setor"] = meta.get("setor", "")

    monitored_extensions = {item["extension"] for item in extensions}
    responsible_email_count = sum(
        bool(profiles.get(extension, {}).get("email"))
        for extension in monitored_extensions
    )
    job_summary = (
        await asyncio.to_thread(availability.notification_summary)
        if availability is not None
        else {"pending": 0, "sent": 0, "suppressed": 0, "total": 0}
    )

    return web.json_response(
        {
            "ami_status": ami_status,
            "last_reconcile_at": last_reconcile_at,
            "last_reconcile_age_seconds": reconcile_age,
            "data_freshness": data_freshness,
            "notifications": {
                "configured": config.notifications_enabled,
                "target_count": responsible_email_count,
                "missing_target_count": max(0, len(monitored_extensions) - responsible_email_count),
                "test_target_count": config.notification_target_count,
                "test_available": alerts is not None and bool(config.email_recipients),
                "test_cooldown_seconds": config.alert_test_cooldown_seconds,
                "responsible_alert_delay_seconds": config.responsible_alert_delay_seconds,
                "mass_outage_threshold": config.mass_outage_threshold,
                "latest_status": recent_alerts[0]["status"] if recent_alerts else None,
                "jobs": job_summary,
                "calendar": calendar.summary() if calendar is not None else {"configured": False},
                "channels": [
                    {
                        "id": "email",
                        "label": "E-mail",
                        "enabled": config.email_enabled,
                        "target_count": len(config.email_recipients),
                    },
                ],
            },
            "management": {
                "responsibles_available": _responsibles_management_available(request),
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
    if alerts is None or not getattr(alerts, "can_send_test", True):
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


async def _handle_availability_report(request: web.Request) -> web.Response:
    availability = request.app[AVAILABILITY_KEY]
    calendar = request.app[CALENDAR_KEY]
    config = request.app[CONFIG_KEY]
    if availability is None or calendar is None:
        return web.json_response(
            {"ok": False, "error": "Historico de disponibilidade indisponivel"},
            status=503,
        )
    try:
        days = int(request.query.get("days", "30"))
    except ValueError:
        return web.json_response({"ok": False, "error": "Periodo invalido"}, status=400)
    if not 1 <= days <= 366:
        return web.json_response(
            {"ok": False, "error": "O periodo deve ficar entre 1 e 366 dias"},
            status=400,
        )
    if config.demo_mode:
        profiles = {
            extension: {**profile, "email": "", "notificar": True}
            for extension, profile in demo.DEMO_NAMES.items()
        }
    else:
        profiles = load_profiles()
    report = await asyncio.to_thread(
        availability.build_report,
        profiles,
        calendar,
        days,
        None,
        config.report_minimum_workdays,
    )
    return web.json_response(report, headers={"Cache-Control": "no-store"})


def create_app(
    tracker,
    client,
    config,
    alerts=None,
    incidents=None,
    alert_store=None,
    availability=None,
    calendar=None,
) -> web.Application:
    app = web.Application(middlewares=[_dashboard_auth_middleware])
    app[TRACKER_KEY] = tracker
    app[CLIENT_KEY] = client
    app[CONFIG_KEY] = config
    app[ALERTS_KEY] = alerts
    app[INCIDENTS_KEY] = incidents
    app[ALERT_STORE_KEY] = alert_store
    app[AVAILABILITY_KEY] = availability
    app[CALENDAR_KEY] = calendar
    app.router.add_get("/", _handle_index)
    app.router.add_get("/assets/pulsopbx-logo.png", _handle_brand_logo)
    app.router.add_get("/favicon.ico", _handle_favicon)
    app.router.add_get("/api/health", _handle_health)
    app.router.add_get("/api/status", _handle_status)
    app.router.add_get("/api/admin/responsibles", _handle_responsibles_list)
    app.router.add_put(
        "/api/admin/responsibles/{extension}", _handle_responsible_save
    )
    app.router.add_delete(
        "/api/admin/responsibles/{extension}", _handle_responsible_clear
    )
    app.router.add_get("/api/reports/availability", _handle_availability_report)
    app.router.add_post("/api/alerts/test", _handle_test_alert)
    return app


async def run_dashboard(
    tracker,
    client,
    config,
    alerts=None,
    incidents=None,
    alert_store=None,
    availability=None,
    calendar=None,
) -> None:
    app = create_app(
        tracker,
        client,
        config,
        alerts,
        incidents,
        alert_store,
        availability,
        calendar,
    )
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.dashboard_host, config.dashboard_port)
    await site.start()
    logger.info("Painel disponivel em http://%s:%s/", config.dashboard_host, config.dashboard_port)
    await asyncio.Event().wait()  # mantem a task viva; o runner serve em background
