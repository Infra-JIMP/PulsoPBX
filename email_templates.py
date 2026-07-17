"""Conteudo de e-mail compativel com Outlook Classic e clientes modernos."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html import escape
from pathlib import Path


TEMPLATE_PATH = Path(__file__).parent / "static" / "email-notification.html"
SIGNATURE_TEXT = """Atenciosamente,

Eduardo Porangaba Leite Ribeiro da Silva
Assistente de TI Júnior
Televendas: 0800-641-1133
Fixo: (47) 3464-1133
Whats: (47) 99980-2446
Av. Celso Ramos, 4821 | Distr. Indl Sul | Garuva - SC
www.joinvilleimplementos.com.br"""


@dataclass(frozen=True)
class EmailContent:
    subject: str
    plain_text: str
    html_text: str


@lru_cache(maxsize=1)
def _template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _duration_text(context: dict) -> str:
    duration = context.get("duration_seconds")
    if not isinstance(duration, (int, float)) or duration < 0:
        return ""
    minutes = max(1, round(duration / 60))
    if minutes == 1:
        return " após aproximadamente 1 minuto útil"
    return f" após aproximadamente {minutes} minutos úteis"


def _steps_html(items: tuple[str, ...]) -> str:
    if not items:
        return ""
    rows = "".join(
        '<tr><td width="26" valign="top" style="width:26px;padding:3px 0 5px;'
        'color:#39465a;font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        f'line-height:21px;">{index}.</td><td valign="top" style="padding:3px 0 5px;'
        'color:#39465a;font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        f'line-height:21px;">{escape(item)}</td></tr>'
        for index, item in enumerate(items, 1)
    )
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        '<tr><td style="padding:21px 0 8px;color:#0b1c3d;font-family:Arial,Helvetica,'
        'sans-serif;font-size:16px;font-weight:bold;line-height:22px;">Como tentar '
        'reconectar</td></tr></table><table role="presentation" width="100%" '
        f'cellspacing="0" cellpadding="0" border="0">{rows}</table>'
    )


def _logo_cell(include_logo: bool) -> str:
    if include_logo:
        return (
            '<td width="218" valign="middle" class="signature-logo" '
            'style="width:218px;padding-right:18px;">'
            '<img src="cid:joinville-logo" width="206" height="174" '
            'alt="Joinville Implementos Rodoviários" '
            'style="display:block;width:206px;height:174px;border:0;outline:none;'
            'text-decoration:none;"></td>'
        )
    return (
        '<td width="218" valign="middle" class="signature-logo" '
        'style="width:218px;padding-right:18px;color:#2449a5;font-family:Arial,'
        'Helvetica,sans-serif;font-size:18px;font-weight:bold;line-height:22px;">'
        'JOINVILLE<br><span style="font-size:12px;font-weight:normal;">'
        'Implementos Rodoviários</span></td>'
    )


def _replace(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def build_email_content(
    extension: str,
    status: str,
    timestamp: str,
    is_test: bool = False,
    context: dict | None = None,
    include_logo: bool = True,
) -> EmailContent:
    context = context or {}
    extension = str(extension).strip()
    timestamp = str(timestamp).strip()
    name = str(context.get("nome") or "").strip()
    sector = str(context.get("setor") or "Não informado").strip()
    greeting = f"Olá, {name}." if name else "Olá."

    if is_test:
        subject = "Teste de notificação do PulsoPBX"
        eyebrow = "TESTE CONCLUÍDO"
        title = "Canal de e-mail validado"
        status_label = "Teste concluído"
        status_color = "#c65f00"
        timestamp_label = "VALIDADO EM"
        message = f"O canal foi validado em {timestamp}. Nenhum ramal caiu."
        steps: tuple[str, ...] = ()
        callout = (
            "Esta é uma mensagem de teste. Os alertas reais continuam seguindo as "
            "regras de expediente, confirmação e tolerância."
        )
    elif status == "offline":
        subject = f"Ramal {extension} desconectado"
        eyebrow = "QUEDA CONFIRMADA"
        title = "Seu ramal está desconectado"
        status_label = "Desconectado"
        status_color = "#d64232"
        timestamp_label = "DETECTADO EM"
        message = (
            "O PulsoPBX identificou que o seu ramal permanece desconectado durante "
            "o horário de trabalho. A queda foi confirmada e continuou ativa após o "
            "período de tolerância de 2 minutos."
        )
        steps = (
            "Verifique se o MicroSIP está aberto.",
            "Confirme se a internet do computador está funcionando.",
            "Confira se o ramal aparece como registrado/conectado no MicroSIP.",
        )
        callout = (
            "Se a indisponibilidade persistir, entre em contato com a equipe de TI. "
            "Caso o ramal já tenha reconectado, desconsidere esta mensagem; o retorno "
            "será registrado automaticamente."
        )
    else:
        duration_text = _duration_text(context)
        subject = f"Ramal {extension} reconectado"
        eyebrow = "CONEXÃO RESTABELECIDA"
        title = "Seu ramal voltou a ficar conectado"
        status_label = "Conectado"
        status_color = "#118b4e"
        timestamp_label = "RECONECTADO EM"
        message = (
            f"O ramal {extension} voltou a ficar conectado em {timestamp}"
            f"{duration_text}."
        )
        steps = ()
        callout = "Nenhuma ação adicional é necessária."

    plain_lines = [
        greeting,
        "",
        message,
        "",
        f"Situação: {status_label}",
        f"Ramal: {extension}",
        f"Setor: {sector}",
        f"{timestamp_label.title()}: {timestamp}",
    ]
    if steps:
        plain_lines.extend(["", "Como tentar reconectar:"])
        plain_lines.extend(f"{index}. {item}" for index, item in enumerate(steps, 1))
    plain_lines.extend(["", callout, "", SIGNATURE_TEXT])

    html_text = _replace(
        _template(),
        {
            "EYEBROW": escape(eyebrow),
            "TITLE": escape(title),
            "GREETING": escape(greeting),
            "MESSAGE": escape(message),
            "STATUS_LABEL": escape(status_label),
            "STATUS_COLOR": status_color,
            "EXTENSION": escape(extension),
            "SECTOR": escape(sector),
            "TIMESTAMP_LABEL": escape(timestamp_label),
            "TIMESTAMP": escape(timestamp),
            "STEPS": _steps_html(steps),
            "CALLOUT": escape(callout),
            "LOGO_CELL": _logo_cell(include_logo),
        },
    )
    return EmailContent(subject, "\n".join(plain_lines), html_text)
