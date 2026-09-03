"""Canais de notificacao e roteamento de entregas por destino."""
import logging
import smtplib
import ssl
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Protocol

from email_templates import build_email_content

logger = logging.getLogger(__name__)
DEFAULT_LOGO_PATH = Path(__file__).parent / "static" / "joinville-logo.png"


class NotificationChannel(Protocol):
    channel_name: str

    @property
    def recipients(self) -> list[str]: ...

    def notify_recipient_change(
        self,
        recipient: str,
        extension: str,
        status: str,
        timestamp: str,
        is_test: bool = False,
        context: dict | None = None,
    ) -> None: ...


class EmailNotifier:
    channel_name = "email"

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        recipients: list[str] | None = None,
        username: str | None = None,
        password: str | None = None,
        subject_brand: str = "Joinville Implementos",
        starttls: bool = True,
        use_ssl: bool = False,
        timeout_seconds: float = 10,
        logo_path: Path | str | None = None,
    ):
        self._host = host
        self._port = port
        self._sender = sender
        self._recipients = list(recipients or [])
        self._username = username
        self._password = password
        self._subject_brand = subject_brand.strip()
        self._starttls = starttls
        self._use_ssl = use_ssl
        self._timeout_seconds = timeout_seconds
        self._logo_path = Path(logo_path) if logo_path is not None else DEFAULT_LOGO_PATH

    @property
    def recipients(self) -> list[str]:
        return list(self._recipients)

    def notify_recipient_change(
        self,
        recipient: str,
        extension: str,
        status: str,
        timestamp: str,
        is_test: bool = False,
        context: dict | None = None,
    ) -> None:
        message = self._build_message(
            recipient,
            extension,
            status,
            timestamp,
            is_test,
            context,
        )

        ssl_context = ssl.create_default_context()
        if self._use_ssl:
            connection = smtplib.SMTP_SSL(
                self._host,
                self._port,
                timeout=self._timeout_seconds,
                context=ssl_context,
            )
        else:
            connection = smtplib.SMTP(
                self._host,
                self._port,
                timeout=self._timeout_seconds,
            )
        with connection as smtp:
            if self._starttls and not self._use_ssl:
                smtp.starttls(context=ssl_context)
            if self._username:
                smtp.login(self._username, self._password or "")
            smtp.send_message(message)
        logger.info("Mensagem de e-mail enviada para %s", recipient)

    def _build_message(
        self,
        recipient: str,
        extension: str,
        status: str,
        timestamp: str,
        is_test: bool = False,
        context: dict | None = None,
    ) -> EmailMessage:
        include_logo = self._logo_path.is_file()
        content = build_email_content(
            extension,
            status,
            timestamp,
            is_test,
            context,
            include_logo=include_logo,
        )
        message = EmailMessage(policy=policy.SMTP)
        message["Subject"] = f"{self._subject_brand} - {content.subject}"
        message["From"] = self._sender
        message["To"] = recipient
        sender_address = parseaddr(self._sender)[1]
        sender_domain = sender_address.rpartition("@")[2] or None
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=sender_domain)
        message["Auto-Submitted"] = "auto-generated"
        message["X-Auto-Response-Suppress"] = "All"
        if status == "offline" and not is_test:
            # Exchange/Outlook converte este cabecalho MIME na bandeira vermelha
            # de acompanhamento exibida como "Sinalizar Item".
            message["X-Message-Flag"] = "Acompanhar ramal"
        message.set_content(content.plain_text, charset="utf-8", cte="base64")
        message.add_alternative(
            content.html_text,
            subtype="html",
            charset="utf-8",
            cte="base64",
        )
        message["Content-Language"] = "pt-BR"
        if include_logo:
            html_part = message.get_payload()[-1]
            html_part.add_related(
                self._logo_path.read_bytes(),
                maintype="image",
                subtype="png",
                cid="<joinville-logo>",
                filename=self._logo_path.name,
                disposition="inline",
            )
        return message


class NotificationRouter:
    """Expoe todos os destinos como uma fila unica sem misturar implementacoes."""

    def __init__(self, channels: list[NotificationChannel]):
        self._channels = list(channels)
        self._channels_by_name = {channel.channel_name: channel for channel in self._channels}
        if len(self._channels_by_name) != len(self._channels):
            raise ValueError("Nome de canal de notificacao duplicado")
        self._bindings: dict[str, tuple[NotificationChannel, str]] = {}
        for channel in self._channels:
            for recipient in channel.recipients:
                target_id = f"{channel.channel_name}:{recipient}"
                if target_id in self._bindings:
                    raise ValueError(f"Destino de notificacao duplicado: {target_id}")
                self._bindings[target_id] = (channel, recipient)

    @property
    def recipients(self) -> list[str]:
        return list(self._bindings)

    @property
    def channel_names(self) -> list[str]:
        return [channel.channel_name for channel in self._channels]

    def can_deliver_recipient(self, recipient: str) -> bool:
        if recipient in self._bindings:
            return True
        channel_name, separator, target = recipient.partition(":")
        return bool(separator and target and channel_name in self._channels_by_name)

    def notify_recipient_change(
        self,
        recipient: str,
        extension: str,
        status: str,
        timestamp: str,
        is_test: bool = False,
        context: dict | None = None,
    ) -> None:
        binding = self._bindings.get(recipient)
        if binding is None:
            channel_name, separator, channel_recipient = recipient.partition(":")
            channel = self._channels_by_name.get(channel_name) if separator else None
            if channel is None or not channel_recipient:
                raise ValueError(f"Destino de notificacao desconhecido: {recipient}")
        else:
            channel, channel_recipient = binding
        channel.notify_recipient_change(
            channel_recipient,
            extension,
            status,
            timestamp,
            is_test,
            context,
        )
