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
    sourcemap: false,
    // 首屏关键路径：vue 运行时与 Element Plus 分开缓存；视图按路由懒加载
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          // 表格/上传/分页只在懒加载视图中局部注册：单独成块后不会进入首屏 preload
          if (/element-plus\/es\/components\/(table|table-column)\//.test(id)) return 'ep-table'
          if (/element-plus\/es\/components\/upload\//.test(id)) return 'ep-upload'
          if (/element-plus\/es\/components\/pagination\//.test(id)) return 'ep-pagination'
          if (id.includes('element-plus') || id.includes('@element-plus')) return 'vendor-element'
          if (id.includes('/vue/') || id.includes('vue-router') || id.includes('pinia') || id.includes('@vue')) return 'vendor-vue'
          return 'vendor'
        },
      },
    },
  },
})
