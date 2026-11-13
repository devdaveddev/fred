// /api/* → the API service, resolved at request time (not baked in at build time).
// With Docker Compose, Caddy routes /api/* to the API before requests ever reach Next, so this only runs where
// Next is the public entrypoint (Render, `npm run dev`). Cookies, uploads, redirects and status codes pass
// through untouched.
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const HOP = new Set(["connection", "keep-alive", "transfer-encoding", "upgrade", "host", "content-length",
  "accept-encoding", "proxy-authorization", "te", "trailer"]);

function upstream(): string {
  const raw = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  return (raw.startsWith("http") ? raw : `http://${raw}`).replace(/\/$/, "");
}

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const url = `${upstream()}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  req.headers.forEach((v, k) => { if (!HOP.has(k.toLowerCase())) headers.set(k, v); });
  headers.set("x-forwarded-proto", req.headers.get("x-forwarded-proto") ?? req.nextUrl.protocol.replace(":", ""));
  const hasBody = !["GET", "HEAD"].includes(req.method);
  let res: Response;
  try {
    res = await fetch(url, {
      method: req.method, headers, redirect: "manual", cache: "no-store",
      body: hasBody ? req.body : undefined,
      // @ts-expect-error Node's fetch needs duplex for streamed request bodies (uploads)
      duplex: hasBody ? "half" : undefined,
    });
  } catch {
    return Response.json({ detail: "The service is starting up or unreachable. Try again in a moment." }, { status: 502 });
  }
  const out = new Headers();
  res.headers.forEach((v, k) => {
    if (!HOP.has(k.toLowerCase()) && k.toLowerCase() !== "content-encoding" && k.toLowerCase() !== "set-cookie") out.set(k, v);
  });
  for (const c of res.headers.getSetCookie()) out.append("set-cookie", c);
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
