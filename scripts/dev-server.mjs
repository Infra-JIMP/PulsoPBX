// Servidor de desenvolvimento local para o painel em nuvem (api/index.js).
//
// O painel roda no Vercel como uma Serverless Function. Para trabalhar nele
// localmente sem depender do Vercel CLI, este script sobe um servidor HTTP
// nativo do Node e entrega cada requisicao ao mesmo handler que o Vercel usa.
//
// Diferencas propositais em relacao a producao (apenas para desenvolvimento):
//   - Os arquivos estaticos de static/ (/, favicon, /assets/*) sao servidos
//     direto, sem HTTP Basic e sem exigir banco. Assim da para iterar no
//     front mesmo sem DATABASE_URL configurada.
//   - Todo o resto (/, /api/*) passa pelo handler real de api/index.js.
//
// Uso: npm run dev
// Variaveis: DEV_HOST (padrao 127.0.0.1), DEV_PORT/PORT (padrao 3000).

import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";

import handler from "../api/index.js";

const host = process.env.DEV_HOST || "127.0.0.1";
const port = Number(process.env.DEV_PORT || process.env.PORT || 3000);
const STATIC_DIR = path.join(process.cwd(), "static");

// Mesmo mapa de arquivos que api/index.js -> serveStatic().
const STATIC_FILES = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/favicon.ico": ["favicon.ico", "image/x-icon"],
  "/assets/pulsopbx-logo.png": ["pulsopbx-logo.png", "image/png"],
  "/assets/joinville-logo.png": ["joinville-logo.png", "image/png"],
};

async function serveStaticDirect(response, pathname) {
  const target = STATIC_FILES[pathname];
  if (!target) return false;
  try {
    const content = await fs.readFile(path.join(STATIC_DIR, target[0]));
    response.statusCode = 200;
    response.setHeader("Content-Type", target[1]);
    response.setHeader("Cache-Control", "no-store");
    response.end(content);
  } catch (error) {
    response.statusCode = 404;
    response.setHeader("Content-Type", "text/plain; charset=utf-8");
    response.end(`Arquivo estatico ausente: static/${target[0]}`);
  }
  return true;
}

const REQUIRED_ENV = ["DATABASE_URL", "DASHBOARD_USERNAME", "DASHBOARD_PASSWORD"];
const missing = REQUIRED_ENV.filter((name) => !process.env[name]);
if (missing.length) {
  console.warn(
    `[dev] Aviso: variaveis ausentes -> ${missing.join(", ")}.\n` +
      "[dev] O front (static/) carrega mesmo assim; as rotas /api/* vao\n" +
      "[dev] responder 401/500 ate elas serem preenchidas no .env\n" +
      "[dev] (rode `vercel env pull .env` para trazer as da nuvem).",
  );
}

const server = http.createServer(async (request, response) => {
  const started = Date.now();
  try {
    const { pathname } = new URL(request.url, "http://localhost");
    if (request.method === "GET" && (await serveStaticDirect(response, pathname))) {
      return;
    }
    await handler(request, response);
  } catch (error) {
    console.error("[dev] Falha ao processar a requisicao:", error);
    if (!response.headersSent) {
      response.statusCode = 500;
      response.setHeader("Content-Type", "application/json; charset=utf-8");
    }
    if (!response.writableEnded) {
      response.end(JSON.stringify({ ok: false, error: "Falha interna no servidor de desenvolvimento" }));
    }
  } finally {
    if (!response.writableEnded) response.end();
    const ms = Date.now() - started;
    console.log(`[dev] ${request.method} ${request.url} -> ${response.statusCode} (${ms}ms)`);
  }
});

server.listen(port, host, () => {
  console.log(`[dev] PulsoPBX rodando em http://${host}:${port}/`);
  console.log("[dev] Ctrl+C para encerrar.");
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    console.log(`\n[dev] ${signal} recebido, encerrando...`);
    server.close(() => process.exit(0));
  });
}
