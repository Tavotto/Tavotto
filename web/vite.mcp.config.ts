import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'
import { noCopyleftPalettes } from './scripts/palette-copyleft-path.mjs'

/**
 * MCP App 画布的构建配置。
 *
 * 产物要能塞进一个 MCP 资源里，所以**必须是单文件**：`assetsInlineLimit`
 * 拉满 + 单 chunk，再由 `scripts/build_mcp_widget.py` 把 JS/CSS 内联进 HTML。
 * 页面里不许有任何外部请求——MCP Apps 的 CSP 由 `_meta.ui.csp` 声明，
 * 而我们声明的是**空的 connectDomains**（画布与后端之间只走 tools/call）。
 */
export default defineConfig({
  plugins: [noCopyleftPalettes(), react(), tailwindcss()],
  // 路径查找器的 WASM 内核（ADR 0185 M2）不进内嵌画布：单文件里塞不下也没必要（宿主 CSP 不放行外部请求），
  // `lib/vecops/kernel` 读到 off 就把按钮灰掉、停上去说原因；连同 `?url` 的导入一起被摇掉
  define: { 'import.meta.env.VITE_VECOPS': JSON.stringify('off') },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
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
      '@palettes': fileURLToPath(
        new URL('../src/tavotto/profiles/palettes.json', import.meta.url),
      ),
      '@palettesCopyleft': fileURLToPath(
        new URL('../src/tavotto/profiles/palettes_copyleft.json', import.meta.url),
      ),
      '@glyphcoverage': fileURLToPath(
        new URL('../src/tavotto/pdfbackend/canvas_coverage.json', import.meta.url),
      ),
    },
  },
  build: {
    outDir: 'dist-mcp',
    emptyOutDir: true,
    // 单文件：图标字体之类一律 base64 内联；代码不切块
    assetsInlineLimit: 100 * 1024 * 1024,
    cssCodeSplit: false,
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: fileURLToPath(new URL('./mcp.html', import.meta.url)),
      output: {
        inlineDynamicImports: true,
        entryFileNames: 'canvas.js',
        assetFileNames: 'canvas.[ext]',
      },
    },
  },
})
