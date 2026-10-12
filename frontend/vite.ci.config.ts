import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Intentionally no API/admin/media proxy: a missed fixture cannot reach 8087.
export default defineConfig({ plugins: [vue()], server: { host: '127.0.0.1', strictPort: true } })
