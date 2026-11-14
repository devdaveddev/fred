"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-fetches the page every few seconds (used while a new workspace's demo data is being prepared). */
export function AutoRefresh({ every = 5000 }: { every?: number }) {
  const router = useRouter();
  useEffect(() => {
    const t = setInterval(() => router.refresh(), every);
    return () => clearInterval(t);
  }, [router, every]);
  return null;
}
