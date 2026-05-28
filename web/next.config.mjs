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
      { source: "/generate", destination: `${BACKEND}/generate` },
      { source: "/generate-multi", destination: `${BACKEND}/generate-multi` },
      { source: "/generate-script", destination: `${BACKEND}/generate-script` },
      { source: "/research", destination: `${BACKEND}/research` },
      { source: "/video/:path*", destination: `${BACKEND}/video/:path*` },
      // Autopilot, Trends, Media — without these, Autopilot / Social
      // Companion / Trend Radar / Reference Library all silently 404 in
      // the browser even though the backend serves them on :8001.
      { source: "/autopilot", destination: `${BACKEND}/autopilot` },
      { source: "/autopilot/:path*", destination: `${BACKEND}/autopilot/:path*` },
      { source: "/trends", destination: `${BACKEND}/trends` },
      { source: "/trends/:path*", destination: `${BACKEND}/trends/:path*` },
      { source: "/media", destination: `${BACKEND}/media` },
      { source: "/media/:path*", destination: `${BACKEND}/media/:path*` },
      // Local-disk media served directly by FastAPI (pre-Supabase uploads).
      { source: "/media-files/:path*", destination: `${BACKEND}/media-files/:path*` },
      { source: "/api/:path*", destination: `${BACKEND}/api/:path*` },
    ];
  },
};

export default nextConfig;
