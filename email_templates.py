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


def _steps_html(items: tuple[str, ...], heading: str = "Como tentar reconectar") -> str:
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
        f'sans-serif;font-size:16px;font-weight:bold;line-height:22px;">{escape(heading)}'
        '</td></tr></table><table role="presentation" width="100%" '
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
    # Avisos de queda e retorno vao para a equipe de TI, entao falam do ramal
    # na terceira pessoa; boas-vindas e teste continuam falando com a pessoa.
    for_operations = context.get("audience") == "operations"
    owner = f"{name} · {sector}" if name else f"setor {sector}"
    greeting = "Olá, equipe." if for_operations else (f"Olá, {name}." if name else "Olá.")
    steps_heading = "Como tentar reconectar"
    # Selo do cabecalho: aviso por padrao, confirmacao nas mensagens positivas.
    icon, icon_background, icon_border = "!", "#fff0ed", "#ffd0c8"

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
    elif context.get("event_type") == "welcome":
        role = str(context.get("cargo") or "").strip()
        extension_text = extension or "ainda não atribuído"
        introduction = f"Você foi cadastrado(a) como {role}" if role else "Você foi cadastrado(a)"
        subject = (
            f"Bem-vindo(a) - seu ramal é {extension}"
            if extension
            else "Bem-vindo(a) - seu cadastro de telefonia"
        )
        eyebrow = "CADASTRO CONCLUÍDO"
        title = "Boas-vindas: seu ramal está ativo"
        status_label = "Cadastro ativo"
        status_color = "#118b4e"
        timestamp_label = "CADASTRADO EM"
        message = (
            f"{introduction} no setor {sector}, com o ramal {extension_text}. "
            "A partir de agora o PulsoPBX acompanha a conexão do seu ramal durante "
            "o expediente e avisa você por e-mail se ele ficar fora do ar."
        )
        steps_heading = "Primeiros passos"
        steps = tuple(context.get("steps") or ())
        icon, icon_background, icon_border = "✓", "#eef9f2", "#bfe3cd"
        callout = (
            "Guarde este e-mail: ele traz o seu ramal e os códigos de atalho. "
            "Em caso de dúvida ou problema na conexão, procure a equipe de TI."
        )
    elif context.get("event_type") == "missed_call":
        caller = str(context.get("caller") or "Numero nao identificado").strip()
        caller_name = str(context.get("caller_name") or "").strip()
        caller_text = f"{caller} ({caller_name})" if caller_name else caller
        subject = f"Chamada perdida no ramal {extension}"
        eyebrow = "CHAMADA PERDIDA"
        title = "Voce recebeu uma chamada nao atendida"
        status_label = "Chamada perdida"
        status_color = "#d64232"
        timestamp_label = "RECEBIDA EM"
        message = (
            f"O PulsoPBX identificou uma chamada interna nao atendida no ramal {extension}. "
            f"Origem: {caller_text}."
        )
        steps = ()
        callout = "Se necessario, retorne a ligacao pelo numero informado."
    elif status == "offline":
        subject = (
            f"Ramal {extension} desconectado - {name}"
            if for_operations and name
            else f"Ramal {extension} desconectado"
        )
        eyebrow = "QUEDA CONFIRMADA"
        status_label = "Desconectado"
        status_color = "#d64232"
        timestamp_label = "DETECTADO EM"
        if for_operations:
            title = f"Ramal {extension} está desconectado"
            message = (
                f"O PulsoPBX confirmou que o ramal {extension} ({owner}) permanece "
                "desconectado durante o horário de trabalho. A queda passou pelo "
                "período de confirmação e pela tolerância antes deste aviso."
            )
            steps_heading = "O que verificar"
            steps = (
                "Confirme no painel se a queda continua ativa.",
                "Verifique a rede e o MicroSIP do computador do colaborador.",
                "Confira se o ramal aparece registrado na central.",
            )
            callout = (
                "Este aviso vai somente para a equipe de TI; o colaborador não "
                "recebe cópia. O retorno do ramal é registrado automaticamente."
            )
        else:
            title = "Seu ramal está desconectado"
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
        subject = (
            f"Ramal {extension} reconectado - {name}"
            if for_operations and name
            else f"Ramal {extension} reconectado"
        )
        eyebrow = "CONEXÃO RESTABELECIDA"
        status_label = "Conectado"
        status_color = "#118b4e"
        timestamp_label = "RECONECTADO EM"
        title = (
            f"Ramal {extension} voltou a ficar conectado"
            if for_operations
            else "Seu ramal voltou a ficar conectado"
        )
        detail = f" ({owner})" if for_operations else ""
        message = (
            f"O ramal {extension}{detail} voltou a ficar conectado em {timestamp}"
            f"{duration_text}."
        )
        steps = ()
        callout = (
            "Incidente encerrado. Nenhuma ação adicional é necessária."
            if for_operations
            else "Nenhuma ação adicional é necessária."
        )
        icon, icon_background, icon_border = "✓", "#eef9f2", "#bfe3cd"

    plain_lines = [
        greeting,
        "",
        message,
        "",
        f"Situação: {status_label}",
        f"Ramal: {extension or 'Não atribuído'}",
        f"Setor: {sector}",
        f"{timestamp_label.title()}: {timestamp}",
    ]
    if steps:
        plain_lines.extend(["", f"{steps_heading}:"])
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
            "ICON": escape(icon),
            "ICON_BACKGROUND": icon_background,
            "ICON_BORDER": icon_border,
            "EXTENSION": escape(extension or "Não atribuído"),
            "SECTOR": escape(sector),
            "TIMESTAMP_LABEL": escape(timestamp_label),
            "TIMESTAMP": escape(timestamp),
            "STEPS": _steps_html(steps, steps_heading),
            "CALLOUT": escape(callout),
            "LOGO_CELL": _logo_cell(include_logo),
        },
    )
    return EmailContent(subject, "\n".join(plain_lines), html_text)
