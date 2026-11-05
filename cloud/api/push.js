import { STATUS_KEY, redis } from "../lib/redis.js";
import { sameText } from "../lib/session.js";

// Recebe o resumo enviado pelo monitor local. Cada envio aceito custa um unico
// comando no Redis (SET). O monitor so envia quando algo muda, no maximo a cada
// 30s, mais um sinal de vida a cada 10 minutos.
const MAX_BODY_BYTES = 256 * 1024;
const MAX_EXTENSIONS = 2000;

const reply = (status, body) =>
  Response.json(body, { status, headers: { "Cache-Control": "no-store" } });

const text = (value, max = 120) => String(value ?? "").slice(0, max);
const number = (value) => (Number.isFinite(Number(value)) ? Number(value) : null);

function sanitize(payload) {
  if (!payload || !Array.isArray(payload.extensions)) return null;
  if (payload.extensions.length > MAX_EXTENSIONS) return null;
  return {
    generated_at: number(payload.generated_at),
    received_at: Date.now() / 1000,
    ami_status: text(payload.ami_status, 40),
    last_reconcile_minute: number(payload.last_reconcile_minute),
    debounce_seconds: number(payload.debounce_seconds),
    extensions: payload.extensions.map((item) => ({
      extension: text(item.extension, 20),
      nome: text(item.nome),
      setor: text(item.setor),
      online: Boolean(item.online),
      since: number(item.since),
      pending_status: item.pending_status === "online" || item.pending_status === "offline"
        ? item.pending_status
        : null,
      pending_since: number(item.pending_since),
    })),
  };
}

export default {
  async fetch(request) {
    if (request.method !== "POST") return reply(405, { ok: false });

    const expected = process.env.PUSH_TOKEN || "";
    const provided = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
    if (expected.length < 32 || !sameText(provided, expected)) {
      return reply(401, { ok: false, error: "unauthorized" });
    }

    const raw = await request.text();
    if (raw.length > MAX_BODY_BYTES) return reply(413, { ok: false, error: "too_large" });

    let snapshot;
    try {
      snapshot = sanitize(JSON.parse(raw));
    } catch {
      snapshot = null;
    }
    if (!snapshot) return reply(400, { ok: false, error: "invalid_payload" });

    try {
      await redis(["SET", STATUS_KEY, JSON.stringify(snapshot)]);
    } catch (error) {
      console.error("Falha ao gravar o status no Redis", error);
      return reply(503, { ok: false, error: "storage_unavailable" });
    }
    return reply(200, { ok: true, extensions: snapshot.extensions.length });
  },
};
