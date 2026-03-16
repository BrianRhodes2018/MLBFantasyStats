/**
 * vite.config.js - Vite Development Server Configuration
 * =======================================================
 *
 * Vite is a modern build tool and dev server for frontend projects.
 * This config file customizes how Vite serves our React app during development.
 *
 * Key concept here: the PROXY configuration.
 *
 * Problem: Our React app runs on http://localhost:5173 (Vite's default port),
 * but our FastAPI backend runs on http://localhost:8000. When the React app
 * makes a fetch("/players/") call, the browser sends it to localhost:5173
 * (the current origin), which doesn't have that endpoint.
 *
 * Solution: The proxy tells Vite's dev server to intercept any request
 * that starts with "/players" and forward it to http://localhost:8000.
 * The React code can use simple relative URLs like fetch("/players/")
 * without knowing about the backend's actual address.
 *
 * This is a DEVELOPMENT-ONLY feature. In production, you'd configure
 * a reverse proxy (like Nginx) or deploy both on the same domain.
 */

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // Plugins extend Vite's functionality. @vitejs/plugin-react adds
  // React-specific features like Fast Refresh (hot module replacement)
  // and JSX transformation.
  plugins: [react()],

  server: {
    // The proxy configuration: intercept requests and forward them
    // to the FastAPI backend running on port 8000.
    proxy: {
      // Any request path starting with "/players" will be forwarded.
      // This covers: /players/, /players/stats, /players/computed, /players/team-stats,
      // /players/rolling-stats, /players/search, /players/filterable-stats
      '/players': {
        target: 'http://localhost:8000',  // Where to forward the request
        changeOrigin: true,               // Changes the Origin header to match the target
      },
      // Forward pitcher routes too — same pattern as players.
      // Covers: /pitchers/, /pitchers/stats, /pitchers/computed, /pitchers/rolling-stats
      '/pitchers': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // Forward fantasy league routes — ESPN fantasy league integration.
      // Covers: /fantasy/leagues, /fantasy/points/batters/{id}, /fantasy/points/pitchers/{id}
      '/fantasy': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // Forward player detail routes — ESPN news proxy + MLB transactions.
      // Covers: /player-detail/news, /player-detail/transactions/{mlb_id}
      '/player-detail': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
