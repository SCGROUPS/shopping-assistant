import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The deployed app serves the API from its own origin under /api/v1, so the
// dev server proxies the same path rather than pointing the client at a
// cross-origin URL. Keeping the two topologies identical means anything that
// depends on same-origin behaviour — cookies, relative fetches, E2E specs —
// behaves the same locally as in production.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api/v1': {
        target: process.env.VITE_DEV_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
