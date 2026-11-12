/**
 * 竖排折行的浏览器那一侧（ADR 0163）：把若干段文字按画布 `TextView` 的竖排样式
 * （`writing-mode: vertical-rl; text-orientation: mixed; text-spacing-trim: space-all`，白空间 / 断词同横排）
 * 挂进真 Chromium，量每个字符落在第几列，回 `[[列文字, …], …]`。
 *
 * 用法：node vertical_css_probe.mjs <web 目录> <输入 JSON>
 * 输入 `{fonts: {serif: <url>, cjk: <url>}, cases: [{text, size_pt, line_height, h_mm}]}`；输出同序的列文字数组。
 * 另有 `formula_cases: [{parts: [{t: 文字} | {m: {width, ascent, descent}}], size_pt, line_height, h_mm}]`：
 * 公式按 `MathFormula` 的竖排形态（`web/src/lib/verticalFormula.ts` + `verticalMetrics.ts`，本脚本把这两份源码
 * 转译后装进页面，读的是同一份）挂进同样的竖排容器，量每个公式的轮廓落在第几列、沿列距列首多远、
 * 横向相对列中线的左右边界，以及公式后第一个字的位置（= 公式预留的长度）。
 * 输出 `{cols: [[列文字…]…], formulas: [[{col, pos, len, left, right, next}…]…]}`，长度单位 pt（左右边界相对列中线）。
 * 字体用批准字体的文件（@font-face），不用机器上碰巧装着的——预览与导出各用各的脸时本来就会有差，
 * 这里要比的是**折行规则**，不是字体度量。
 */
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const [webDir, inputPath] = process.argv.slice(2)
const require = createRequire(path.join(webDir, 'package.json'))
const { chromium } = require('@playwright/test')
const input = JSON.parse(readFileSync(inputPath, 'utf8'))
const ts = require('typescript')
// 预览的公式摆法源码：纯函数、无 import，去掉 export 后当全局函数装进页面
const shared = ['src/lib/verticalFormula.ts', 'src/lib/verticalMetrics.ts']
  .map((f) =>
    ts
      .transpileModule(readFileSync(path.join(webDir, f), 'utf8'), {
        compilerOptions: { target: 'ES2020', module: 'ESNext' },
      })
      .outputText.replace(/^export /gm, ''),
  )
  .join('\n')

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
  await page.addScriptTag({ content: shared })
  const formulas = []
  for (const c of input.formula_cases ?? []) {
    formulas.push(
      await page.evaluate((c) => {
        const MM = 96 / 25.4
        const sizePx = c.size_pt * (96 / 72)
        const columnPx = sizePx * c.line_height
        const stack = 'RcSerif, RcCjk'
        const el = document.createElement('div')
        el.className = 't'
        el.style.height = `${c.h_mm * MM}px`
        el.style.fontSize = `${sizePx}px`
        el.style.lineHeight = String(c.line_height)
        const marks = []
        c.parts.forEach((p, i) => {
          if (p.t !== undefined) {
            el.appendChild(document.createTextNode(p.t))
            return
          }
          const g = verticalFormulaBox({
            columnPx,
            sizePx,
            widthEm: p.m.width,
            ascentEm: p.m.ascent,
            descentEm: p.m.descent,
            baselineShiftPx: sidewaysBaselineShift(stack, sizePx),
          })
          const wrap = document.createElement('span')
          Object.assign(wrap.style, { display: 'inline-block', position: 'relative', width: g.wrapper.width + 'px', height: g.wrapper.height + 'px' })
          const NS = 'http://www.w3.org/2000/svg'
          const svg = document.createElementNS(NS, 'svg')
          svg.setAttribute('viewBox', `0 ${-p.m.ascent} ${p.m.width} ${p.m.ascent + p.m.descent}`)
          svg.setAttribute('width', g.svg.width)
          svg.setAttribute('height', g.svg.height)
          Object.assign(svg.style, { position: 'absolute', left: g.svg.left + 'px', top: g.svg.top + 'px', transformOrigin: g.svg.transformOrigin, transform: g.svg.transform, overflow: 'visible' })
          const path = document.createElementNS(NS, 'path')
          // 整块实心矩形：轮廓的包围盒就是公式盒（上升 / 下降 / 宽）
          path.setAttribute('d', `M0 ${-p.m.ascent} L${p.m.width} ${-p.m.ascent} L${p.m.width} ${p.m.descent} L0 ${p.m.descent} Z`)
          svg.appendChild(path)
          wrap.appendChild(svg)
          el.appendChild(wrap)
          marks.push({ path, i })
        })
        document.body.appendChild(el)
        const box = el.getBoundingClientRect()
        const PT = 72 / 96
        const out = marks.map(({ path, i }) => {
          const r = path.getBoundingClientRect()
          const col = Math.round((box.right - (r.left + r.right) / 2 - columnPx / 2) / columnPx)
          const cx = box.right - (col + 0.5) * columnPx
          // 公式后第一个字的位置（沿列方向离列首）
          const nxt = c.parts[i + 1]
          let next = null
          if (nxt && nxt.t) {
            const tn = el.childNodes[Array.prototype.indexOf.call(el.childNodes, path.ownerSVGElement.parentNode) + 1]
            const rg = document.createRange()
            rg.setStart(tn, 0)
            rg.setEnd(tn, 1)
            next = (rg.getBoundingClientRect().top - box.top) * PT
          }
          return { col, pos: (r.top - box.top) * PT, len: r.height * PT, left: (r.left - cx) * PT, right: (r.right - cx) * PT, next }
        })
        el.remove()
        return out
      }, c),
    )
  }
  console.log(JSON.stringify({ cols: out, formulas }))
} finally {
  await browser.close()
}
