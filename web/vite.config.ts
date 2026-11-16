import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the React app runs on :5173 and forwards /api to the Python server on :8000.
// In production FastAPI serves the built files itself, so there is a single origin and no CORS.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
