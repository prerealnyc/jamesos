/** @type {import('next').NextConfig} */

// Proxy backend calls to the FastAPI origin so the browser stays
// same-origin (no CORS) in dev and prod. Override with BACKEND_ORIGIN.
const BACKEND = process.env.BACKEND_ORIGIN || "http://localhost:8001";

const nextConfig = {
  async rewrites() {
    return [
      { source: "/ask", destination: `${BACKEND}/ask` },
      { source: "/health", destination: `${BACKEND}/health` },
      { source: "/events", destination: `${BACKEND}/events` },
      { source: "/events/:path*", destination: `${BACKEND}/events/:path*` },
      { source: "/plug-ins", destination: `${BACKEND}/plug-ins` },
      { source: "/plug-ins/:path*", destination: `${BACKEND}/plug-ins/:path*` },
      { source: "/ingest/:path*", destination: `${BACKEND}/ingest/:path*` },
      { source: "/api/:path*", destination: `${BACKEND}/api/:path*` },
    ];
  },
};

export default nextConfig;
