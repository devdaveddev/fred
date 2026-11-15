// Server-only API client: forwards the visitor's session cookie to the API on the internal network.
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { ApiError, call, makeApi } from "./api";

async function serverCall<T>(path: string, init?: RequestInit): Promise<T> {
  const jar = await cookies();
  try {
    return await call<T>(path, { ...init, headers: { ...(init?.headers ?? {}), cookie: jar.toString() } });
  } catch (e) {
    // The session is gone (expired, signed out elsewhere, or the database changed): the middleware drops the
    // stale cookie on the way to /login, otherwise /login would bounce back here forever.
    if (e instanceof ApiError && e.status === 401) redirect("/login?session=expired");
    throw e;
  }
}

export const sapi = makeApi(serverCall);
