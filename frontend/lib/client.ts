// Small browser helper for mutations: JSON in, readable error out.
export async function send<T = unknown>(path: string, method: "POST" | "PATCH" | "PUT" | "DELETE", body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method, headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = (j as { detail?: unknown }).detail;
    throw new Error(typeof d === "string" ? d : "Something went wrong. Try again.");
  }
  return j as T;
}

export const inDays = (n: number) => new Date(Date.now() + n * 86400000).toISOString().slice(0, 10);
