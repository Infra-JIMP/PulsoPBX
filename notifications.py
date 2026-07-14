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
        context: dict | None = None,
    ) -> None: ...


def _message_parts(
    extension: str,
    status: str,
    timestamp: str,
    is_test: bool,
    context: dict | None = None,
) -> tuple[str, str]:
    context = context or {}
    name = str(context.get("nome") or "").strip()
    greeting = f"Olá, {name}.\n\n" if name else "Olá.\n\n"
    if is_test:
        return (
            "Teste de notificação do PulsoPBX",
            f"O canal foi validado em {timestamp}. Nenhum ramal caiu.",
        )
    if status == "offline":
        return (
            f"Ramal {extension} desconectado",
            greeting
            + f"O PulsoPBX identificou que o ramal {extension} ficou desconectado "
            f"em {timestamp}, durante o expediente.\n\n"
            "Para voltar a receber e realizar chamadas, verifique se o MicroSIP está "
            "aberto e conectado, se a internet está funcionando e se o ramal aparece "
            "como registrado. Se a indisponibilidade persistir, procure a equipe de TI.",
        )
    duration = context.get("duration_seconds")
    duration_text = ""
    if isinstance(duration, (int, float)) and duration >= 0:
        minutes = max(1, round(duration / 60))
        duration_text = (
            " após aproximadamente 1 minuto"
            if minutes == 1
            else f" após aproximadamente {minutes} minutos"
        )
    return (
        f"Ramal {extension} reconectado",
        greeting
        + f"O ramal {extension} voltou a ficar conectado em {timestamp}{duration_text}. "
        "Nenhuma ação adicional é necessária.",
    )


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
        starttls: bool = True,
        use_ssl: bool = False,
        timeout_seconds: float = 10,
    ):
        self._host = host
        self._port = port
        self._sender = sender
        self._recipients = list(recipients or [])
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
        context: dict | None = None,
    ) -> None:
        subject, body = _message_parts(extension, status, timestamp, is_test, context)
        message = EmailMessage()
        message["Subject"] = f"[PulsoPBX] {subject}"
        message["From"] = self._sender
        message["To"] = recipient
        message.set_content(f"{body}\n\nMensagem automática do monitor de ramais PulsoPBX.")

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
