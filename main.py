"""Monitor de ramais MikoPBX -> alerta WhatsApp, com painel web de status. Ponto de entrada do servico."""
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import mikopbx_api
from alerts import AlertDispatcher
from ami_client import AmiClient
from config import load_config
from notifier import WhatsAppNotifier
from state import StateTracker
from web import run_dashboard

LOG_DIR = Path(__file__).parent / "logs"
TICK_INTERVAL_SECONDS = 5


async def mikopbx_names_loop(config) -> None:
    while True:
        await asyncio.to_thread(
            mikopbx_api.refresh, config.mikopbx_api_url, config.mikopbx_api_key, config.mikopbx_verify_tls
        )
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


async def tick_loop(tracker: StateTracker, alerts: AlertDispatcher | None) -> None:
    logger = logging.getLogger("tick")
    while True:
        await asyncio.sleep(TICK_INTERVAL_SECONDS)
        for extension, status in tracker.tick():
            logger.info("Ramal %s mudou para %s (confirmado)", extension, status)
            if alerts is not None:
                alerts.enqueue(extension, status)


async def run() -> None:
    setup_logging()
    logger = logging.getLogger("main")
    config = load_config()

    tracker = StateTracker(debounce_seconds=config.debounce_seconds)

    notifier: WhatsAppNotifier | None = None
    if config.whatsapp_enabled:
        notifier = WhatsAppNotifier(
            token=config.whatsapp_token,
            phone_number_id=config.whatsapp_phone_id,
            template_name=config.whatsapp_template,
            use_template=config.whatsapp_use_template,
            recipients=config.whatsapp_recipients,
        )
    else:
        logger.warning(
            "WHATSAPP_TOKEN/WHATSAPP_PHONE_ID/WHATSAPP_RECIPIENTS ausentes no .env - "
            "alertas por WhatsApp desativados por enquanto (painel continua funcionando)"
        )

    alerts: AlertDispatcher | None = None
    if notifier is not None:
        alerts = AlertDispatcher(
            notifier,
            max_attempts=config.alert_max_attempts,
            retry_base_seconds=config.alert_retry_base_seconds,
        )

    async def on_snapshot(extension: str, online: bool) -> None:
        # Filtra "ramais" que nao sao telefones de pessoas (filas, conferencias,
        # aplicacoes de teste): so rastreamos numeros que tem funcionario vinculado
        # no MikoPBX. Se a lista de funcionarios ainda nao carregou (cache vazio),
        # nao filtra - rastreia tudo ate a lista chegar.
        known = mikopbx_api.get_cached_names()
        if config.mikopbx_api_enabled and known and extension not in known:
            return
        tracker.update(extension, online)

    if config.demo_mode:
        import demo
        demo.seed(tracker)
        logger.warning("DEMO_MODE ativo - painel exibindo ramais de exemplo (nao sao reais)")

    # Carrega a lista de funcionarios ANTES de conectar a AMI, para o filtro de
    # ramais ja estar pronto quando os primeiros eventos chegarem.
    if config.mikopbx_api_enabled and not config.demo_mode:
        await asyncio.to_thread(
            mikopbx_api.refresh, config.mikopbx_api_url, config.mikopbx_api_key, config.mikopbx_verify_tls
        )

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
        logger.info("Conectando a AMI em %s:%s...", config.ami_host, config.ami_port)
        await client.connect()
    elif not config.demo_mode:
        logger.warning(
            "AMI_USER/AMI_SECRET ausentes no .env - monitoramento de ramais desativado por enquanto "
            "(painel sobe mesmo assim, mostrando 'AMI não configurada')"
        )

    tasks = [
        tick_loop(tracker, alerts),
        run_dashboard(tracker, client, config, alerts),
    ]
    if alerts is not None:
        tasks.append(alerts.run())
    if client is not None:
        tasks.append(client.periodic_reconcile(config.reconcile_seconds))
    if config.mikopbx_api_enabled and not config.demo_mode:
        tasks.append(mikopbx_names_loop(config))
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


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
