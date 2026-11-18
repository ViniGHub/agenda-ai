import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// In development the React app runs on :5173 and forwards /api to the Python server on :8000.
// In production FastAPI serves the built files itself, so there is a single origin and no CORS.
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // Installable app (PWA): manifest + a service worker that keeps the app's own files for instant,
    // offline-safe start. The API is never cached: answers and the agenda are personal and must be fresh.
    VitePWA({
      registerType: 'prompt', // a new version waits until the person taps "atualizar"
      injectRegister: false, // registered in main.tsx, next to the "nova versão" notice
      includeAssets: ['favicon.ico', 'apple-touch-icon-180x180.png', 'icone.svg'],
      manifest: {
        id: '/',
        name: 'Agenda IA',
        short_name: 'Agenda IA',
        description: 'Pergunte sobre a sua agenda, anote compromissos por texto ou voz.',
        lang: 'pt-BR',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        orientation: 'portrait',
        background_color: '#e7dcc8', // the desk behind the notebook
        theme_color: '#e7dcc8',
        icons: [
          { src: 'pwa-64x64.png', sizes: '64x64', type: 'image/png' },
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          { src: 'maskable-icon-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // The app shell. Only the Latin subsets of the fonts: Portuguese needs nothing else.
        globPatterns: ['**/*.{js,css,html,png,svg,ico}', '**/*-latin-*.woff2'],
        globIgnores: ['**/*-latin-ext-*'],
        navigateFallback: '/index.html', // screens live in the #hash, so every page is index.html
        navigateFallbackDenylist: [/^\/api\//], // includes the Google login redirects
        cleanupOutdatedCaches: true,
      },
    }),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
