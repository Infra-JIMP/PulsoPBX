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
from pathlib import Path

logger = logging.getLogger(__name__)

NAMES_FILE = Path(__file__).parent / "ramais_nomes.json"

_cache: dict = {"mtime": None, "data": {}}


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
