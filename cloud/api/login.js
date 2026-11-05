import {
  SESSION_COOKIE,
  SESSION_SECONDS,
  createSessionValue,
  sameText,
} from "../lib/session.js";

const redirect = (location, extraHeaders = {}) =>
  new Response(null, {
    status: 303,
    headers: { Location: location, "Cache-Control": "no-store", ...extraHeaders },
  });

export default {
  async fetch(request) {
    if (request.method !== "POST") return redirect("/login");

    const form = await request.formData().catch(() => null);
    const username = String(form?.get("username") || "");
    const password = String(form?.get("password") || "");
    const expectedUser = process.env.PANEL_USERNAME || "";
    const expectedPassword = process.env.PANEL_PASSWORD || "";

    const ok =
      expectedUser.length > 0 &&
      expectedPassword.length >= 12 &&
      sameText(username, expectedUser) &&
      sameText(password, expectedPassword);

    if (!ok) {
      // Atraso fixo para encarecer tentativas repetidas de senha.
      await new Promise((resolve) => setTimeout(resolve, 800));
      return redirect("/login?erro=1");
    }

    const value = await createSessionValue();
    return redirect("/", {
      "Set-Cookie": `${SESSION_COOKIE}=${value}; Path=/; Max-Age=${SESSION_SECONDS}; HttpOnly; Secure; SameSite=Lax`,
    });
  },
};
