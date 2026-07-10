"""Dados de demonstracao para desenvolver/visualizar o painel sem a AMI conectada.

Ativado apenas quando DEMO_MODE=true no .env. Em producao fica desligado e o
painel usa os ramais reais vindos da AMI.
"""
import time

from state import StateTracker

# (ramal, online, ha quantos segundos esta nesse estado)
_DEMO_EXTENSIONS = [
    ("1001", True, 3 * 3600),
    ("1002", True, 26 * 3600),
    ("1003", False, 240),
    ("1004", True, 45 * 60),
    ("1005", True, 5 * 3600),
    ("1006", False, 3 * 3600 + 20 * 60),
    ("1007", True, 12 * 3600),
    ("1008", True, 90),
    ("1009", True, 8 * 3600),
    ("1010", False, 55),
    ("2001", True, 30 * 3600),
    ("2002", True, 2 * 3600),
]


DEMO_NAMES = {
    "1001": {"nome": "Recepção", "setor": "Atendimento"},
    "1002": {"nome": "João Silva", "setor": "Vendas"},
    "1003": {"nome": "Maria Souza", "setor": "Vendas"},
    "1004": {"nome": "Suporte Técnico", "setor": "TI"},
    "1005": {"nome": "Financeiro", "setor": "Financeiro"},
    "1006": {"nome": "Compras", "setor": "Compras"},
    "1007": {"nome": "Expedição", "setor": "Logística"},
    "1008": {"nome": "Carlos Lima", "setor": "Vendas"},
    "1009": {"nome": "Recursos Humanos", "setor": "Administrativo"},
    "1010": {"nome": "Almoxarifado", "setor": "Logística"},
    "2001": {"nome": "Diretoria", "setor": "Diretoria"},
    "2002": {"nome": "Ana Paula", "setor": "Marketing"},
}


def seed(tracker: StateTracker) -> None:
    now = time.time()
    for extension, online, since_seconds in _DEMO_EXTENSIONS:
        tracker.update(extension, online, now=now - since_seconds)
