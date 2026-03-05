/**
 * main.jsx - React Application Entry Point
 * ==========================================
 *
 * This is the very first JavaScript file that runs when the app loads.
 * It does one thing: mount the React component tree into the HTML page.
 *
 * Key concepts:
 * - createRoot(): Creates a React "root" — the connection point between
 *   React's virtual DOM and the actual browser DOM.
 * - document.getElementById('root'): Finds the <div id="root"> in index.html.
 *   This is the container where React will render everything.
 * - StrictMode: A development-only wrapper that enables extra checks:
 *   - Warns about deprecated APIs
 *   - Detects unexpected side effects by running some functions twice
 *   - Helps you find bugs early (no performance impact in production)
 * - The import of './index.css' loads Vite's default base styles.
 *   We import './App.css' for our custom styles.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'  // Vite's default base styles (resets, etc.)
import './App.css'    // Our custom application styles
import App from './App.jsx'

// createRoot().render() replaces the old ReactDOM.render() from React 17.
// It enables React 18's concurrent features (though we don't use them here).
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
