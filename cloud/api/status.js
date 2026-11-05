import { STATUS_KEY, redis } from "../lib/redis.js";

// O acesso ja foi validado pelo middleware, que roda antes do cache. A CDN
// guarda esta resposta por 15s: com 1 ou 50 pessoas olhando, o Redis recebe no
// maximo uma leitura a cada 15s por regiao.
const CDN_CACHE = "public, s-maxage=15, stale-while-revalidate=15";
const STALE_SECONDS = 15 * 60;

const NOT_CONFIGURED_ALERT = { status: "not_configured", sent_count: 0, total_recipients: 0 };

// Converte o resumo minimo enviado pelo monitor local no mesmo formato do
// /api/status do painel local, para a nuvem usar exatamente a mesma interface.
export function toDashboard(snapshot, now = Date.now() / 1000) {
  const items = snapshot?.extensions || [];
  const debounce = Number(snapshot?.debounce_seconds) || 30;
  const age = snapshot?.generated_at ? now - snapshot.generated_at : null;
  const amiStatus = snapshot ? snapshot.ami_status || "not_configured" : "not_configured";

  let freshness = "fresh";
  if (!snapshot) freshness = "not_available";
  else if (age > STALE_SECONDS || amiStatus === "disconnected") freshness = "stale";
  else if (amiStatus === "connecting") freshness = "syncing";

  const extensions = items.map((item) => ({
    extension: item.extension,
    nome: item.nome || "",
    setor: item.setor || "",
    online: Boolean(item.online),
    since: item.since,
    pending_status: item.pending_status || null,
    pending_since: item.pending_status ? item.pending_since : null,
    confirmation_remaining_seconds:
      item.pending_status && item.pending_since
        ? Math.max(0, debounce - (now - item.pending_since))
        : 0,
    responsible_email_configured: false,
    alerts_muted: false,
    alert: NOT_CONFIGURED_ALERT,
    incident: null,
  }));
  const online = extensions.filter((item) => item.online).length;

  return {
    // Com o monitor local calado ha muito tempo, o painel deve mostrar isso.
    ami_status: age != null && age > STALE_SECONDS ? "disconnected" : amiStatus,
    last_reconcile_at: snapshot?.last_reconcile_minute ? snapshot.last_reconcile_minute * 60 : null,
    last_reconcile_age_seconds: null,
    data_freshness: freshness,
    notifications: { configured: false, alert_recipient_count: 0, jobs: {}, calendar: { configured: false } },
    management: { responsibles_available: false, directory_available: false },
    generated_at: snapshot?.generated_at ?? null,
    total: extensions.length,
    online,
    offline: extensions.length - online,
    confirming: extensions.filter((item) => item.pending_status).length,
    extensions,
    recent_events: [],
    recent_alerts: [],
    incidents: [],
  };
}

export default {
  async fetch() {
    let snapshot = null;
    try {
      const raw = await redis(["GET", STATUS_KEY]);
      snapshot = raw ? JSON.parse(raw) : null;
    } catch (error) {
      console.error("Falha ao ler o status no Redis", error);
      return Response.json(
        { ok: false, error: "storage_unavailable" },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }
    return Response.json(toDashboard(snapshot), {
      status: 200,
      headers: {
        // O navegador sempre revalida; quem guarda e a CDN da Vercel.
        "Cache-Control": "max-age=0, must-revalidate",
        "Vercel-CDN-Cache-Control": CDN_CACHE,
      },
    });
  },
};
