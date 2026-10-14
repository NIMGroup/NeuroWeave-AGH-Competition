import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_AGH_SERVER === 'true' ? 'http://127.0.0.1:8127' : 'http://127.0.0.1:8010',
        changeOrigin: true,
        secure: false,
      },
    },
  },
})
