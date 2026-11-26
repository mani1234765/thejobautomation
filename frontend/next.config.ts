import type { NextConfig } from "next";

// Where /api/* is proxied. Either a full origin, or host + port as exposed by the hosting platform
// (e.g. a Render private service). Evaluated at build time, so set these before `next build`.
const API_ORIGIN =
  process.env.JOBPILOT_API_ORIGIN ??
  (process.env.JOBPILOT_API_HOST
    ? `http://${process.env.JOBPILOT_API_HOST}:${process.env.JOBPILOT_API_PORT ?? "10000"}`
    : "http://127.0.0.1:8000");

const nextConfig: NextConfig = {
  // JobPilot's product screens are client-rendered against the API (React Query), so server
  // component caching is not used.
  cacheComponents: false,
  poweredByHeader: false,
  devIndicators: { position: "bottom-right" },
  // The browser talks to the API on the same origin: cookies stay httpOnly and there is no CORS surface.
  rewrites: async () => [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }],
  headers: async () => [
    {
      source: "/:path*",
      headers: [
        { key: "X-Frame-Options", value: "DENY" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      ],
    },
  ],
  turbopack: {
    rules: {
      "*.css": { loaders: ["@tailwindcss/turbopack"], as: "*.css" },
    },
  },
};

export default nextConfig;
