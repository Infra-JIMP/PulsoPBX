"""Carrega excecoes locais de nome, setor e notificacao por ramal.

Formato aceito (cada valor pode ser uma string ou um objeto):

    {
      "1001": "Recepção",
      "1002": {
        "nome": "João Silva",
        "setor": "Vendas",
        "email": "joao@empresa.com.br",
        "notificar": true
      }
    }

O arquivo e recarregado automaticamente quando muda (cache por mtime), entao da
para editar os dados sem reiniciar o servico. O MikoPBX continua sendo a fonte
principal; este arquivo serve apenas para sobrescritas pontuais.
"""
import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

NAMES_FILE = Path(__file__).parent / "ramais_nomes.json"

_cache: dict = {"mtime": None, "data": {}}
_write_lock = threading.Lock()


def _normalize(raw: dict) -> dict:
    data = {}
    for extension, value in raw.items():
        if isinstance(value, str):
            data[str(extension)] = {
                "nome": value,
                "setor": "",
                "email": "",
                "notificar": True,
            }
        elif isinstance(value, dict):
            data[str(extension)] = {
                "nome": value.get("nome", ""),
                "setor": value.get("setor", ""),
                "email": str(value.get("email", "") or "").strip().lower(),
                "notificar": value.get("notificar", True) is not False,
            }
    return data


def load_names() -> dict:
    try:
        mtime = NAMES_FILE.stat().st_mtime
    except FileNotFoundError:
        return {}

    if _cache["mtime"] != mtime:
        try:
            raw = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
            _cache["data"] = _normalize(raw)
            _cache["mtime"] = mtime
        except Exception:
            logger.exception("Falha ao ler %s - mantendo nomes anteriores", NAMES_FILE.name)
    return _cache["data"]


def _read_raw() -> dict:
    try:
        raw = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"Nao foi possivel ler {NAMES_FILE.name}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{NAMES_FILE.name} deve conter um objeto JSON")
    return raw


def _write_raw(raw: dict) -> None:
    NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = NAMES_FILE.with_name(f".{NAMES_FILE.name}.tmp")
    content = json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, NAMES_FILE)
    finally:
        temporary.unlink(missing_ok=True)
    _cache["mtime"] = None


def save_email_override(
    extension: str,
    email: str,
    notify: bool = True,
    sector: str | None = None,
) -> dict:
    """Salva o destinatario e, quando informado, o setor local do ramal.

    ``sector=None`` preserva compatibilidade com chamadas antigas e mantem o
    setor atual. Uma string vazia remove somente a sobrescrita local do setor.
    """
    extension = str(extension).strip()
    if not extension:
        raise ValueError("Ramal obrigatorio")
    with _write_lock:
        raw = _read_raw()
        existing = raw.get(extension, {})
        if isinstance(existing, str):
            existing = {"nome": existing}
        elif not isinstance(existing, dict):
            existing = {}
        existing["email"] = str(email).strip().lower()
        existing["notificar"] = bool(notify)
        if sector is not None:
            normalized_sector = " ".join(str(sector).split())
            if normalized_sector:
                existing["setor"] = normalized_sector
            else:
                existing.pop("setor", None)
        raw[extension] = existing
        _write_raw(raw)
        return _normalize({extension: existing})[extension]


def clear_email_override(extension: str) -> bool:
    """Remove somente a excecao de e-mail e volta a herdar o MikoPBX."""
    extension = str(extension).strip()
    with _write_lock:
        raw = _read_raw()
        existing = raw.get(extension)
        if not isinstance(existing, dict):
            return False
        changed = "email" in existing or "notificar" in existing
        existing.pop("email", None)
        existing.pop("notificar", None)
        if existing:
            raw[extension] = existing
        else:
            raw.pop(extension, None)
        if changed:
            _write_raw(raw)
        return changed
