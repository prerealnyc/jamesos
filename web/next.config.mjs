/** @type {import('next').NextConfig} */

// Proxy backend calls to the FastAPI origin so the browser stays
// same-origin (no CORS) in dev and prod. Override with BACKEND_ORIGIN.
//
// The env lookup MUST happen inside the function — Next.js evaluates
// the rewrites at runtime (after Railway has injected env vars), but
// any top-level `const X = process.env.Y` is evaluated when the module
// first loads, which on Railway can be before the env is fully set
// in the Node process. Moving the lookup inside `rewrites()` makes
// it deterministic.

const nextConfig = {
  async rewrites() {
    const BACKEND = process.env.BACKEND_ORIGIN || "http://localhost:8001";
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
      // Post-image generation lives at /images/* — separate from the
      // /images page route, since the Next page resolves first.
      { source: "/images/:path*", destination: `${BACKEND}/images/:path*` },
      // Hero context — the page is /hero, the endpoints are /hero/context
      // and /hero/context/refresh. Catch-all here so the rewrite picks
      // up sub-paths but not the bare /hero page (which is a Next route).
      { source: "/hero/:path*", destination: `${BACKEND}/hero/:path*` },
      // Long Form Cutter — sub-paths only (sources, upload, render,
      // candidates) so the bare /long-form route stays the Next page.
      { source: "/long-form/:path*", destination: `${BACKEND}/long-form/:path*` },
      { source: "/api/:path*", destination: `${BACKEND}/api/:path*` },
      // Auth endpoints (login/signup/logout/me/password). Without these
      // the browser hits the Next.js 3000 server (no such route) and
      // gets a 404 instead of the FastAPI auth handlers on 8001.
      { source: "/auth/:path*", destination: `${BACKEND}/auth/:path*` },
      // Agent (Ask the memory → Do mode) — list runs / get run / kick.
      { source: "/agent/:path*", destination: `${BACKEND}/agent/:path*` },
      // Analytics — handles / summary / posts / timeline / cohort /
      // accounts / refresh.
      { source: "/analytics/:path*", destination: `${BACKEND}/analytics/:path*` },
      // Integrations — Meta Graph + PostProxy + the unified
      // /integrations/connections view the Analytics page reads.
      { source: "/integrations/:path*", destination: `${BACKEND}/integrations/:path*` },
      // Social Research roster (weekly influencer scrape).
      { source: "/research/:path*", destination: `${BACKEND}/research/:path*` },
      // Voice Studio — Drive folder/links → transcribe → voice_corpus.
      // /voice/* are API paths; the /voice-studio page is a separate Next route.
      { source: "/voice/:path*", destination: `${BACKEND}/voice/:path*` },
      // Style Templates — Design Inspector library. /templates(/*) are API
      // paths; the /style-templates page is a separate Next route, so no clash.
      { source: "/templates", destination: `${BACKEND}/templates` },
      { source: "/templates/:path*", destination: `${BACKEND}/templates/:path*` },
      // Composition build-queue (which reference layouts render today vs queued).
      { source: "/compositions", destination: `${BACKEND}/compositions` },
    ];
  },
};

export default nextConfig;
