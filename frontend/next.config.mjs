const API = process.env.API_URL || "http://127.0.0.1:8010";
const DEMO = process.env.NEXT_PUBLIC_DEMO === "1";
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  distDir: process.env.NEXT_DIST_DIR || ".next",
  ...(DEMO
    ? {
        // Static GitHub Pages build: no server. The backend runs in the browser (see lib/demo.ts, public/demo/*).
        output: "export",
        trailingSlash: true,
        basePath: BASE_PATH,
        images: { unoptimized: true },
      }
    : {
        // Normal build: same-origin proxy to the FastAPI service (no CORS in the browser).
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
        },
      }),
};
export default nextConfig;
