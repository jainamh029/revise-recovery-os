const API = process.env.API_URL || "http://127.0.0.1:8010";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Same-origin proxy to the FastAPI service: no CORS in the browser, one deployment surface.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};
export default nextConfig;
