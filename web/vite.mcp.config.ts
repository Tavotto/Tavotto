import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

/**
 * MCP App 画布的构建配置。
 *
 * 产物要能塞进一个 MCP 资源里，所以**必须是单文件**：`assetsInlineLimit`
 * 拉满 + 单 chunk，再由 `scripts/build_mcp_widget.py` 把 JS/CSS 内联进 HTML。
 * 页面里不许有任何外部请求——MCP Apps 的 CSP 由 `_meta.ui.csp` 声明，
 * 而我们声明的是**空的 connectDomains**（画布与后端之间只走 tools/call）。
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@profiles': fileURLToPath(
        new URL('../src/tavotto/profiles/publication.json', import.meta.url),
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
