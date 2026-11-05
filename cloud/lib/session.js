// Sessao assinada em cookie. Usamos cookie (e nao HTTP Basic) porque a CDN da
// Vercel nao guarda em cache pedidos com cabecalho Authorization; com cookie, o
// middleware valida o acesso antes do cache e todos os usuarios logados
// compartilham a mesma leitura do Redis.

export const SESSION_COOKIE = "pulso_session";
export const SESSION_SECONDS = 12 * 60 * 60;

const encoder = new TextEncoder();

async function hmacHex(secret, value) {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(value));
  return [...new Uint8Array(signature)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function sameText(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function sessionSecret() {
  const secret = process.env.SESSION_SECRET || "";
  if (secret.length < 32) throw new Error("SESSION_SECRET ausente ou curto");
  return secret;
}

export async function createSessionValue(now = Date.now()) {
  const expires = Math.floor(now / 1000) + SESSION_SECONDS;
  return `${expires}.${await hmacHex(sessionSecret(), String(expires))}`;
}

export async function isValidSession(cookieHeader, now = Date.now()) {
  const match = (cookieHeader || "")
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${SESSION_COOKIE}=`));
  if (!match) return false;
  const [expires, signature] = match.slice(SESSION_COOKIE.length + 1).split(".");
  if (!/^\d+$/.test(expires || "") || Number(expires) * 1000 < now) return false;
  return sameText(signature, await hmacHex(sessionSecret(), expires));
}

export { sameText };
