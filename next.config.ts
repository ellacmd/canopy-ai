import type { NextConfig } from "next";

const backend = new URL(process.env.CANOPY_BACKEND_URL ?? "http://127.0.0.1:8000");
if (
  !["http:", "https:"].includes(backend.protocol) ||
  backend.username || backend.password || backend.pathname !== "/" ||
  backend.search || backend.hash
) {
  throw new Error("CANOPY_BACKEND_URL must be an HTTP(S) origin");
}

const config: NextConfig = {
  poweredByHeader: false,
  // Match the backend's bounded provider wait before a workflow stage is emitted.
  experimental: { proxyTimeout: 90_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend.origin}/api/:path*` }];
  },
};

export default config;
