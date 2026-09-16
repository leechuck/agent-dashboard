import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: '../agentdash/static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8790', changeOrigin: true },
      '/login': { target: 'http://127.0.0.1:8790', changeOrigin: true },
      '/history': { target: 'http://127.0.0.1:8790', changeOrigin: true },
    },
  },
})
