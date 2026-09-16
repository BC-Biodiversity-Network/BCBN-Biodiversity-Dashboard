import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Standard Vite setup for a React app. Nothing custom yet.
// Files in public/ are served as-is at the root of the site, so
// public/data/bc_hex_r5.csv.gz is reachable at /data/bc_hex_r5.csv.gz.
export default defineConfig({
  plugins: [react()],
})
