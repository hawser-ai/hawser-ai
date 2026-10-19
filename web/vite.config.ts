/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// The app is served by hawserd at /app (and from `vite dev` at /app/ with the API proxied).
export default defineConfig({
  base: '/app/',
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: process.env.HAWSER_SERVER_URL ?? 'http://127.0.0.1:8080', changeOrigin: true },
      '/healthz': { target: process.env.HAWSER_SERVER_URL ?? 'http://127.0.0.1:8080', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
})
