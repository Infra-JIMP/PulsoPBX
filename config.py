"""Carrega e valida a configuracao do monitor a partir do ambiente."""
import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
PROJECT_DIR = Path(__file__).parent
TRUE_VALUES = {"1", "true", "yes", "sim"}
FALSE_VALUES = {"0", "false", "no", "nao", "não"}


class ConfigError(ValueError):
    """Configuracao invalida, com referencia direta a variavel de ambiente."""


def _env_text(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _optional_env(name: str) -> str | None:
    return _env_text(name) or None


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ConfigError(
        f"{name} deve ser true/false, 1/0, yes/no ou sim/nao; recebido: {value!r}"
    )


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _env_text(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} deve ser um numero inteiro; recebido: {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} deve estar entre {minimum} e {maximum}; recebido: {value}")
    return value


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = _env_text(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} deve ser numerico; recebido: {raw!r}") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigError(f"{name} deve estar entre {minimum:g} e {maximum:g}; recebido: {raw!r}")
    return value


def _csv_env(name: str) -> list[str]:
    values = (_item.strip() for _item in os.environ.get(name, "").split(","))
    return list(dict.fromkeys(item for item in values if item))


@dataclass(frozen=True)
class Config:
    ami_host: str
    ami_port: int
    ami_user: str | None
    ami_secret: str | None
    ami_enabled: bool

    email_smtp_host: str | None
    email_smtp_port: int
    email_smtp_username: str | None
    email_smtp_password: str | None
    email_sender: str | None
    email_starttls: bool
    email_use_ssl: bool
    email_timeout_seconds: float
    email_recipients: list[str]
    email_enabled: bool

    debounce_seconds: float
    reconcile_seconds: float
    alert_max_attempts: int
    alert_retry_base_seconds: float
    alert_test_cooldown_seconds: float
    responsible_alert_delay_seconds: float
    mass_outage_threshold: int
    mass_outage_window_seconds: float
    incidents_db_path: Path
    work_calendar_path: Path
    report_minimum_workdays: int

    dashboard_host: str
    dashboard_port: int
    dashboard_username: str | None
    dashboard_password: str | None
    dashboard_auth_enabled: bool

    demo_mode: bool

    mikopbx_api_key: str | None
    mikopbx_api_url: str
    mikopbx_api_enabled: bool
    mikopbx_verify_tls: bool
    mikopbx_names_refresh_seconds: float

    @property
    def notifications_enabled(self) -> bool:
        return self.email_enabled

    @property
    def notification_target_count(self) -> int:
        return len(self.email_recipients)

    @property
    def enabled_notification_channels(self) -> list[str]:
        return ["email"] if self.email_enabled else []


def load_config() -> Config:
    ami_host = _env_text("AMI_HOST", "192.168.1.254")
    dashboard_host = _env_text("DASHBOARD_HOST", "0.0.0.0")
    if not ami_host:
        raise ConfigError("AMI_HOST nao pode ficar vazio")
    if not dashboard_host:
        raise ConfigError("DASHBOARD_HOST nao pode ficar vazio")
    dashboard_username = _optional_env("DASHBOARD_USERNAME")
    dashboard_password = _optional_env("DASHBOARD_PASSWORD")
    if bool(dashboard_username) != bool(dashboard_password):
        raise ConfigError("DASHBOARD_USERNAME e DASHBOARD_PASSWORD devem ser configurados juntos")

    ami_user = _optional_env("AMI_USER")
    ami_secret = _optional_env("AMI_SECRET")
    email_smtp_host = _optional_env("EMAIL_SMTP_HOST")
    email_smtp_username = _optional_env("EMAIL_SMTP_USERNAME")
    email_smtp_password = _optional_env("EMAIL_SMTP_PASSWORD")
    email_sender = _optional_env("EMAIL_FROM")
    email_recipients = _csv_env("EMAIL_RECIPIENTS")
    email_starttls = _bool_env("EMAIL_SMTP_STARTTLS", True)
    email_use_ssl = _bool_env("EMAIL_SMTP_SSL", False)
    if email_starttls and email_use_ssl:
        raise ConfigError("EMAIL_SMTP_STARTTLS e EMAIL_SMTP_SSL nao podem estar ativos juntos")
    if bool(email_smtp_username) != bool(email_smtp_password):
        raise ConfigError("EMAIL_SMTP_USERNAME e EMAIL_SMTP_PASSWORD devem ser configurados juntos")
    email_required_parts = {
        "EMAIL_SMTP_HOST": email_smtp_host,
        "EMAIL_FROM": email_sender,
    }
    email_requested = any(email_required_parts.values()) or bool(
        email_smtp_username or email_smtp_password or email_recipients
    )
    if email_requested and not all(email_required_parts.values()):
        missing = ", ".join(
            name for name, value in email_required_parts.items() if not value
        )
        raise ConfigError(f"Configuracao parcial de E-mail; faltando: {missing}")

    mikopbx_api_key = _optional_env("MIKOPBX_API_KEY")
    mikopbx_api_url = _env_text(
        "MIKOPBX_API_URL", "https://192.168.1.254/pbxcore/api/v3"
    ).rstrip("/")
    if mikopbx_api_key and not mikopbx_api_url:
        raise ConfigError("MIKOPBX_API_URL nao pode ficar vazia quando MIKOPBX_API_KEY estiver configurada")

    incidents_path = Path(_env_text("INCIDENTS_DB_PATH", "data/pulsopbx.db"))
    if not incidents_path.is_absolute():
        incidents_path = PROJECT_DIR / incidents_path
    work_calendar_path = Path(_env_text("WORK_CALENDAR_PATH", "work_calendar.json"))
    if not work_calendar_path.is_absolute():
        work_calendar_path = PROJECT_DIR / work_calendar_path

    return Config(
        ami_host=ami_host,
        ami_port=_int_env("AMI_PORT", 5038, 1, 65535),
        ami_user=ami_user,
        ami_secret=ami_secret,
        ami_enabled=bool(ami_user and ami_secret),
        email_smtp_host=email_smtp_host,
        email_smtp_port=_int_env(
            "EMAIL_SMTP_PORT", 465 if email_use_ssl else 587, 1, 65535
        ),
        email_smtp_username=email_smtp_username,
        email_smtp_password=email_smtp_password,
        email_sender=email_sender,
        email_starttls=email_starttls,
        email_use_ssl=email_use_ssl,
        email_timeout_seconds=_float_env("EMAIL_SMTP_TIMEOUT_SECONDS", 10, 1, 120),
        email_recipients=email_recipients,
        # Os destinatarios globais sao opcionais: alertas reais usam o e-mail
        # individual trazido pelo perfil do ramal no MikoPBX.
        email_enabled=bool(email_smtp_host and email_sender),
        debounce_seconds=_float_env("DEBOUNCE_SECONDS", 30, 0, 86_400),
        reconcile_seconds=_float_env("RECONCILE_SECONDS", 60, 1, 86_400),
        alert_max_attempts=_int_env("ALERT_MAX_ATTEMPTS", 3, 1, 20),
        alert_retry_base_seconds=_float_env("ALERT_RETRY_BASE_SECONDS", 15, 1, 3_600),
        alert_test_cooldown_seconds=_float_env("ALERT_TEST_COOLDOWN_SECONDS", 60, 10, 3_600),
        responsible_alert_delay_seconds=_float_env(
            "RESPONSIBLE_ALERT_DELAY_SECONDS", 120, 30, 3_600
        ),
        mass_outage_threshold=_int_env("MASS_OUTAGE_THRESHOLD", 5, 2, 10_000),
        mass_outage_window_seconds=_float_env(
            "MASS_OUTAGE_WINDOW_SECONDS", 60, 10, 3_600
        ),
        incidents_db_path=incidents_path,
        work_calendar_path=work_calendar_path,
        report_minimum_workdays=_int_env("REPORT_MINIMUM_WORKDAYS", 20, 1, 366),
        dashboard_host=dashboard_host,
        dashboard_port=_int_env("DASHBOARD_PORT", 8080, 1, 65535),
        dashboard_username=dashboard_username,
        dashboard_password=dashboard_password,
        dashboard_auth_enabled=bool(dashboard_username and dashboard_password),
        demo_mode=_bool_env("DEMO_MODE", False),
        mikopbx_api_key=mikopbx_api_key,
        mikopbx_api_url=mikopbx_api_url,
        mikopbx_api_enabled=bool(mikopbx_api_key),
        mikopbx_verify_tls=_bool_env("MIKOPBX_VERIFY_TLS", False),
        mikopbx_names_refresh_seconds=_float_env(
            "MIKOPBX_NAMES_REFRESH_SECONDS", 300, 1, 86_400
        ),
    )
