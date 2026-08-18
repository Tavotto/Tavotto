import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

const BACKEND = 'http://127.0.0.1:5089'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // 出版规范 profile 的**唯一权威文件**在 Python 包里（wheel 也带着它）。
      // 这里按路径别名整份 import 进 bundle：规则常量绝不在 TS 侧再抄一遍，
      // 否则「双栏 150mm」改一处、另一处照旧放行。
      '@profiles': fileURLToPath(
        new URL('../src/magplot/profiles/publication.json', import.meta.url),
      ),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/exports': { target: BACKEND, changeOrigin: true },
    },
  },
})
