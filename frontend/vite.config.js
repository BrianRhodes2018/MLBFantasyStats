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

// ---------------------------------------------------------------------------
// PWA (Progressive Web App) Plugin
// ---------------------------------------------------------------------------
// This plugin turns our regular website into an installable app.
// At build time, it auto-generates two critical files:
//   1. manifest.webmanifest — tells the browser "this is an app" (name, icon, theme)
//   2. sw.js (Service Worker) — caches files so the app loads fast & works offline
//
// What is a Service Worker?
// It's a JavaScript file that runs in the background (separate from your app).
// It intercepts network requests and can serve cached responses. Think of it
// as a smart proxy between your app and the internet:
//   - First visit: Downloads and caches all your app files
//   - Repeat visits: Serves cached files instantly (no network wait)
//   - Offline: Still works because everything is cached locally
// ---------------------------------------------------------------------------
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  // Plugins extend Vite's functionality. @vitejs/plugin-react adds
  // React-specific features like Fast Refresh (hot module replacement)
  // and JSX transformation.
  plugins: [
    react(),

    // -----------------------------------------------------------------------
    // VitePWA Plugin Configuration
    // -----------------------------------------------------------------------
    // This is what makes our website installable on iPhones/Android.
    // When someone visits in Safari and taps "Add to Home Screen", the browser
    // reads the manifest to know what icon to show and how to display the app.
    // -----------------------------------------------------------------------
    VitePWA({
      // "autoUpdate" = the service worker updates silently in the background.
      // When we deploy a new version, users automatically get it on their
      // next visit — no "please refresh" prompt needed.
      registerType: 'autoUpdate',

      // -----------------------------------------------------------------------
      // Manifest — The App's Identity Card
      // -----------------------------------------------------------------------
      // This object becomes the manifest.webmanifest file in the build output.
      // The browser reads this file to understand:
      //   - What to call the app on the home screen
      //   - What icon to use
      //   - What colors to use for the splash screen
      //   - Whether to show browser bars or go full-screen
      // -----------------------------------------------------------------------
      manifest: {
        name: 'MLB Fantasy Stats',           // Full name (shown in app switcher)
        short_name: 'MLB Stats',             // Short name (shown under home screen icon)
        description: 'MLB player stats with fantasy league scoring',
        theme_color: '#0a1929',              // Matches our app's dark blue background
        background_color: '#0a1929',         // Splash screen background while app loads

        // "standalone" is the KEY setting — it removes the browser's address bar
        // and navigation buttons. The app fills the whole screen, just like a
        // native app from the App Store. Other options:
        //   - "browser" = normal browser window (defeats the purpose)
        //   - "minimal-ui" = tiny nav bar (compromise)
        //   - "fullscreen" = no status bar either (too aggressive for most apps)
        display: 'standalone',

        start_url: '/',                      // URL that opens when you tap the icon

        // Icons — the browser picks the best size for each context:
        //   - 192x192: Home screen icon, app switcher
        //   - 512x512: Splash screen, store listing
        //   - "maskable": Tells the OS it can crop this icon into any shape
        //     (circle on Android, rounded square on iOS, etc.)
        icons: [
          {
            src: 'pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: 'pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
          },
          {
            src: 'pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',             // Safe to crop into any shape
          },
        ],
      },

      // -----------------------------------------------------------------------
      // Workbox — Service Worker Caching Strategy
      // -----------------------------------------------------------------------
      // Workbox is a Google library that vite-plugin-pwa uses to generate the
      // service worker. It handles all the caching logic so we don't have to
      // write raw service worker code (which is complex and error-prone).
      //
      // Two types of caching:
      //   1. Precaching (globPatterns) — Cache app files at install time
      //   2. Runtime caching — Cache API responses as they're fetched
      // -----------------------------------------------------------------------
      workbox: {
        // Precache these file types when the service worker installs.
        // These are your app's "shell" — the HTML, CSS, JS, and images
        // that make up the UI. They're cached on first visit and served
        // instantly on every subsequent visit.
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],

        // Runtime caching rules for API requests.
        // Unlike precaching (which downloads files upfront), runtime caching
        // saves API responses as they happen. Each rule has:
        //   - urlPattern: Which requests to cache (regex match)
        //   - handler: The caching strategy to use
        //   - options: Cache name, size limits, expiration
        runtimeCaching: [
          {
            // Match any request to our backend API endpoints
            urlPattern: /^https:\/\/.*\/(players|pitchers|fantasy)/,

            // "NetworkFirst" strategy:
            //   1. Try to fetch from the network (get fresh data)
            //   2. If network succeeds → return fresh data AND save it to cache
            //   3. If network fails (offline) → return the cached version
            //
            // This is ideal for our API data because:
            //   - Stats update daily, so we want fresh data when possible
            //   - But we still want the app to work offline with stale data
            //
            // Other strategies (not used here but good to know):
            //   - "CacheFirst": Check cache first, only hit network if not cached
            //     Good for: images, fonts — things that rarely change
            //   - "StaleWhileRevalidate": Serve from cache immediately, then
            //     update cache in background. Good for: frequently accessed data
            //     where slightly stale is OK
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-cache',        // Name shown in DevTools > Application > Cache
              expiration: {
                maxEntries: 50,              // Keep at most 50 API responses cached
                maxAgeSeconds: 3600,         // Expire after 1 hour (3600 seconds)
              },
            },
          },
        ],
      },
    }),
  ],

  server: {
    // The proxy configuration: intercept requests and forward them
    // to the FastAPI backend running on port 8001.
    proxy: {
      // Any request path starting with "/players" will be forwarded.
      // This covers: /players/, /players/stats, /players/computed, /players/team-stats,
      // /players/rolling-stats, /players/search, /players/filterable-stats
      '/players': {
        target: 'http://localhost:8001',  // Where to forward the request
        changeOrigin: true,               // Changes the Origin header to match the target
      },
      // Forward pitcher routes too — same pattern as players.
      // Covers: /pitchers/, /pitchers/stats, /pitchers/computed, /pitchers/rolling-stats
      '/pitchers': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      // Forward fantasy league routes — ESPN fantasy league integration.
      // Covers: /fantasy/leagues, /fantasy/points/batters/{id}, /fantasy/points/pitchers/{id}
      '/fantasy': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      // Forward player detail routes — ESPN news proxy + MLB transactions.
      // Covers: /player-detail/news, /player-detail/transactions/{mlb_id}
      '/player-detail': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      // Forward season metadata route — returns available season snapshots.
      '/seasons': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
})
