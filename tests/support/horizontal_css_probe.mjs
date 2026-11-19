/**
 * 横排行盒的浏览器那一侧（ADR 0073 修订，2026-11-16）：按画布 `TextView` 的横排摆法（外层定字号 / 行距，
 * 内容层往下挪 `horizontalBaselineFixPx`，公式按 `MathFormula` 的 `lineBox` 形态撑开所在行）挂进真 Chromium，
 * 量每一行的基线、每个公式轮廓的上下边、整个框的高度。
 *
 * 用法：node horizontal_css_probe.mjs <web 目录> <输入 JSON>
 * 输入 `{fonts: {serif, cjk}, k_per_mille, cases: [{lines: [[{t: 文字} | {m: {width, ascent, descent}}]…], size_pt,
 * line_height, w_mm, bold, cjk_first}]}`（`cjk_first`：中文脸排第一，它定行盒的度量）；输出 `[{baselines: [pt…], formulas: [{top, bottom}…], height}]`，相对框顶、单位 pt。
 * 补差的源码（`web/src/lib/lineBox.ts`）转译后装进页面，读的是同一份；字体用批准字体的文件
 * （@font-face）——浏览器按这张脸的 hhea 摆基线、导出不读度量，补差要把二者对齐，这里量的就是这一点。
 */
import { createRequire } from 'node:module'
import { readFileSync, writeFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

const [webDir, inputPath] = process.argv.slice(2)
const require = createRequire(path.join(webDir, 'package.json'))
const { chromium } = require('@playwright/test')
const input = JSON.parse(readFileSync(inputPath, 'utf8'))
const ts = require('typescript')
const shared =
  `const TEXT_BASELINE_BELOW_CENTER_PER_MILLE = ${Number(input.k_per_mille)};\n` +
  ['src/lib/lineBox.ts']
    .map((f) =>
      ts
        .transpileModule(readFileSync(path.join(webDir, f), 'utf8'), {
          compilerOptions: { target: 'ES2020', module: 'ESNext' },
        })
        .outputText.replace(/^import .*$/gm, '')
        .replace(/^export /gm, ''),
    )
    .join('\n')

const html = `<!doctype html><meta charset="utf-8"><style>
@font-face{font-family:RcSerif;src:url("${input.fonts.serif}")}
@font-face{font-family:RcSerif;font-weight:700;src:url("${input.fonts.serif_bold}")}
@font-face{font-family:RcCjk;src:url("${input.fonts.cjk}")}
body{margin:0}
.t{position:absolute;left:0;top:0;font-family:RcSerif,RcCjk;white-space:pre-wrap;word-break:break-word;box-sizing:border-box}
</style><body></body>`

const browser = await chromium.launch()
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1400 } })
  // 页面落成输入旁边的文件再打开：file:// 的字体要同源才载得进（setContent 的 about:blank 显式载入会被拒）
  const htmlPath = path.join(path.dirname(inputPath), 'probe.html')
  writeFileSync(htmlPath, html)
  await page.goto(pathToFileURL(htmlPath).href)
  await page.evaluate(async () => {
    await document.fonts.load('400 16px RcSerif')
    await document.fonts.load('700 16px RcSerif')
    await document.fonts.load('16px RcCjk', '文')
    await document.fonts.ready
  })
  await page.addScriptTag({ content: shared })
  const out = []
  for (const c of input.cases) {
    out.push(
      await page.evaluate((c) => {
        const MM = 96 / 25.4
        const PT = 72 / 96
        const sizePx = c.size_pt * (96 / 72)
        const stack = c.cjk_first ? 'RcCjk, RcSerif' : 'RcSerif, RcCjk'
        const shiftPx = horizontalBaselineFixPx(stack, sizePx, c.line_height, { bold: !!c.bold })
        const el = document.createElement('div')
        el.className = 't'
        el.style.width = `${c.w_mm * MM}px`
        el.style.fontSize = `${sizePx}px`
        el.style.lineHeight = String(c.line_height)
        el.style.fontWeight = c.bold ? '700' : '400'
        el.style.fontFamily = stack
        const lines = document.createElement('div')
        Object.assign(lines.style, { position: 'relative', top: `${shiftPx}px` })
        el.appendChild(lines)
        const marks = []
        const paths = []
        c.lines.forEach((parts, li) => {
          if (li > 0) lines.appendChild(document.createTextNode('\n'))
          // 行首一个 0×0 的行内块：它的下边缘就是这一行的基线
          const mk = document.createElement('span')
          Object.assign(mk.style, { display: 'inline-block', width: '0', height: '0' })
          lines.appendChild(mk)
          marks.push(mk)
          for (const p of parts) {
            if (p.t !== undefined) {
              lines.appendChild(document.createTextNode(p.t))
              continue
            }
            const { width, ascent, descent } = p.m
            // `MathFormula` 的横排 lineBox 形态（同一组样式）
            const wrap = document.createElement('span')
            Object.assign(wrap.style, {
              display: 'inline-block',
              position: 'relative',
              width: `${width * sizePx}px`,
              height: `${(ascent + descent) * sizePx}px`,
              verticalAlign: `${-(descent * sizePx + shiftPx)}px`,
            })
            const NS = 'http://www.w3.org/2000/svg'
            const svg = document.createElementNS(NS, 'svg')
            svg.setAttribute('viewBox', `0 ${-ascent} ${width} ${ascent + descent}`)
            svg.setAttribute('width', String(width * sizePx))
            svg.setAttribute('height', String((ascent + descent) * sizePx))
            Object.assign(svg.style, { position: 'absolute', left: '0', top: `${-shiftPx}px`, overflow: 'visible' })
            const path = document.createElementNS(NS, 'path')
            path.setAttribute('d', `M0 ${-ascent} L${width} ${-ascent} L${width} ${descent} L0 ${descent} Z`)
            svg.appendChild(path)
            wrap.appendChild(svg)
            lines.appendChild(wrap)
            paths.push(path)
          }
        })
        document.body.appendChild(el)
        const box = el.getBoundingClientRect()
        const res = {
          shift_pt: shiftPx * PT,
          baselines: marks.map((m) => (m.getBoundingClientRect().bottom - box.top) * PT),
          formulas: paths.map((p) => {
            const r = p.getBoundingClientRect()
            return { top: (r.top - box.top) * PT, bottom: (r.bottom - box.top) * PT }
          }),
          height: box.height * PT,
        }
        el.remove()
        return res
      }, c),
    )
  }
  console.log(JSON.stringify(out))
} finally {
  await browser.close()
}
