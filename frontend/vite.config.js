import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 13.7：dev 模式 /api → 后端 127.0.0.1:8000（生产由 FastAPI 同源托管）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
})
