import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

/**
 * 浏览器 playground（网站 `/try`）的构建配置。
 *
 * 与 MCP 画布（vite.mcp.config.ts）不同，这不是单文件产物：页面部署在
 * tavotto.com 的静态托管上，正常的分文件 + 内容哈希缓存更合适。产物由
 * `scripts/build_browser_playground.py` 收尾（改名 index.html、附上
 * engine.zip 与指纹 manifest），再由网站仓库 `pnpm sync-playground` 收走。
 *
 * `base: './'`：页面挂在 `/try/` 子路径下，资源引用必须是相对的。
 * Pyodide 本体**不在**这次构建里——它按 packaging/playground-runtime.json
 * 钉死的版本在运行时从 CDN 拉（决定与代价见 ADR 0007）。
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@profiles': fileURLToPath(
        new URL('../src/tavotto/profiles/publication.json', import.meta.url),
      ),
      '@playground-runtime': fileURLToPath(
        new URL('../packaging/playground-runtime.json', import.meta.url),
      ),
    },
  },
  base: './',
  build: {
    outDir: 'dist-playground',
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./playground.html', import.meta.url)),
    },
  },
})
