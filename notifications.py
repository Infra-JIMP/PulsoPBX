"""Canais de notificacao e roteamento de entregas por destino."""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

logger = logging.getLogger(__name__)


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
    ) -> None: ...


def _message_parts(extension: str, status: str, timestamp: str, is_test: bool) -> tuple[str, str]:
    if is_test:
        return (
            "Teste de notificacao do PulsoPBX",
            f"O canal foi validado em {timestamp}. Nenhum ramal caiu.",
        )
    if status == "offline":
        return (
            f"Ramal {extension} indisponivel",
            f"O ramal {extension} ficou indisponivel em {timestamp}.",
        )
    return (
        f"Ramal {extension} disponivel novamente",
        f"O ramal {extension} voltou a ficar disponivel em {timestamp}.",
    )


class EmailNotifier:
    channel_name = "email"

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        recipients: list[str],
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        use_ssl: bool = False,
        timeout_seconds: float = 10,
    ):
        self._host = host
        self._port = port
        self._sender = sender
        self._recipients = list(recipients)
        self._username = username
        self._password = password
        self._starttls = starttls
        self._use_ssl = use_ssl
        self._timeout_seconds = timeout_seconds

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
    ) -> None:
        subject, body = _message_parts(extension, status, timestamp, is_test)
        message = EmailMessage()
        message["Subject"] = f"[PulsoPBX] {subject}"
        message["From"] = self._sender
        message["To"] = recipient
        message.set_content(f"{body}\n\nMensagem automatica do monitor de ramais PulsoPBX.")

        context = ssl.create_default_context()
        if self._use_ssl:
            connection = smtplib.SMTP_SSL(
                self._host,
                self._port,
                timeout=self._timeout_seconds,
                context=context,
            )
        else:
            connection = smtplib.SMTP(
                self._host,
                self._port,
                timeout=self._timeout_seconds,
            )
        with connection as smtp:
            if self._starttls and not self._use_ssl:
                smtp.starttls(context=context)
            if self._username:
                smtp.login(self._username, self._password or "")
            smtp.send_message(message)
        logger.info("Mensagem de e-mail enviada para %s", recipient)


class NotificationRouter:
    """Expoe todos os destinos como uma fila unica sem misturar implementacoes."""

    def __init__(self, channels: list[NotificationChannel]):
        self._channels = list(channels)
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
        return recipient in self._bindings

    def notify_recipient_change(
        self,
        recipient: str,
        extension: str,
        status: str,
        timestamp: str,
        is_test: bool = False,
    ) -> None:
        binding = self._bindings.get(recipient)
        if binding is None:
            raise ValueError(f"Destino de notificacao desconhecido: {recipient}")
        channel, channel_recipient = binding
        channel.notify_recipient_change(
            channel_recipient,
            extension,
            status,
            timestamp,
            is_test,
        )
