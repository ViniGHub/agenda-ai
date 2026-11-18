import { defineConfig, minimal2023Preset } from '@vite-pwa/assets-generator/config'

// Generates the app icons from public/icone.svg (run once, the PNGs are committed):
//   npx pwa-assets-generator
// minimal-2023: favicon, 64/192/512 px, a "maskable" 512 px for Android and the 180 px apple-touch-icon.
export default defineConfig({
  headLinkOptions: { preset: '2023' },
  preset: {
    ...minimal2023Preset,
    // The cover color fills the padding Android and iOS add around the icon.
    maskable: { ...minimal2023Preset.maskable, resizeOptions: { background: '#7d3326' } },
    apple: { ...minimal2023Preset.apple, resizeOptions: { background: '#7d3326' } },
  },
  images: ['public/icone.svg'],
})
