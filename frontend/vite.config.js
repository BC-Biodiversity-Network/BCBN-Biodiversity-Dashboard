import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Where the species API runs during development. `npm run dev` passes every
// request for /api on to it, so the page can call /api/... as if the API were
// part of the same site. Start the API first, from backend/:
//   uvicorn api.main:app --reload --port 8000
// Set BCBN_API_DEV_URL to use another address. This only affects `npm run
// dev`; where the built site finds the API is set in src/api.js.
const API_DEV_URL = process.env.BCBN_API_DEV_URL || 'http://127.0.0.1:8000'

// Files in public/ are served as-is at the root of the site, so
// public/data/bc_hex_r5.csv.gz is reachable at /data/bc_hex_r5.csv.gz.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': API_DEV_URL,
    },
  },
  // The tests (`npm test`, run by Vitest) use this same setup, in a pretend
  // browser page (jsdom). Only files ending in .test.js or .test.jsx are
  // tests; nothing the site imports, so none of it goes into the build.
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{js,jsx}'],
  },
})
