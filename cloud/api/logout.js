import { SESSION_COOKIE } from "../lib/session.js";

export default {
  async fetch() {
    return new Response(null, {
      status: 303,
      headers: {
        Location: "/login",
        "Cache-Control": "no-store",
        "Set-Cookie": `${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`,
      },
    });
  },
};
