import { NextResponse, type NextRequest } from "next/server";

const PUBLIC = ["/login", "/signup"];
const OPEN_PREFIX = ["/invite/", "/welcome"]; // reachable signed in or not

// Cheap gate: no session cookie means straight to sign-in. The API still validates every session.
export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const hasSession = req.cookies.has("fred_session");
  if (OPEN_PREFIX.some((p) => pathname.startsWith(p))) return NextResponse.next();
  // Visitors without a session see the landing page at the root.
  if (!hasSession && pathname === "/") return NextResponse.rewrite(new URL("/welcome", req.url));
  if (!hasSession && !PUBLIC.includes(pathname)) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
    return NextResponse.redirect(url);
  }
  if (hasSession && PUBLIC.includes(pathname)) {
    return NextResponse.redirect(new URL("/", req.url));
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!api|_next|icon.svg|favicon.ico).*)"] };
