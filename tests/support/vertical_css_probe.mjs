/**
 * 竖排折行的浏览器那一侧（ADR 0163）：把若干段文字按画布 `TextView` 的竖排样式
 * （`writing-mode: vertical-rl; text-orientation: mixed; text-spacing-trim: space-all`，白空间 / 断词同横排）
 * 挂进真 Chromium，量每个字符落在第几列，回 `[[列文字, …], …]`。
 *
 * 用法：node vertical_css_probe.mjs <web 目录> <输入 JSON>
 * 输入 `{fonts: {serif: <url>, cjk: <url>}, cases: [{text, size_pt, line_height, h_mm}]}`；输出同序的列文字数组。
 * 字体用批准字体的文件（@font-face），不用机器上碰巧装着的——预览与导出各用各的脸时本来就会有差，
 * 这里要比的是**折行规则**，不是字体度量。
 */
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import path from 'node:path'

const [webDir, inputPath] = process.argv.slice(2)
const require = createRequire(path.join(webDir, 'package.json'))
const { chromium } = require('@playwright/test')
const input = JSON.parse(readFileSync(inputPath, 'utf8'))

const html = `<!doctype html><meta charset="utf-8"><style>
@font-face{font-family:RcSerif;src:url("${input.fonts.serif}")}
@font-face{font-family:RcCjk;src:url("${input.fonts.cjk}")}
body{margin:0}
.t{position:absolute;right:0;top:0;writing-mode:vertical-rl;text-orientation:mixed;text-spacing-trim:space-all;
  font-family:RcSerif,RcCjk;white-space:pre-wrap;word-break:break-word;box-sizing:border-box}
</style><body></body>`

const browser = await chromium.launch()
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1400 } })
  await page.setContent(html)
  await page.evaluate(() => document.fonts.ready)
  const out = []
  for (const c of input.cases) {
    const cols = await page.evaluate((c) => {
      const MM = 96 / 25.4
      const el = document.createElement('div')
      el.className = 't'
      el.style.height = `${c.h_mm * MM}px`
      el.style.fontSize = `${c.size_pt * (96 / 72)}px`
      el.style.lineHeight = String(c.line_height)
      el.textContent = c.text
      document.body.appendChild(el)
      const tn = el.firstChild
      const groups = []
      for (let i = 0; i < tn.length; i++) {
        const r = document.createRange()
        r.setStart(tn, i)
        r.setEnd(tn, i + 1)
        const q = r.getBoundingClientRect()
        const key = Math.round(q.x)
        const last = groups[groups.length - 1]
        if (last && Math.abs(last.key - key) < 4) last.s += tn.data[i]
        else groups.push({ key, s: tn.data[i] })
      }
      el.remove()
      return groups.map((g) => g.s)
    }, c)
    out.push(cols)
  }
  console.log(JSON.stringify(out))
} finally {
  await browser.close()
}
