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
 *    产物上差 2.0–3.5 倍且比值不是常数——`large_preview_svg.py` 报标签数，
 *    这里报浏览器的 `Nodes`，**各报各的，绝不互相相除**。
 * 3. **卸载后强制 GC 再读一次**。不回收就量到「这一趟分配了多少」，而不是
 *    「留下了多少」——两者在开关循环里长得一模一样，而只有后者能回答
 *    「有没有泄漏」。
 * 4. **计时报两个数，谁都不冒充谁**：`parseMs` 只到 `innerHTML` 赋值返回
 *    （解析 + 插入），`renderedMs` 走完那两帧（布局 + 首次栅格化）。用户
 *    卡住的是后者；只报前者会把一段真实的停顿说没了。实测 40 000 条折线上
 *    261 ms → 399 ms（+53%），mesh 上 75 → 87（+16%）。
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
 *     node tests/support/browser_dom_probe.mjs <preview.svg> [--cycles N] [--copies N]
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

/**
 * 参数**具名**，且不认识的一律当场退出。
 *
 * 上一版第三个位置参数是 cycles，于是 `--json`（那是 `scripts/bench_render.py`
 * 的旗标）会被 `Number()` 变成 `NaN`：循环一轮都不跑，输出一份 `cycles: []`
 * 的合法 JSON。**静默产出空结果**比报错坏得多——它看起来像「量过了，没事」。
 */
function parseArgs(argv) {
  const usage =
    '用法: node tests/support/browser_dom_probe.mjs <preview.svg> [--cycles N] [--copies N]'
  const out = { svgPath: null, cycles: 5, copies: 1 }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--cycles' || a === '--copies') {
      const n = Number(argv[++i])
      if (!Number.isInteger(n) || n < 1) {
        console.error(`${a} 要一个 >= 1 的整数，收到: ${argv[i]}\n${usage}`)
        process.exit(2)
      }
      out[a === '--cycles' ? 'cycles' : 'copies'] = n
    } else if (a.startsWith('-')) {
      console.error(`不认识的参数: ${a}\n${usage}`)
      process.exit(2)
    } else if (out.svgPath == null) {
      out.svgPath = a
    } else {
      console.error(`多余的参数: ${a}\n${usage}`)
      process.exit(2)
    }
  }
  if (out.svgPath == null) {
    console.error(usage)
    process.exit(2)
  }
  return out
}

const { svgPath, cycles, copies } = parseArgs(process.argv.slice(2))
// `--copies N` 把同一份 payload 并排挂 N 次，量的是「多面板画布」那一行：
// 每个 live 面板都被 pin 住、按设计永不驱逐，代价是线性叠加的。
const svg = readFileSync(svgPath, 'utf8').repeat(copies)

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

/**
 * 渲染进程被打死时**如实记一行，不是抛栈**。
 *
 * `--copies 4` 那一档实测就走到这里：页面直接没了，CDP 报
 * `Target page, context or browser has been closed`。上一版在这里抛未捕获
 * 异常、退出码 1、stdout 一个字节都没有——而**「浏览器死了」正是本探针要
 * 量的那个结局**，把它变成一次崩溃就等于量到了却说不出来。
 */
const crashed = (err) =>
  /Target (page|closed)|browser has been closed|crashed/i.test(String(err?.message ?? err))

for (let i = 0; i < cycles; i++) {
  try {
    const t0 = Date.now()
    await page.evaluate((s) => {
      document.getElementById('host').innerHTML = s
    }, svg)
    // **解析 + 插入**，到此为止。它不含布局与栅格化——把这个数叫「挂载耗时」
    // 会漏掉用户实际卡住的那一段，所以两个数分开报，谁都不冒充谁。
    const parseMs = Date.now() - t0
    // 两帧：第一帧只保证样式算完，布局与栅格化落在第二帧
    await page.evaluate(
      () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
    )
    // **到这里才是用户看见东西的那一刻**：解析 + 布局 + 首次栅格化都算进去了。
    // 判「会不会卡」用这个数，不是上面那个。
    const renderedMs = Date.now() - t0
    const mounted = await sample()

    await page.evaluate(() => {
      document.getElementById('host').innerHTML = ''
    })
    await cdp.send('HeapProfiler.enable')
    await cdp.send('HeapProfiler.collectGarbage')
    await page.evaluate(() => new Promise((r) => setTimeout(r, 300)))
    const unmounted = await sample()

    rows.push({ cycle: i + 1, parseMs, renderedMs, mounted, unmounted })
  } catch (err) {
    if (!crashed(err)) throw err
    rows.push({ cycle: i + 1, rendererCrashed: true, error: String(err?.message ?? err) })
    break
  }
}

console.log(
  JSON.stringify(
    {
      svgPath,
      copies,
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
