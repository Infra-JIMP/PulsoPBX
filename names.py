"""Carrega o mapeamento ramal -> nome/setor de ramais_nomes.json.

Formato aceito (cada valor pode ser uma string ou um objeto):

    {
      "1001": "Recepção",
      "1002": {"nome": "João Silva", "setor": "Vendas"}
    }

O arquivo e recarregado automaticamente quando muda (cache por mtime), entao da
para editar os nomes sem reiniciar o servico.
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
            data[str(extension)] = {"nome": value, "setor": ""}
        elif isinstance(value, dict):
            data[str(extension)] = {
                "nome": value.get("nome", ""),
                "setor": value.get("setor", ""),
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
