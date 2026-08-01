/**
 * config.js - Application Configuration
 * =======================================
 *
 * Centralized configuration values used across the app.
 *
 * KEY CONCEPT: Vite Environment Variables
 * ----------------------------------------
 * Vite exposes environment variables through `import.meta.env`.
 * Only variables prefixed with `VITE_` are available in the browser
 * (this prevents accidentally leaking server-side secrets).
 *
 * How it works in different environments:
 *
 * LOCAL DEVELOPMENT:
 *   - No VITE_API_URL is set, so API_BASE defaults to "" (empty string)
 *   - This means fetch("/players/") stays as a relative URL
 *   - The Vite proxy (configured in vite.config.js) intercepts it and
 *     forwards to http://localhost:8001
 *
 * PRODUCTION (Vercel):
 *   - Set VITE_API_URL in Vercel's Environment Variables settings
 *   - Example: VITE_API_URL=https://your-app.onrender.com
 *   - This means fetch("/players/") becomes
 *     fetch("https://your-app.onrender.com/players/")
 *   - No proxy needed — the browser calls the Render backend directly
 *
 * To set in Vercel: Project Settings > Environment Variables > Add:
 *   Key: VITE_API_URL
 *   Value: https://your-backend-name.onrender.com
 */

const DEFAULT_PRODUCTION_API_BASE = 'https://mlbfantasystats.onrender.com'

// API base URL — prepended to all backend API calls.
// Empty string in local development (uses Vite proxy), full URL in deployed builds.
export const API_BASE = import.meta.env.VITE_API_URL || (import.meta.env.PROD ? DEFAULT_PRODUCTION_API_BASE : '')

// Pitcher Ks remains dark unless explicitly enabled for a Vercel environment.
// This flag controls both navigation and direct route resolution.
export const PITCHER_KS_ENABLED = String(import.meta.env.VITE_PITCHER_KS_ENABLED).toLowerCase() === 'true'
