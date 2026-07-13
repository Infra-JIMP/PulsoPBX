"""Envio de alertas via WhatsApp Cloud API (Meta Graph API)."""
import logging
import re
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10


class WhatsAppNotificationError(RuntimeError):
    """Falha transitória ou definitiva ao entregar uma mensagem ao WhatsApp."""


class WhatsAppNotifier:
    def __init__(
        self,
        token: str,
        phone_number_id: str,
        graph_api_version: str,
        template_name: str,
        use_template: bool,
        recipients: list[str],
    ):
        graph_api_version = graph_api_version.strip()
        if not re.fullmatch(r"v\d+\.\d+", graph_api_version):
            raise ValueError("WHATSAPP_GRAPH_API_VERSION deve seguir o formato v25.0")
        self._token = token
        self._url = f"https://graph.facebook.com/{graph_api_version}/{phone_number_id}/messages"
        self._template_name = template_name
        self._use_template = use_template
        self._recipients = recipients

    @property
    def recipients(self) -> list[str]:
        return list(self._recipients)

    def _post(self, payload: dict) -> None:
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            response = requests.post(self._url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            logger.warning("Falha de rede ao enviar WhatsApp para %s: %s", payload["to"], exc)
            raise WhatsAppNotificationError(str(exc)) from exc
        if response.status_code >= 400:
            logger.error("Falha ao enviar WhatsApp: %s - %s", response.status_code, response.text)
            raise WhatsAppNotificationError(f"HTTP {response.status_code}")
        else:
            logger.info("Mensagem WhatsApp enviada para %s", payload["to"])

    def _send_template(self, to: str, extension: str, status_text: str, timestamp: str) -> None:
        self._post(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": self._template_name,
                    "language": {"code": "pt_BR"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": extension},
                                {"type": "text", "text": status_text},
                                {"type": "text", "text": timestamp},
                            ],
                        }
                    ],
                },
            }
        )

    def _send_text(self, to: str, extension: str, status_text: str, timestamp: str) -> None:
        self._post(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": f"Alerta de ramal: o ramal {extension} {status_text} em {timestamp}."},
            }
        )

    def notify_recipient_change(
        self,
        recipient: str,
        extension: str,
        status: str,
        timestamp: str,
        is_test: bool = False,
    ) -> None:
        if is_test:
            status_text = "foi usado em um teste manual do PulsoPBX; nenhum ramal caiu"
        else:
            status_text = "ficou indisponivel" if status == "offline" else "voltou a ficar disponivel"
        if self._use_template:
            self._send_template(recipient, extension, status_text, timestamp)
        else:
            self._send_text(recipient, extension, status_text, timestamp)

    def notify_extension_change(self, extension: str, status: str) -> None:
        """Compatibilidade para chamadas manuais; o monitor usa a fila em alerts.py."""
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        for recipient in self._recipients:
            self.notify_recipient_change(recipient, extension, status, timestamp)
