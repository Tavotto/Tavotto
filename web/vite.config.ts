import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'
import { noCopyleftPalettes } from './scripts/palette-copyleft-path.mjs'

// 开发预览换端口：TAVOTTO_DEV_BACKEND=http://127.0.0.1:5397 npx vite --port 5297
const BACKEND = process.env.TAVOTTO_DEV_BACKEND ?? 'http://127.0.0.1:5089'

export default defineConfig({
  plugins: [noCopyleftPalettes(), react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // 出版规范 profile 的**唯一权威文件**在 Python 包里（wheel 也带着它）。
      // 这里按路径别名整份 import 进 bundle：规则常量绝不在 TS 侧再抄一遍，
      // 否则「双栏 150mm」改一处、另一处照旧放行。
      '@profiles': fileURLToPath(
        new URL('../src/tavotto/profiles/publication.json', import.meta.url),
      ),
      '@journals': fileURLToPath(
        new URL('../src/tavotto/profiles/journals.json', import.meta.url),
      ),
      // Agent 新增对象的字段表（ADR 0208 §五）：唯一出处在 Python 包里，后端 engine/agentobjects.py 读同一份
      '@agentObjects': fileURLToPath(
        new URL('../src/tavotto/profiles/agent_objects.json', import.meta.url),
      ),
      // 推荐配色目录（ADR 0210）：色值的唯一权威同在 Python 包里。GPL 的期刊风格色板单独一份；
      // TAVOTTO_NO_COPYLEFT_PALETTES=1 构建时 `noCopyleftPalettes` 插件加载成空表，产物里就没有那些色值（闭源组件用，ADR 0198）
      '@palettes': fileURLToPath(
        new URL('../src/tavotto/profiles/palettes.json', import.meta.url),
      ),
      '@palettesCopyleft': fileURLToPath(
        new URL('../src/tavotto/profiles/palettes_copyleft.json', import.meta.url),
      ),
      // 画布文字的字形覆盖表（生成物，唯一产生者
      // `scripts/gen_canvas_coverage.py`）。浏览器里没有字体引擎，「这个字
      // 导出后是不是方框」只能靠它回答——同 `@profiles`，整份 import 进
      // bundle，绝不在 TS 侧抄第二份。
      '@glyphcoverage': fileURLToPath(
        new URL('../src/tavotto/pdfbackend/canvas_coverage.json', import.meta.url),
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
