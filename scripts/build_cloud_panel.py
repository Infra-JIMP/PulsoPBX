"""Gera o painel da nuvem (cloud/public/index.html) a partir do painel local.

O visual e o comportamento sao os mesmos de static/index.html. Na nuvem ficam
desligadas as areas que dependem de dados que nao saem da rede (relatorios,
historico de chamadas, alertas, cadastros e listas), e a atualizacao passa de
3s para 15s - o mesmo tempo do cache da CDN, entao consultar mais rapido nao
traria dado novo. Com a aba escondida, nao ha consulta nenhuma.

Uso:  .venv\\Scripts\\python.exe -m scripts.build_cloud_panel
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "static" / "index.html"
PUBLIC = ROOT / "cloud" / "public"

CLOUD_CSS = """
<style id="pulso-cloud">
  /* Areas que dependem do historico local nao existem na nuvem. */
  .availability-report, section.activity, #calls-open, #profile-tab-history,
  #profile-panel-history, .app-nav { display: none !important; }
  /* E-mail desligado de proposito: a coluna "Alerta" so repetiria isso. */
  .table-wrap table th:nth-child(5), .table-wrap table td:nth-child(5) { display: none; }
  .table-wrap table { min-width: 720px; }
  .cloud-logout { display: inline-flex; align-items: center; gap: 6px; padding: 7px 13px;
    border: 1px solid var(--border-strong); border-radius: 999px; color: var(--muted);
    background: rgba(255,255,255,.05); text-decoration: none; font-size: .78rem; font-weight: 650; }
  .cloud-logout:hover { color: var(--text); border-color: rgba(255,177,107,.45); }
</style>
"""

# (trecho original, substituicao) - cada trecho precisa existir exatamente uma vez.
PATCHES = [
    ("</head>", CLOUD_CSS + "</head>"),
    (
        'notifyText.textContent = "E-mail não configurado";',
        'notifyText.textContent = CLOUD_MODE ? "Painel em nuvem · somente leitura" : "E-mail não configurado";',
    ),
    ("const POLL_MS = 3000;", "const POLL_MS = 15000; const CLOUD_MODE = true;"),
    (
        'const res = await fetch("/api/status", { cache: "no-store" }); if (!res.ok)',
        'const res = await fetch("/api/status", { cache: "no-store" }); '
        'if (res.status === 401) { location.href = "/login"; return; } if (!res.ok)',
    ),
    ("async function refreshReport() {", "async function refreshReport() {\n  if (CLOUD_MODE) return;"),
    ("async function loadProfileReport() {", "async function loadProfileReport() {\n  if (CLOUD_MODE) return;"),
    ("async function loadCallHistory() {", "async function loadCallHistory() {\n  if (CLOUD_MODE) return;"),
    (
        "async function loadDirectoryPublic(force = false) {",
        "async function loadDirectoryPublic(force = false) {\n  if (CLOUD_MODE) return;",
    ),
    (
        '<span class="clock" id="clock">--:--:--</span>',
        '<span class="clock" id="clock">--:--:--</span>'
        '<a class="cloud-logout" href="/api/logout">Sair</a>',
    ),
    (
        "setInterval(refresh, POLL_MS);",
        "setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS); "
        'document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });',
    ),
]


def build() -> Path:
    html = SOURCE.read_text(encoding="utf-8")
    for original, replacement in PATCHES:
        count = html.count(original)
        if count != 1:
            raise SystemExit(
                f"O painel local mudou: trecho encontrado {count}x, esperado 1x:\n  {original[:90]}"
            )
        html = html.replace(original, replacement)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    target = PUBLIC / "index.html"
    target.write_text(html, encoding="utf-8")
    (PUBLIC / "assets").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "static" / "pulsopbx-logo.png", PUBLIC / "assets" / "pulsopbx-logo.png")
    shutil.copyfile(ROOT / "static" / "favicon.ico", PUBLIC / "favicon.ico")
    return target


if __name__ == "__main__":
    print(f"Painel da nuvem gerado em {build()}")
