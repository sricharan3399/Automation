/// <reference types="vitest" />
import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dashboard is served by the FastAPI backend from `dist/` in production.
// During development Vite proxies API and WebSocket traffic to the backend so
// the browser only ever talks to one origin.
export default defineConfig({
  plugins: [react()],
  // Mirrors the `@/*` paths mapping in tsconfig.json so builds, tests and the
  // type checker all resolve imports the same way.
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
