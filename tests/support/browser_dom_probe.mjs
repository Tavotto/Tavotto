/**
 * 把一份预览 SVG 挂进**真 Chromium**，量 DOM 节点数、JS 堆与渲染进程内存。
 *
 * 01–04 每一节的「还没解决的」里都写着同一句：浏览器侧仍未实测。引擎那一侧
 * 的数（字节数、`<path>` 数）是我们自己数出来的，而 #181 的症状发生在
 * **浏览器**里——126 MB 的字符串在 JS 堆里放着是一回事，展开成几十万个 DOM
 * 节点是另一回事。这个探针只回答后者。
 *
 * ## 三条纪律
 *
 * 1. **挂法与 `PanelView` 一致**：一次 `innerHTML` 塞进去（那正是
 *    `dangerouslySetInnerHTML` 做的事），等两帧再读数。写成 `appendChild`
 *    一个个塞会量出一条完全不同的曲线，而产品里没有那条路。
 * 2. **节点数与堆取自 Chromium 自己**（CDP `Performance.getMetrics` 的
 *    `Nodes` / `JSHeapUsedSize`），不是我们数标签数出来的。两把尺子在同一批
 *    产物上差 2.0–2.5 倍且比值不是常数——`large_preview_svg.py` 报标签数，
 *    这里报浏览器的 `Nodes`，**各报各的，绝不互相相除**。
 * 3. **卸载后强制 GC 再读一次**。不回收就量到「这一趟分配了多少」，而不是
 *    「留下了多少」——两者在开关循环里长得一模一样，而只有后者能回答
 *    「有没有泄漏」。
 *
 * ## 量内存时先说清「谁的」
 *
 * 第一版按 `ps | grep -- '--type=renderer'` 全局求和，稳定得到 5.7 GB——那是
 * 这台机器上**所有** Chromium 渲染进程（含用户自己开着的浏览器）。它稳定、
 * 可复现、量纲也对，唯独主语不是被测对象。所以这里先快照已存在的 renderer
 * pid，启动之后取差集：只认这一次新出现的那些。
 *
 * 用法：
 *
 *     node tests/support/browser_dom_probe.mjs <preview.svg> [cycles]
 *
 * 需要 `web/node_modules`（`cd web && pnpm install`）。stdout 是 JSON。
 */
import { readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '..', '..')
// playwright 装在 web/ 的 node_modules 里（前端那套依赖），不是仓库根
const require = createRequire(path.join(REPO, 'web', 'package.json'))
const { chromium } = require('@playwright/test')

const svgPath = process.argv[2]
const cycles = Number(process.argv[3] ?? 5)
if (!svgPath) {
  console.error('用法: node tests/support/browser_dom_probe.mjs <preview.svg> [cycles]')
  process.exit(2)
}
const svg = readFileSync(svgPath, 'utf8')

/** 此刻机器上所有 Chromium 渲染进程的 pid → RSS(KB)。 */
const rendererPids = () => {
  try {
    const out = execSync("ps -Ao pid=,rss=,command= | grep -- '--type=renderer' | grep -v grep", {
      encoding: 'utf8',
      shell: '/bin/sh',
    })
    const m = new Map()
    for (const line of out.trim().split('\n').filter(Boolean)) {
      const [pid, rss] = line.trim().split(/\s+/)
      m.set(Number(pid), Number(rss))
    }
    return m
  } catch {
    // 一个 renderer 都没有时 grep 退出码非 0。**这不是错误**，是零。
    return new Map()
  }
}

const before = new Set(rendererPids().keys())
const browser = await chromium.launch()
const page = await browser.newPage()
await page.setContent('<!doctype html><html><body><div id="host"></div></body></html>')
const cdp = await page.context().newCDPSession(page)
await cdp.send('Performance.enable')

// 启动之后新出现的 renderer 就是我们的。前提是测量期间不另开 Chromium——
// 采样失败时如实报 null，不猜（`ourRssKb` 为 null 只说明这一项没量到，
// 节点数与堆照样是真的）。
const ours = [...rendererPids().keys()].filter((pid) => !before.has(pid))

const ourRssKb = () => {
  const live = rendererPids()
  const rows = ours.filter((pid) => live.has(pid)).map((pid) => live.get(pid))
  return rows.length ? rows.reduce((a, b) => a + b, 0) : null
}

const metrics = async () => {
  const { metrics: raw } = await cdp.send('Performance.getMetrics')
  const m = Object.fromEntries(raw.map((x) => [x.name, x.value]))
  return { nodes: m.Nodes, jsHeapUsed: m.JSHeapUsedSize }
}

const sample = async () => ({ ...(await metrics()), ourRssKb: ourRssKb() })

const rows = []
const baseline = await sample()

for (let i = 0; i < cycles; i++) {
  const t0 = Date.now()
  await page.evaluate((s) => {
    document.getElementById('host').innerHTML = s
  }, svg)
  const mountMs = Date.now() - t0
  // 两帧：第一帧只保证样式算完，布局与栅格化落在第二帧
  await page.evaluate(
    () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
  )
  const mounted = await sample()

  await page.evaluate(() => {
    document.getElementById('host').innerHTML = ''
  })
  await cdp.send('HeapProfiler.enable')
  await cdp.send('HeapProfiler.collectGarbage')
  await page.evaluate(() => new Promise((r) => setTimeout(r, 300)))
  const unmounted = await sample()

  rows.push({ cycle: i + 1, mountMs, mounted, unmounted })
}

console.log(
  JSON.stringify(
    {
      svgPath,
      svgBytes: Buffer.byteLength(svg),
      ourRendererPids: ours,
      baseline,
      cycles: rows,
    },
    null,
    1,
  ),
)
await browser.close()
