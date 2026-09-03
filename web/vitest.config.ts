import { defineConfig } from 'vitest/config'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // 与 vite.config.ts 同源：单测跑的必须是同一份规范文件
      '@profiles': fileURLToPath(
        new URL('../src/tavotto/profiles/publication.json', import.meta.url),
      ),
      // 与 vite.config.ts 同源：字形覆盖表的判据必须是同一份生成物
      '@glyphcoverage': fileURLToPath(
        new URL('../src/tavotto/pdfbackend/canvas_coverage.json', import.meta.url),
      ),
      // 与 vite.playground.config.ts 同源：playground 运行时锁的唯一权威
      '@playground-runtime': fileURLToPath(
        new URL('../packaging/playground-runtime.json', import.meta.url),
      ),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
  },
})
