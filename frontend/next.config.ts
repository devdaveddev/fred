import type { NextConfig } from "next";

// /api/* is proxied by app/api/[...path]/route.ts at request time (Render, dev), or by Caddy before Next (Compose).
const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
};

export default config;
