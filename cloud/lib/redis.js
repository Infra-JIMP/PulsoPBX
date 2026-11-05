// Cliente minimo da API REST do Upstash Redis: um comando por chamada, sem SDK.
// Aceita os nomes de variavel criados pela integracao da Vercel (KV_*) e os do
// proprio Upstash (UPSTASH_REDIS_REST_*).

export const STATUS_KEY = "pulsopbx:status";

function credentials() {
  const url = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) throw new Error("Credenciais do Redis ausentes");
  return { url, token };
}

export async function redis(command) {
  const { url, token } = credentials();
  const response = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(command),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.error) {
    throw new Error(`Redis recusou o comando: ${payload.error || response.status}`);
  }
  return payload.result;
}
