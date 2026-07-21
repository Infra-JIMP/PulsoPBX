import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { neon } from "@neondatabase/serverless";

const STATIC_DIR = path.join(process.cwd(), "static");
const ALLOWED_PERIODS = new Set([7, 30, 90]);
let schemaReady = false;

function sendJson(response, status, payload) {
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Cache-Control", "no-store");
  response.end(JSON.stringify(payload));
}

function secretEquals(candidate, expected) {
  if (!candidate || !expected) return false;
  const left = Buffer.from(String(candidate));
  const right = Buffer.from(String(expected));
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

function bearerAuthorized(request) {
  const value = String(request.headers.authorization || "");
  return value.startsWith("Bearer ") && secretEquals(value.slice(7), process.env.SYNC_TOKEN);
}

function dashboardAuthorized(request) {
  const expectedUser = process.env.DASHBOARD_USERNAME;
  const expectedPassword = process.env.DASHBOARD_PASSWORD;
  if (!expectedUser || !expectedPassword) return false;
  const value = String(request.headers.authorization || "");
  if (!value.startsWith("Basic ")) return false;
  try {
    const decoded = Buffer.from(value.slice(6), "base64").toString("utf8");
    const split = decoded.indexOf(":");
    return split > 0
      && secretEquals(decoded.slice(0, split), expectedUser)
      && secretEquals(decoded.slice(split + 1), expectedPassword);
  } catch {
    return false;
  }
}

function adminAuthorized(request) {
  return secretEquals(
    request.headers["x-pulsopbx-admin"],
    process.env.RESPONSIBLES_ADMIN_PASSWORD,
  ) && request.headers["x-pulsopbx-action"] === "manage-responsibles";
}

function getDatabase() {
  if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL nao configurada");
  return neon(process.env.DATABASE_URL);
}

async function ensureSchema(sql) {
  if (schemaReady) return;
  await sql`
    CREATE TABLE IF NOT EXISTS pulsopbx_cloud_state (
      id TEXT PRIMARY KEY,
      payload JSONB NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
  `;
  await sql`
    CREATE TABLE IF NOT EXISTS pulsopbx_cloud_commands (
      id BIGSERIAL PRIMARY KEY,
      method TEXT NOT NULL,
      path TEXT NOT NULL,
      body JSONB NOT NULL DEFAULT '{}'::jsonb,
      status TEXT NOT NULL DEFAULT 'pending',
      result JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      delivered_at TIMESTAMPTZ,
      completed_at TIMESTAMPTZ
    )
  `;
  schemaReady = true;
}

async function readBody(request) {
  if (request.body && typeof request.body === "object") return request.body;
  let body = "";
  for await (const chunk of request) {
    body += chunk;
    if (body.length > 5_000_000) throw new Error("Payload excede o limite permitido");
  }
  return body ? JSON.parse(body) : {};
}

async function latestState(sql) {
  const rows = await sql`
    SELECT payload, EXTRACT(EPOCH FROM updated_at) AS updated_at
    FROM pulsopbx_cloud_state WHERE id = 'primary' LIMIT 1
  `;
  return rows[0] || null;
}

function periodFrom(url) {
  const value = Number(url.searchParams.get("days") || 30);
  return ALLOWED_PERIODS.has(value) ? value : null;
}

function callsForPeriod(state, days) {
  const all = state?.payload?.calls?.calls || [];
  const cutoff = Date.now() / 1000 - days * 86400;
  return all.filter((call) => Number(call.started_at || 0) >= cutoff);
}

function summarizeCalls(calls) {
  const summary = {
    total: calls.length,
    answered: 0,
    not_answered: 0,
    busy: 0,
    failed: 0,
    answer_rate_percent: null,
    instability_count: 0,
  };
  for (const call of calls) {
    const disposition = String(call.disposition || "").toUpperCase();
    if (disposition === "ANSWERED") summary.answered += 1;
    else if (disposition === "NOANSWER") summary.not_answered += 1;
    else if (disposition === "BUSY") summary.busy += 1;
    else if (disposition === "FAILED" || disposition === "CHANUNAVAIL") summary.failed += 1;
    if (call.connection_status !== "stable") summary.instability_count += 1;
  }
  if (summary.total) {
    summary.answer_rate_percent = Math.round(summary.answered / summary.total * 1000) / 10;
  }
  return summary;
}

function saoPauloDate(timestamp) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Sao_Paulo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(timestamp * 1000));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function extensionHistory(state, extension, days) {
  const calls = callsForPeriod(state, days).filter(
    (call) => String(call.destination_extension || "") === extension,
  );
  const summary = summarizeCalls(calls);
  const answeredDurations = calls
    .filter((call) => call.disposition === "ANSWERED")
    .map((call) => Number(call.talk_seconds || 0));
  summary.average_talk_seconds = answeredDurations.length
    ? Math.round(answeredDurations.reduce((total, value) => total + value, 0) / answeredDurations.length)
    : 0;

  const byDay = new Map();
  for (const call of calls) {
    const key = saoPauloDate(Number(call.started_at));
    const item = byDay.get(key) || { date: key, received: 0, answered: 0 };
    item.received += 1;
    item.answered += call.disposition === "ANSWERED" ? 1 : 0;
    byDay.set(key, item);
  }
  const daily = [];
  const now = new Date();
  for (let distance = days - 1; distance >= 0; distance -= 1) {
    const point = new Date(now.getTime() - distance * 86400000);
    const key = saoPauloDate(point.getTime() / 1000);
    daily.push(byDay.get(key) || { date: key, received: 0, answered: 0 });
  }
  const people = state?.payload?.directory?.people || [];
  const person = people.find((item) => String(item.extension || "") === extension) || {};
  return {
    ok: true,
    extension,
    name: person.name || "",
    sector: person.sector || "",
    days,
    summary,
    daily,
    calls: calls.slice(0, 200),
    generated_at: Date.now() / 1000,
  };
}

async function serveStatic(response, pathname) {
  const files = {
    "/": ["index.html", "text/html; charset=utf-8"],
    "/favicon.ico": ["favicon.ico", "image/x-icon"],
    "/assets/pulsopbx-logo.png": ["pulsopbx-logo.png", "image/png"],
    "/assets/joinville-logo.png": ["joinville-logo.png", "image/png"],
  };
  const target = files[pathname];
  if (!target) return false;
  const content = await fs.readFile(path.join(STATIC_DIR, target[0]));
  response.statusCode = 200;
  response.setHeader("Content-Type", target[1]);
  response.setHeader("Cache-Control", pathname === "/" ? "no-store" : "public, max-age=86400");
  response.end(content);
  return true;
}

async function queueCommand(sql, request, url) {
  const isTestAlert = url.pathname === "/api/alerts/test";
  const testAlertAuthorized = isTestAlert
    && request.headers["x-pulsopbx-action"] === "test-alert";
  if (!testAlertAuthorized && !adminAuthorized(request)) {
    return { status: 401, payload: { ok: false, error: "Senha administrativa invalida" } };
  }
  const body = await readBody(request);
  const rows = await sql`
    INSERT INTO pulsopbx_cloud_commands(method, path, body)
    VALUES (${request.method}, ${url.pathname}, ${JSON.stringify(body)}::jsonb)
    RETURNING id
  `;
  return {
    status: 202,
    payload: { ok: true, queued: true, command_id: String(rows[0].id) },
  };
}

async function handleSync(sql, request) {
  if (!bearerAuthorized(request)) {
    return { status: 401, payload: { ok: false, error: "Token de sincronizacao invalido" } };
  }
  const body = await readBody(request);
  if (!body || body.source !== "pulsopbx-local" || typeof body.status !== "object") {
    return { status: 400, payload: { ok: false, error: "Snapshot invalido" } };
  }
  for (const result of body.command_results || []) {
    const id = Number(result.id);
    if (!Number.isSafeInteger(id)) continue;
    await sql`
      UPDATE pulsopbx_cloud_commands
      SET status = ${result.ok ? "completed" : "failed"},
          result = ${JSON.stringify(result)}::jsonb,
          completed_at = NOW()
      WHERE id = ${id}
    `;
  }
  const snapshot = { ...body };
  delete snapshot.command_results;
  await sql`
    INSERT INTO pulsopbx_cloud_state(id, payload, updated_at)
    VALUES ('primary', ${JSON.stringify(snapshot)}::jsonb, NOW())
    ON CONFLICT(id) DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
  `;
  const commands = await sql`
    SELECT id, method, path, body
    FROM pulsopbx_cloud_commands
    WHERE status = 'pending'
       OR (status = 'delivered' AND delivered_at < NOW() - INTERVAL '2 minutes')
    ORDER BY id ASC LIMIT 20
  `;
  if (commands.length) {
    for (const command of commands) {
      await sql`
        UPDATE pulsopbx_cloud_commands
        SET status = 'delivered', delivered_at = NOW()
        WHERE id = ${Number(command.id)}
      `;
    }
  }
  return {
    status: 200,
    payload: {
      ok: true,
      commands: commands.map((item) => ({ ...item, id: String(item.id) })),
    },
  };
}

export default async function handler(request, response) {
  const url = new URL(request.url, "https://pulsopbx.invalid");
  try {
    if (url.pathname === "/api/health") {
      const sql = getDatabase();
      await ensureSchema(sql);
      const state = await latestState(sql);
      const age = state ? Math.max(0, Date.now() / 1000 - Number(state.updated_at)) : null;
      return sendJson(response, 200, {
        ready: true,
        cloud: "connected",
        source: state ? (age <= 90 ? "online" : "stale") : "waiting",
        source_age_seconds: age,
        ami: state?.payload?.status?.ami_status || "not_available",
      });
    }

    const sql = getDatabase();
    await ensureSchema(sql);
    if (url.pathname === "/api/sync" && request.method === "POST") {
      const result = await handleSync(sql, request);
      return sendJson(response, result.status, result.payload);
    }
    if (!dashboardAuthorized(request)) {
      response.statusCode = 401;
      response.setHeader("WWW-Authenticate", 'Basic realm="PulsoPBX", charset="UTF-8"');
      return response.end("Autenticacao obrigatoria");
    }
    if (await serveStatic(response, url.pathname)) return;

    const state = await latestState(sql);
    if (!state) {
      return sendJson(response, 503, { ok: false, error: "Aguardando a primeira sincronizacao local" });
    }
    if (url.pathname === "/api/status" && request.method === "GET") {
      const payload = structuredClone(state.payload.status || {});
      payload.cloud = { synchronized_at: Number(state.updated_at), provider: "vercel" };
      return sendJson(response, 200, payload);
    }
    if (url.pathname === "/api/directory" && request.method === "GET") {
      return sendJson(response, 200, state.payload.directory || { people: [], sectors: [] });
    }
    if (url.pathname === "/api/admin/directory" && request.method === "GET") {
      if (!adminAuthorized(request)) {
        return sendJson(response, 401, { ok: false, error: "Senha administrativa invalida" });
      }
      return sendJson(response, 200, state.payload.admin_directory || { people: [], changes: [] });
    }
    if (
      url.pathname === "/api/admin/directory"
      || url.pathname.startsWith("/api/admin/directory/")
      || url.pathname === "/api/alerts/test"
    ) {
      const result = await queueCommand(sql, request, url);
      return sendJson(response, result.status, result.payload);
    }
    if (url.pathname === "/api/reports/availability" && request.method === "GET") {
      const days = periodFrom(url);
      if (!days) return sendJson(response, 400, { ok: false, error: "Periodo invalido" });
      return sendJson(response, 200, state.payload.reports?.[String(days)] || {});
    }
    if (url.pathname === "/api/calls/history" && request.method === "GET") {
      const days = periodFrom(url);
      if (!days) return sendJson(response, 400, { ok: false, error: "Periodo invalido" });
      const calls = callsForPeriod(state, days);
      return sendJson(response, 200, {
        ok: true,
        days,
        summary: summarizeCalls(calls),
        calls: calls.slice(0, 300),
        generated_at: Date.now() / 1000,
      });
    }
    const extensionMatch = url.pathname.match(/^\/api\/calls\/extensions\/(\d{1,10})$/);
    if (extensionMatch && request.method === "GET") {
      const days = periodFrom(url);
      if (!days) return sendJson(response, 400, { ok: false, error: "Periodo invalido" });
      return sendJson(response, 200, extensionHistory(state, extensionMatch[1], days));
    }
    if (url.pathname.startsWith("/api/directory/export/")) {
      return sendJson(response, 503, { ok: false, error: "Exportacao disponivel apenas no painel interno" });
    }
    return sendJson(response, 404, { ok: false, error: "Recurso nao encontrado" });
  } catch (error) {
    console.error(error);
    return sendJson(response, 500, { ok: false, error: "Falha interna no painel em nuvem" });
  }
}
