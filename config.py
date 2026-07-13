"""Carrega a configuracao do monitor a partir de variaveis de ambiente (.env).

AMI e WhatsApp sao opcionais: se as credenciais de um deles nao estiverem
preenchidas, o programa sobe mesmo assim (o painel web mostra o que falta
configurar) em vez de travar no startup.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
PROJECT_DIR = Path(__file__).parent


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "sim")


@dataclass(frozen=True)
class Config:
    ami_host: str
    ami_port: int
    ami_user: str | None
    ami_secret: str | None
    ami_enabled: bool

    whatsapp_token: str | None
    whatsapp_phone_id: str | None
    whatsapp_graph_api_version: str
    whatsapp_template: str
    whatsapp_use_template: bool
    whatsapp_recipients: list[str]
    whatsapp_enabled: bool

    debounce_seconds: float
    reconcile_seconds: float
    alert_max_attempts: int
    alert_retry_base_seconds: float
    alert_test_cooldown_seconds: float
    incidents_db_path: Path

    dashboard_host: str
    dashboard_port: int

    demo_mode: bool

    mikopbx_api_key: str | None
    mikopbx_api_url: str
    mikopbx_api_enabled: bool
    mikopbx_verify_tls: bool
    mikopbx_names_refresh_seconds: float


def load_config() -> Config:
    ami_user = os.environ.get("AMI_USER") or None
    ami_secret = os.environ.get("AMI_SECRET") or None

    whatsapp_token = os.environ.get("WHATSAPP_TOKEN") or None
    whatsapp_phone_id = os.environ.get("WHATSAPP_PHONE_ID") or None
    recipients_raw = os.environ.get("WHATSAPP_RECIPIENTS", "")
    whatsapp_recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

    mikopbx_api_key = os.environ.get("MIKOPBX_API_KEY") or None
    incidents_path = Path(os.environ.get("INCIDENTS_DB_PATH", "data/pulsopbx.db"))
    if not incidents_path.is_absolute():
        incidents_path = PROJECT_DIR / incidents_path

    return Config(
        ami_host=os.environ.get("AMI_HOST", "192.168.1.254"),
        ami_port=int(os.environ.get("AMI_PORT", "5038")),
        ami_user=ami_user,
        ami_secret=ami_secret,
        ami_enabled=bool(ami_user and ami_secret),
        whatsapp_token=whatsapp_token,
        whatsapp_phone_id=whatsapp_phone_id,
        whatsapp_graph_api_version=os.environ.get("WHATSAPP_GRAPH_API_VERSION", "v25.0"),
        whatsapp_template=os.environ.get("WHATSAPP_TEMPLATE", "ramal_alerta"),
        whatsapp_use_template=_bool_env("WHATSAPP_USE_TEMPLATE", True),
        whatsapp_recipients=whatsapp_recipients,
        whatsapp_enabled=bool(whatsapp_token and whatsapp_phone_id and whatsapp_recipients),
        debounce_seconds=float(os.environ.get("DEBOUNCE_SECONDS", "30")),
        reconcile_seconds=float(os.environ.get("RECONCILE_SECONDS", "60")),
        alert_max_attempts=max(1, int(os.environ.get("ALERT_MAX_ATTEMPTS", "3"))),
        alert_retry_base_seconds=max(1, float(os.environ.get("ALERT_RETRY_BASE_SECONDS", "15"))),
        alert_test_cooldown_seconds=max(
            10, float(os.environ.get("ALERT_TEST_COOLDOWN_SECONDS", "60"))
        ),
        incidents_db_path=incidents_path,
        dashboard_host=os.environ.get("DASHBOARD_HOST", "0.0.0.0"),
        dashboard_port=int(os.environ.get("DASHBOARD_PORT", "8080")),
        demo_mode=_bool_env("DEMO_MODE", False),
        mikopbx_api_key=mikopbx_api_key,
        mikopbx_api_url=os.environ.get("MIKOPBX_API_URL", "https://192.168.1.254/pbxcore/api/v3"),
        mikopbx_api_enabled=bool(mikopbx_api_key),
        mikopbx_verify_tls=_bool_env("MIKOPBX_VERIFY_TLS", False),
        mikopbx_names_refresh_seconds=float(os.environ.get("MIKOPBX_NAMES_REFRESH_SECONDS", "300")),
    )
