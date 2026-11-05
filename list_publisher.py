"""Publicacao das listas corporativas (ramais e e-mails) em disco.

Nao ha verificacao periodica: as listas sao regravadas somente quando o
cadastro muda - contratacao, desligamento, troca de cargo/setor/ramal ou de
e-mail (pela tela administrativa ou vindo do MikoPBX). Assim cada pessoa
aparece sempre com o proprio e-mail atual, inclusive depois de uma troca de
dominio, sem nenhum custo enquanto nada muda.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from pathlib import Path

from directory_exports import build_directory_workbook

logger = logging.getLogger(__name__)

LIST_FILES = {
    "extensions": "lista-ramais-atualizada.xlsx",
    "emails": "lista-emails-atualizada.xlsx",
}
_PERSON_FIELDS = (
    "name",
    "email",
    "extension",
    "sector",
    "sector_short",
    "sector_sort",
    "sort_order",
)


class ListPublisher:
    def __init__(self, directory, output_dir: Path):
        self._directory = directory
        self._output_dir = Path(output_dir)
        self._last_fingerprint: str | None = None
        self._lock = threading.Lock()

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def publish_if_changed(self, reason: str = "cadastro alterado") -> bool:
        """Grava as duas listas se o conteudo mudou ou se algum arquivo sumiu.

        Alteracoes que nao aparecem nas listas (ex.: preferencia de aviso)
        nao regravam nada.
        """
        with self._lock:
            people = self._directory.list_people(False)
            quick_dials = self._directory.list_quick_dials()
            fingerprint = self._fingerprint(people, quick_dials)
            files_present = all(
                (self._output_dir / name).is_file() for name in LIST_FILES.values()
            )
            if fingerprint == self._last_fingerprint and files_present:
                return False

            self._output_dir.mkdir(parents=True, exist_ok=True)
            for kind, filename in LIST_FILES.items():
                content = build_directory_workbook(kind, people, quick_dials)
                self._write_atomically(self._output_dir / filename, content)
            self._last_fingerprint = fingerprint
        logger.info(
            "Listas corporativas atualizadas em %s (%s; %d colaborador(es) ativo(s))",
            self._output_dir,
            reason,
            len(people),
        )
        return True

    async def publish(self, reason: str = "cadastro alterado") -> bool:
        """Versao assincrona que nunca derruba quem chamou (cadastro ja foi salvo)."""
        try:
            return await asyncio.to_thread(self.publish_if_changed, reason)
        except Exception:
            # Pasta indisponivel ou arquivo aberto no Excel: o cadastro segue salvo
            # e a proxima alteracao regrava as listas.
            logger.exception("Nao foi possivel atualizar as listas corporativas")
            return False

    @staticmethod
    def _fingerprint(people: list[dict], quick_dials: list[dict]) -> str:
        payload = {
            "people": [
                {field: person.get(field) for field in _PERSON_FIELDS}
                for person in people
                if person.get("active")
            ],
            "quick_dials": [
                {"code": dial.get("code"), "label": dial.get("label")}
                for dial in quick_dials
            ],
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _write_atomically(target: Path, content: bytes) -> None:
        # Quem estiver abrindo a lista nunca ve um arquivo pela metade.
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, target)
