import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    proxy: {
      '/api': process.env.DOUDIZHU_DEV_BACKEND || 'http://localhost:8000',
      '/ws': {
        target: (process.env.DOUDIZHU_DEV_BACKEND || 'http://localhost:8000').replace(/^http/, 'ws'),
        ws: true,
      },
    },
  },
})
