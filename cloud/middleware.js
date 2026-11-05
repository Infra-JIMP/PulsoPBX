import { next } from "@vercel/functions";

import { isValidSession } from "./lib/session.js";

// Somente o painel e o status exigem sessao. Login, envio do monitor local
// (que tem token proprio) e arquivos estaticos ficam fora do matcher.
export const config = {
  matcher: ["/", "/index", "/index.html", "/api/status"],
};

export default async function middleware(request) {
  let valid = false;
  try {
    valid = await isValidSession(request.headers.get("cookie"));
  } catch {
    valid = false;
  }
  if (valid) return next();

  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/")) {
    return new Response(JSON.stringify({ ok: false, error: "login_required" }), {
      status: 401,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  }
  return new Response(null, {
    status: 302,
    headers: { Location: "/login", "Cache-Control": "no-store" },
  });
}
