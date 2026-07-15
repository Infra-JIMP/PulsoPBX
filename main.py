"""Monitor de ramais MikoPBX, alertas por e-mail e painel web."""
import asyncio
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import mikopbx_api
from alert_store import AlertStore
from alerts import AlertDispatcher
from ami_client import AmiClient
from availability import AvailabilityStore
from config import ConfigError, load_config
from incidents import IncidentStore
from notifications import EmailNotifier, NotificationRouter
from profiles import load_profiles
from responsible_alerts import ResponsibleAlertScheduler
from state import StateTracker
from web import run_dashboard
from work_calendar import WorkCalendar

LOG_DIR = Path(__file__).parent / "logs"
TICK_INTERVAL_SECONDS = 5


async def _apply_employee_snapshot(
    tracker: StateTracker,
    incidents: IncidentStore | None,
    names: dict[str, str],
    availability: AvailabilityStore | None = None,
) -> None:
    removed = tracker.retain_extensions(names)
    if not removed:
        return
    logging.getLogger("main").info(
        "%d ramal(is) removido(s) do monitoramento por nao constarem mais no MikoPBX",
        len(removed),
    )
    if incidents is not None:
        await asyncio.to_thread(incidents.resolve_removed_extensions, removed)
    if availability is not None:
        await asyncio.to_thread(
            availability.suppress_pending_for_extensions,
            removed,
        )


async def mikopbx_names_loop(
    config,
    tracker: StateTracker,
    incidents: IncidentStore | None,
    availability: AvailabilityStore | None,
) -> None:
    while True:
        names = await asyncio.to_thread(
            mikopbx_api.refresh, config.mikopbx_api_url, config.mikopbx_api_key, config.mikopbx_verify_tls
        )
        if names is not None:
            await _apply_employee_snapshot(tracker, incidents, names, availability)
        await asyncio.sleep(config.mikopbx_names_refresh_seconds)


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = RotatingFileHandler(
        LOG_DIR / "monitor.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])


def build_notification_router(config) -> NotificationRouter:
    channels = []
    if config.email_enabled:
        channels.append(
            EmailNotifier(
                host=config.email_smtp_host,
                port=config.email_smtp_port,
                sender=config.email_sender,
                recipients=config.email_recipients,
                username=config.email_smtp_username,
                password=config.email_smtp_password,
                starttls=config.email_starttls,
                use_ssl=config.email_use_ssl,
                timeout_seconds=config.email_timeout_seconds,
            )
        )
    return NotificationRouter(channels)


async def tick_loop(
    tracker: StateTracker,
    scheduler: ResponsibleAlertScheduler | None,
    incidents: IncidentStore | None,
    availability: AvailabilityStore | None,
) -> None:
    logger = logging.getLogger("tick")
    while True:
        await asyncio.sleep(TICK_INTERVAL_SECONDS)
        transition_at = time.time()
        for extension, status in tracker.tick(now=transition_at):
            logger.info("Ramal %s mudou para %s (confirmado)", extension, status)
            profile = load_profiles().get(extension, {})
            incident = None
            if incidents is not None:
                try:
                    incident = await asyncio.to_thread(
                        incidents.record_transition,
                        extension,
                        status,
                        transition_at,
                    )
                except Exception:
                    logger.exception("Falha ao registrar incidente do ramal %s", extension)
            if availability is not None:
                try:
                    await asyncio.to_thread(
                        availability.record_event,
                        extension,
                        status,
                        transition_at,
                        "transition",
                        profile,
                    )
                except Exception:
                    logger.exception("Falha ao registrar historico do ramal %s", extension)
            if scheduler is not None:
                await scheduler.schedule_transition(
                    extension,
                    status,
                    incident,
                    transition_at,
                )


async def maintain_ami_connection(client: AmiClient, retry_seconds: float = 5) -> None:
    """Mantem a AMI em segundo plano sem impedir o painel web de iniciar."""
    while True:
        try:
            await client.connect()
            # O panoramisk assume as reconexoes depois do primeiro connect.
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger("main").exception(
                "Falha ao iniciar conexao AMI; nova tentativa em %.0fs", retry_seconds
            )
            await asyncio.sleep(retry_seconds)


async def run() -> None:
    setup_logging()
    logger = logging.getLogger("main")
    try:
        config = load_config()
    except ConfigError as exc:
        logger.critical("Configuracao invalida; servico nao iniciado: %s", exc)
        return
    if config.dashboard_host not in {"127.0.0.1", "::1", "localhost"} and not config.dashboard_auth_enabled:
        logger.warning(
            "Painel exposto em %s sem autenticacao; configure DASHBOARD_USERNAME e DASHBOARD_PASSWORD",
            config.dashboard_host,
        )

    tracker = StateTracker(debounce_seconds=config.debounce_seconds)

    incidents: IncidentStore | None = IncidentStore(config.incidents_db_path)
    try:
        await asyncio.to_thread(incidents.initialize)
        logger.info("Historico de incidentes em %s", config.incidents_db_path)
    except Exception:
        logger.exception("Historico persistente indisponivel; monitor seguira sem salvar incidentes")
        incidents = None

    alert_store: AlertStore | None = AlertStore(config.incidents_db_path)
    try:
        await asyncio.to_thread(alert_store.initialize)
        logger.info("Historico de entregas de alertas em %s", config.incidents_db_path)
    except Exception:
        logger.exception("Historico de alertas indisponivel; entregas seguirao apenas em memoria")
        alert_store = None

    availability: AvailabilityStore | None = AvailabilityStore(config.incidents_db_path)
    try:
        await asyncio.to_thread(availability.initialize)
        logger.info("Historico cronologico de disponibilidade em %s", config.incidents_db_path)
    except Exception:
        logger.exception("Historico de disponibilidade indisponivel")
        availability = None

    calendar = WorkCalendar(config.work_calendar_path)
    if calendar.configured:
        logger.info("Calendario de expediente carregado de %s", config.work_calendar_path)
    else:
        logger.warning(
            "Calendario de expediente ainda nao configurado; historico sera coletado, "
            "mas avisos individuais ficarao suspensos"
        )

    notifier = build_notification_router(config)
    if notifier.channel_names:
        logger.info(
            "Canais de alerta ativos: %s (%d destino(s) global(is) para teste)",
            ", ".join(notifier.channel_names),
            len(notifier.recipients),
        )
    else:
        logger.warning(
            "Nenhum canal de alerta configurado; painel e monitoramento continuam funcionando"
        )

    alerts: AlertDispatcher | None = None
    if notifier.channel_names:
        alerts = AlertDispatcher(
            notifier,
            max_attempts=config.alert_max_attempts,
            retry_base_seconds=config.alert_retry_base_seconds,
            store=alert_store,
            test_cooldown_seconds=config.alert_test_cooldown_seconds,
        )

    responsible_scheduler: ResponsibleAlertScheduler | None = None

    async def on_snapshot(extension: str, online: bool) -> None:
        # Filtra "ramais" que nao sao telefones de pessoas (filas, conferencias,
        # aplicacoes de teste): so rastreamos numeros que tem funcionario vinculado
        # no MikoPBX. Se a lista de funcionarios ainda nao carregou (cache vazio),
        # nao filtra - rastreia tudo ate a lista chegar.
        known = mikopbx_api.get_cached_names()
        if (
            config.mikopbx_api_enabled
            and mikopbx_api.is_cache_ready()
            and extension not in known
        ):
            return
        observed_at = time.time()
        is_baseline = tracker.update(extension, online, now=observed_at)
        if not is_baseline:
            return
        profile = load_profiles().get(extension, {})
        if availability is not None:
            try:
                await asyncio.to_thread(
                    availability.record_event,
                    extension,
                    "online" if online else "offline",
                    observed_at,
                    "baseline",
                    profile,
                )
            except Exception:
                logger.exception("Falha ao registrar baseline do ramal %s", extension)
        # Um incidente aberto antes de uma reinicializacao precisa continuar ou ser
        # encerrado pelo primeiro snapshot real, sem criar uma nova queda artificial.
        if incidents is not None and responsible_scheduler is not None:
            open_incident = await asyncio.to_thread(
                incidents.get_open, extension, observed_at
            )
            if open_incident is not None:
                if online:
                    resolved = await asyncio.to_thread(
                        incidents.record_transition,
                        extension,
                        "online",
                        observed_at,
                    )
                    await responsible_scheduler.schedule_transition(
                        extension, "online", resolved, observed_at
                    )
                else:
                    await responsible_scheduler.schedule_transition(
                        extension, "offline", open_incident, observed_at
                    )

    if config.demo_mode:
        import demo
        demo.seed(tracker)
        if availability is not None:
            for item in tracker.snapshot():
                await asyncio.to_thread(
                    availability.record_event,
                    item["extension"],
                    "online" if item["online"] else "offline",
                    item["since"],
                    "baseline",
                    demo.DEMO_NAMES.get(item["extension"], {}),
                )
        logger.warning("DEMO_MODE ativo - painel exibindo ramais de exemplo (nao sao reais)")

    # Carrega a lista de funcionarios ANTES de conectar a AMI, para o filtro de
    # ramais ja estar pronto quando os primeiros eventos chegarem.
    if config.mikopbx_api_enabled and not config.demo_mode:
        names = await asyncio.to_thread(
            mikopbx_api.refresh, config.mikopbx_api_url, config.mikopbx_api_key, config.mikopbx_verify_tls
        )
        if names is not None:
            await _apply_employee_snapshot(tracker, incidents, names, availability)

    client: AmiClient | None = None
    # Demonstracao precisa ser totalmente isolada: mesmo que o .env local tenha
    # credenciais validas, ela nao pode abrir uma segunda conexao na AMI real.
    if config.ami_enabled and not config.demo_mode:
        client = AmiClient(
            host=config.ami_host,
            port=config.ami_port,
            username=config.ami_user,
            secret=config.ami_secret,
            on_snapshot=on_snapshot,
        )
    elif not config.demo_mode:
        logger.warning(
            "AMI_USER/AMI_SECRET ausentes no .env - monitoramento de ramais desativado por enquanto "
            "(painel sobe mesmo assim, mostrando 'AMI não configurada')"
        )

    if availability is not None:
        responsible_scheduler = ResponsibleAlertScheduler(
            availability,
            calendar,
            tracker,
            alerts,
            client,
            delay_seconds=config.responsible_alert_delay_seconds,
            mass_outage_threshold=config.mass_outage_threshold,
            mass_outage_window_seconds=config.mass_outage_window_seconds,
        )

    tasks = [
        tick_loop(tracker, responsible_scheduler, incidents, availability),
        run_dashboard(
            tracker,
            client,
            config,
            alerts,
            incidents,
            alert_store,
            availability,
            calendar,
        ),
    ]
    if alerts is not None:
        tasks.append(alerts.run())
    if responsible_scheduler is not None:
        tasks.append(responsible_scheduler.run())
    if client is not None:
        logger.info(
            "Conexao AMI sera mantida em segundo plano em %s:%s",
            config.ami_host,
            config.ami_port,
        )
        tasks.append(maintain_ami_connection(client))
        tasks.append(client.periodic_reconcile(config.reconcile_seconds))
    if config.mikopbx_api_enabled and not config.demo_mode:
        tasks.append(mikopbx_names_loop(config, tracker, incidents, availability))
    elif not config.demo_mode:
        logger.warning(
            "MIKOPBX_API_KEY ausente no .env - nomes dos ramais nao serao buscados "
            "automaticamente (use ramais_nomes.json manualmente, se quiser)"
        )

    try:
        await asyncio.gather(*tasks)
    finally:
        if client is not None:
            client.close()
        if incidents is not None:
            incidents.close()
        if alert_store is not None:
            alert_store.close()
        if availability is not None:
            availability.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
