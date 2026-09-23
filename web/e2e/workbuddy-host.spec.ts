import { test, expect, type FrameLocator, type Page } from '@playwright/test'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { cpSync, existsSync, mkdtempSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { createInterface } from 'node:readline'

/**
 * WorkBuddy P0 spike：**按 WorkBuddy 公开文档形状**的假宿主 × **真** Tavotto MCP server。
 *
 * 与 `mcp-canvas.spec.ts` 的区别：那边的引擎响应是用例捏的；这里 host 页面的每一次
 * `tools/call` / `resources/read` 都经 Node 转发到一个真 `codex-plugin/mcp/server.py`
 * 子进程（真启动器 → 真 resolver → 真引擎 → 真 matplotlib），画布是本次构建的真产物。
 *
 * 宿主一侧照 WorkBuddy「MCP Apps 接入指南」（腾讯云文档 1831/137044，2026-08-26 版）写：
 *   * 异源 sandbox proxy（host 自供），里面再嵌 guest iframe，sandbox 属性
 *     `allow-scripts allow-same-origin allow-forms`，CSP 从资源 `_meta.ui.csp` 生成 `<meta>`；
 *   * HTML > 256 KB 时 host **不预取**，只把 `resourceUri` 交给 proxy，由 guest 一侧经
 *     `resources/read` 回拉；
 *   * 反向 `tools/call` **默认弹框授权**；「始终允许」按 (server, tool) 在本 session 内生效，
 *     `/clear` 后失效；拒绝时 guest 拿到 `{ isError: true }`、server 不被调用；
 *   * hostContext：theme / displayMode / locale / containerDimensions / safeAreaInsets，
 *     变化走 `ui/notifications/host-context-changed`。
 *
 * **它不是 WorkBuddy。** 文档没写的（CSP 原文、握手 JSON、权限框文案、ACP 通知上限、
 * 什么时候建 iframe）这里都是假设——结论只能是「Tavotto 在文档描述的宿主里能跑」，
 * 真客户端验收见 `docs/acceptance/workbuddy-mcp-app.md`。
 *
 * 需要一个装了 tavotto 的解释器：`WB_SPIKE_PYTHON`（缺省仓库 `.venv/bin/python`）。
 * 找不到就整组 skip 并在报告里说出口——**skip 不是绿**。
 */

const REPO = path.resolve(import.meta.dirname, '..', '..')
const PLUGIN = path.join(REPO, 'codex-plugin')
const CORPUS = path.join(REPO, 'tests', 'acceptance', 'corpus')
const LARGE = path.join(REPO, 'tests', 'workbuddy', 'fixtures', 'large')
const PYTHON = process.env.WB_SPIKE_PYTHON || path.join(REPO, '.venv', 'bin', 'python')
const HOST = 'http://workbuddy-host.test'
const SANDBOX = 'http://workbuddy-sandbox.test'
const LAZY_THRESHOLD = 256 * 1024

test.skip(!existsSync(PYTHON), `没有装了 tavotto 的解释器（WB_SPIKE_PYTHON=${PYTHON}）——本组未执行`)
test.skip(
  !existsSync(path.join(PLUGIN, 'mcp', 'widget', 'canvas.html')),
  '画布产物不在：先 python scripts/build_mcp_widget.py',
)

// ---------------------------------------------------------------- 真 stdio server
type Json = Record<string, unknown>

class RealServer {
  private proc: ChildProcessWithoutNullStreams
  private id = 0
  private waiters = new Map<number, (m: Json) => void>()
  readonly serverRequests: string[] = []
  stderr = ''

  private elicitation: 'accept' | 'decline'

  constructor(elicitation: 'accept' | 'decline') {
    this.elicitation = elicitation
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      TAVOTTO_MCP_PYTHON: PYTHON,
    }
    for (const k of Object.keys(env)) {
      if (k.startsWith('CODEX_') || (k.startsWith('TAVOTTO_MCP_') && k !== 'TAVOTTO_MCP_PYTHON'))
        delete env[k]
    }
    delete env.PYTHONPATH // 见 tests/workbuddy/probe.py：它会让启动器自己的解释器被选成引擎
    // WorkBuddy 连接器 mcp.json 写 `"cwd": "."` = 连接器目录
    this.proc = spawn('python3', [path.join(PLUGIN, 'mcp', 'server.py')], {
      cwd: PLUGIN,
      env,
    })
    this.proc.stderr.on('data', (b: Buffer) => {
      this.stderr = (this.stderr + b.toString()).slice(-8000)
    })
    createInterface({ input: this.proc.stdout }).on('line', (line) => {
      if (!line.trim()) return
      const msg = JSON.parse(line) as Json
      if (typeof msg.method === 'string' && msg.id != null) return this.answer(msg)
      const w = this.waiters.get(msg.id as number)
      if (w) {
        this.waiters.delete(msg.id as number)
        w(msg)
      }
    })
  }

  private write(msg: Json) {
    this.proc.stdin.write(JSON.stringify(msg) + '\n')
  }

  private answer(msg: Json) {
    this.serverRequests.push(msg.method as string)
    if (msg.method === 'elicitation/create') {
      const result =
        this.elicitation === 'accept'
          ? { action: 'accept', content: { approve: true } }
          : { action: 'decline' }
      return this.write({ jsonrpc: '2.0', id: msg.id, result })
    }
    this.write({
      jsonrpc: '2.0',
      id: msg.id,
      error: { code: -32601, message: 'unsupported' },
    })
  }

  rpc(method: string, params?: Json, timeoutMs = 600_000): Promise<Json> {
    const id = ++this.id
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`${method} 超时\n${this.stderr}`)), timeoutMs)
      this.waiters.set(id, (m) => {
        clearTimeout(t)
        resolve(m)
      })
      this.write({ jsonrpc: '2.0', id, method, ...(params ? { params } : {}) })
    })
  }

  async init(caps: Json = { elicitation: {} }) {
    const r = await this.rpc('initialize', {
      protocolVersion: '2025-06-18',
      capabilities: caps,
      clientInfo: { name: 'workbuddy-spike-fake-host', version: '0' },
    })
    this.write({ jsonrpc: '2.0', method: 'notifications/initialized' })
    return r
  }

  async call(name: string, args: Json): Promise<Json> {
    const r = await this.rpc('tools/call', { name, arguments: args })
    return r.result as Json
  }

  close() {
    this.proc.stdin.end()
    setTimeout(() => this.proc.kill(), 5_000).unref()
  }
}

const sc = (r: Json) => (r.structuredContent ?? {}) as Json

function workspace(src: string): string {
  const dir = path.join(mkdtempSync(path.join(tmpdir(), 'wb-e2e-')), path.basename(src))
  cpSync(src, dir, {
    recursive: true,
    filter: (p) => !/__pycache__|\.(pdf|png|svg)$/.test(p),
  })
  return dir
}

// ---------------------------------------------------------------- 假宿主页面
/**
 * 宿主页：按文档流程建 sandbox → 交 resourceUri（> 256 KB 不预取）→ guest 握手 →
 * 推 tool-input / tool-result → 对 guest 的 tools/call 走权限闸。
 * `__mcp(method, params)` 是 Node 暴露进来的转发函数（真 server）。
 */
const HOST_HTML = `<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%} iframe{border:0;width:100%;height:100%;display:block}
</style></head><body><iframe id="sandbox" src="${SANDBOX}/proxy.html"></iframe><script>
const W = window
W.__PROMPTS__ = []      // 每弹一次权限框记一条
W.__FORWARDED__ = []    // 真的转发到 server 的 tools/call
W.__DENIED__ = []
W.__DISPLAY__ = []
W.__SIZES__ = []
W.__LOG__ = []
W.__ALWAYS__ = new Set()
W.__clear = () => W.__ALWAYS__.clear()  // 模拟 /clear
const cfg = W.__CFG__
let hostContext = { ...cfg.hostContext }
const sandbox = document.getElementById('sandbox')
const post = (m) => sandbox.contentWindow.postMessage(m, '${SANDBOX}')
const decide = () => (W.__DECISIONS__.length ? W.__DECISIONS__.shift() : 'deny')

async function onGuestToolCall(msg) {
  const tool = msg.params.name
  const key = 'tavotto:' + tool
  if (!W.__ALWAYS__.has(key)) {
    const d = decide()
    W.__PROMPTS__.push(tool)
    if (d === 'always') W.__ALWAYS__.add(key)
    if (d === 'deny') {
      W.__DENIED__.push(tool)
      post({ jsonrpc: '2.0', id: msg.id, result: { isError: true, content: [{ type: 'text', text: '用户拒绝了本次工具调用' }] } })
      return
    }
  }
  W.__FORWARDED__.push(JSON.parse(JSON.stringify(msg.params)))
  const r = await W.__mcp('tools/call', msg.params)
  post(r.error ? { jsonrpc: '2.0', id: msg.id, error: r.error } : { jsonrpc: '2.0', id: msg.id, result: r.result })
}

window.addEventListener('message', async (ev) => {
  if (ev.origin !== '${SANDBOX}') return
  const msg = ev.data
  if (!msg || msg.jsonrpc !== '2.0') return
  W.__LOG__.push(msg.method || ('response:' + msg.id))
  if (msg.method === 'ui/notifications/sandbox-proxy-ready') {
    // 文档：> 256 KB 不预取，只传 resourceUri；这里用 resources/list 里没有的 size
    // 没法事先知道体积，所以按配置决定（真 WorkBuddy 怎么判是待问的问题之一）
    post({ jsonrpc: '2.0', method: 'ui/notifications/sandbox-resource-ready', params:
      cfg.lazy ? { resourceUri: cfg.resourceUri, csp: cfg.csp } : { html: cfg.html, csp: cfg.csp } })
    return
  }
  if (msg.method === 'resources/read') {  // 只读，无需授权
    const r = await W.__mcp('resources/read', msg.params)
    W.__LAZY_READ__ = { uri: msg.params.uri, bytes: r.result ? r.result.contents[0].text.length : null }
    post({ jsonrpc: '2.0', id: msg.id, ...(r.error ? { error: r.error } : { result: r.result }) })
    return
  }
  if (msg.method === 'ui/initialize') {
    post({ jsonrpc: '2.0', id: msg.id, result: {
      protocolVersion: msg.params.protocolVersion,
      hostInfo: { name: 'workbuddy-spike-fake-host', version: '0' },
      hostCapabilities: { serverTools: {}, serverResources: {}, openLinks: {} },
      hostContext,
    }})
    return
  }
  if (msg.method === 'ui/notifications/initialized') {
    W.__READY__ = true
    post({ jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: { arguments: cfg.toolArgs } })
    post({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: cfg.toolResult })
    return
  }
  if (msg.method === 'tools/call') return onGuestToolCall(msg)
  if (msg.method === 'ui/request-display-mode') {
    W.__DISPLAY__.push(msg.params.mode)
    hostContext = { ...hostContext, displayMode: msg.params.mode }
    post({ jsonrpc: '2.0', id: msg.id, result: { mode: msg.params.mode } })
    post({ jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: { displayMode: msg.params.mode } })
    return
  }
  if (msg.method === 'ui/notifications/size-changed') { W.__SIZES__.push(msg.params); return }
  if (msg.id != null && msg.method) post({ jsonrpc: '2.0', id: msg.id, result: {} })
})
W.__setContext = (patch) => {
  hostContext = { ...hostContext, ...patch }
  post({ jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: patch })
}
</script></body></html>`

/** host 自供的 sandbox proxy（异源）：收 HTML 或 resourceUri，建 guest，双向转发。 */
const PROXY_HTML = `<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%} iframe{border:0;width:100%;height:100%;display:block}
</style></head><body><script>
const host = window.parent
let guest = null, lazy = null
function mount(html, csp) {
  const meta = '<meta http-equiv="Content-Security-Policy" content="' + csp + '">'
  html = /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (m) => m + meta) : meta + html
  guest = document.createElement('iframe')
  guest.id = 'guest'
  guest.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms')
  guest.srcdoc = html
  document.body.appendChild(guest)
}
window.addEventListener('message', (ev) => {
  const m = ev.data
  if (ev.source === host) {
    if (m && m.method === 'ui/notifications/sandbox-resource-ready') {
      if (m.params.html) mount(m.params.html, m.params.csp)
      else { lazy = { id: 'lazy-' + Date.now(), csp: m.params.csp }
             host.postMessage({ jsonrpc: '2.0', id: lazy.id, method: 'resources/read', params: { uri: m.params.resourceUri } }, '*') }
      return
    }
    if (lazy && m && m.id === lazy.id) { mount(m.result.contents[0].text, lazy.csp); lazy = null; return }
    if (guest) guest.contentWindow.postMessage(m, '*')
    return
  }
  if (guest && ev.source === guest.contentWindow) host.postMessage(m, '*')
})
host.postMessage({ jsonrpc: '2.0', method: 'ui/notifications/sandbox-proxy-ready', params: {} }, '*')
</script></body></html>`

/** 从 `_meta.ui.csp` 生成的 CSP（WorkBuddy 的原文未公开：这里取最严的一种写法）。 */
function cspFrom(meta: Json | undefined): string {
  const csp = (meta?.csp ?? {}) as {
    connectDomains?: string[]
    resourceDomains?: string[]
  }
  const connect = csp.connectDomains?.length ? csp.connectDomains.join(' ') : "'none'"
  const res = (csp.resourceDomains ?? []).join(' ')
  return [
    "default-src 'none'",
    `script-src 'unsafe-inline' ${res}`.trim(),
    `style-src 'unsafe-inline' ${res}`.trim(),
    `img-src data: blob: ${res}`.trim(),
    `font-src data: ${res}`.trim(),
    'media-src data: blob:',
    `connect-src ${connect}`,
  ].join('; ')
}

interface Boot {
  server: RealServer
  frame: FrameLocator
  open: Json
  project: string
}

async function boot(
  page: Page,
  opts: {
    project: string
    stem: string
    decisions: string[]
    hostContext?: Json
    lazy?: boolean
    elicitation?: 'accept' | 'decline'
  },
): Promise<Boot> {
  const server = new RealServer(opts.elicitation ?? 'accept')
  await server.init()
  // 模型那一侧的调用：WorkBuddy 的 agent 调 open，host 拿到结果再挂 UI
  const openResult = await server.call('tavotto_open_figure', {
    project_path: opts.project,
    stem: opts.stem,
  })
  const open = sc(openResult)
  expect(open.session_id, JSON.stringify(open).slice(0, 400)).toBeTruthy()
  const meta = (openResult._meta ?? {}) as Json
  const resourceUri = (meta.ui as Json | undefined)?.resourceUri as string
  expect(resourceUri).toBe('ui://tavotto/canvas/v1.html')
  const list = await server.rpc('resources/list')
  const desc = ((list.result as Json).resources as Json[])[0]
  const read = await server.rpc('resources/read', { uri: resourceUri })
  const html = (((read.result as Json).contents as Json[])[0].text as string) ?? ''
  const lazy = opts.lazy ?? html.length > LAZY_THRESHOLD

  await page.exposeFunction('__mcp', (method: string, params: Json) => server.rpc(method, params))
  await page.addInitScript(
    (cfg) => {
      ;(window as unknown as Json).__CFG__ = cfg
      ;(window as unknown as Json).__DECISIONS__ = cfg.decisions
    },
    {
      resourceUri,
      lazy,
      html: lazy ? null : html,
      csp: cspFrom(desc._meta as Json),
      toolArgs: { project_path: opts.project, stem: opts.stem },
      toolResult: openResult,
      decisions: opts.decisions,
      hostContext: {
        theme: 'light',
        displayMode: 'inline',
        availableDisplayModes: ['inline', 'fullscreen', 'pip'],
        locale: 'zh-CN',
        containerDimensions: { width: 1100, height: 760 },
        safeAreaInsets: { top: 0, right: 0, bottom: 0, left: 0 },
        ...opts.hostContext,
      },
    },
  )
  await page.route(`${HOST}/**`, (r) =>
    r.fulfill({ contentType: 'text/html; charset=utf-8', body: HOST_HTML }),
  )
  await page.route(`${SANDBOX}/**`, (r) =>
    r.fulfill({ contentType: 'text/html; charset=utf-8', body: PROXY_HTML }),
  )
  await page.setViewportSize({ width: 1100, height: 760 })
  await page.goto(`${HOST}/host.html`)
  const frame = page.frameLocator('#sandbox').frameLocator('#guest')
  return { server, frame, open, project: opts.project }
}

const w = <T>(page: Page, key: string) =>
  page.evaluate((k) => (window as unknown as Record<string, unknown>)[k], key) as Promise<T>

/** 在画布上拖 manifest 里某个元素的 bbox 中心（bbox 是 figure 分数、左上原点）。 */
async function dragElement(page: Page, frame: FrameLocator, bbox: number[], dx: number, dy: number) {
  const svg = frame.locator('[data-element-svg]').first()
  const box = (await svg.boundingBox())!
  const x = box.x + box.width * (bbox[0] + bbox[2] / 2)
  const y = box.y + box.height * (bbox[1] + bbox[3] / 2)
  await page.mouse.move(x, y)
  await page.mouse.down()
  for (let i = 1; i <= 8; i++) await page.mouse.move(x + (dx * i) / 8, y + (dy * i) / 8)
  await page.mouse.up()
}

async function serverPatches(server: RealServer, sid: string) {
  return sc(await server.call('tavotto_session_state', { session_id: sid })).patches as Json[]
}

async function currentLegend(server: RealServer, sid: string): Promise<number[]> {
  const st = sc(await server.call('tavotto_session_state', { session_id: sid }))
  return legendBBox(st)
}

/** 证据截图：设了 WB_EVIDENCE_DIR 才存（报告引用的就是这些文件）。 */
async function shot(page: Page, name: string) {
  const dir = process.env.WB_EVIDENCE_DIR
  if (dir) await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

function legendBBox(open: Json): number[] {
  const els = ((open.manifest as Json).elements as Json[]) ?? []
  return els.find((e) => e.role === 'legend' && e.draggable)!.bbox as number[]
}

// ------------------------------------------------------------------------ 用例

test('c01 真引擎：1.35 MB 画布经 guest 回拉进 ready；拖动 → 权限（始终允许）→ 真 apply；/clear 后重新要授权；预检与导出', async ({
  page,
}) => {
  const project = workspace(CORPUS)
  const { server, frame, open } = await boot(page, {
    project,
    stem: 'c01_line',
    // 第 1 次拖动：始终允许；/clear 后第 1 次：仅本次；预检：始终允许；导出：始终允许
    decisions: ['always', 'once', 'always', 'always'],
  })
  const sid = open.session_id as string
  try {
    // ① 大资源延迟加载：host 没预取，guest 一侧回拉了完整 HTML
    await expect
      .poll(() => w<Json | undefined>(page, '__LAZY_READ__'), {
        timeout: 30_000,
      })
      .toBeTruthy()
    const lazyRead = (await w<Json>(page, '__LAZY_READ__'))!
    expect(lazyRead.uri).toBe('ui://tavotto/canvas/v1.html')
    expect(lazyRead.bytes as number).toBeGreaterThan(LAZY_THRESHOLD)

    // ② 画布进 ready：真 SVG 在、stem 在、「已同步」在
    await expect(frame.locator('[data-element-svg] svg').first()).toBeVisible({
      timeout: 60_000,
    })
    await expect(frame.getByText('c01_line').first()).toBeVisible()
    await expect(frame.getByText('已同步')).toBeVisible()
    // 画布一握手就要全屏
    await expect.poll(() => w<string[]>(page, '__DISPLAY__')).toContain('fullscreen')
    // 没有授权过的反向调用：启动阶段一次都没发（小图是完整结果，不用取件）
    expect(await w<string[]>(page, '__PROMPTS__')).toEqual([])

    // ③ 第一次拖图例：弹一次权限框、选「始终允许」、真 server 收到全量 patches
    const bbox = legendBBox(open)
    await dragElement(page, frame, bbox, -60, 40)
    await expect
      .poll(async () => (await serverPatches(server, sid)).length, {
        timeout: 60_000,
      })
      .toBe(1)
    const first = (await serverPatches(server, sid))[0]
    expect(first.gid).toBe('axes_0.legend')
    expect(await w<string[]>(page, '__PROMPTS__')).toEqual(['tavotto_apply_overrides'])
    await expect(frame.getByText('已同步')).toBeVisible({ timeout: 60_000 })

    // ④ 再拖两次：不再弹框（session 级始终允许），server 的值跟着变
    await shot(page, 'c01-after-first-drag')
    for (const [dx, dy] of [
      [30, -20],
      [-20, 15],
    ]) {
      const before = JSON.stringify((await serverPatches(server, sid))[0].value)
      await dragElement(page, frame, await currentLegend(server, sid), dx, dy)
      await expect
        .poll(async () => JSON.stringify((await serverPatches(server, sid))[0]?.value), { timeout: 60_000 })
        .not.toBe(before)
      await expect(frame.getByText('已同步')).toBeVisible({ timeout: 60_000 })
    }
    expect(await w<string[]>(page, '__PROMPTS__')).toEqual(['tavotto_apply_overrides'])
    const applies = (await w<Json[]>(page, '__FORWARDED__')).filter(
      (c) => c.name === 'tavotto_apply_overrides',
    )
    expect(applies.length).toBe(3)

    // ⑤ /clear：始终允许失效，下一次拖动重新弹框
    await page.evaluate(() => (window as unknown as { __clear: () => void }).__clear())
    const moved3 = JSON.stringify((await serverPatches(server, sid))[0].value)
    await dragElement(page, frame, await currentLegend(server, sid), -15, 15)
    await expect
      .poll(async () => JSON.stringify((await serverPatches(server, sid))[0]?.value), { timeout: 60_000 })
      .not.toBe(moved3)
    expect(await w<string[]>(page, '__PROMPTS__')).toEqual([
      'tavotto_apply_overrides',
      'tavotto_apply_overrides',
    ])
    await expect(frame.getByText('已同步')).toBeVisible({ timeout: 60_000 })

    // ⑥ 预检（另一个工具 = 另一次授权）
    // 编辑之后这颗按钮写的是「预检已过期」，编辑前写计数——两种都含「预检」或「阻断」
    await frame.getByRole('button', { name: /预检|阻断/ }).click()
    await expect.poll(() => w<string[]>(page, '__PROMPTS__')).toContain('tavotto_preflight')
    await expect(frame.getByText(/仍要导出/)).toBeVisible({ timeout: 60_000 })

    // ⑦ 导出：c01_line 在课题组规范下有阻断项，先按界面要求显式确认再导出
    await frame.getByText(/仍要导出/).click()
    await frame.getByRole('button', { name: /导出 PDF\+PNG/ }).click()
    // 判据是「已导出」那条提示——面包屑里本来就写着 c01_line.pdf，按 .pdf 找会提前成立
    await expect(frame.getByText(/已导出/).first()).toBeVisible({
      timeout: 120_000,
    })
    await shot(page, 'c01-exported')
    const exportDir = path.join(project, 'tavottofile', 'export')
    const pdfs = readdirSync(exportDir).filter((f) => f.endsWith('.pdf'))
    expect(pdfs.length).toBeGreaterThan(0)
    expect(await w<string[]>(page, '__PROMPTS__')).toEqual([
      'tavotto_apply_overrides',
      'tavotto_apply_overrides',
      'tavotto_preflight',
      'tavotto_export',
    ])
    test.info().annotations.push({
      type: 'evidence',
      description: JSON.stringify({
        prompts: await w<string[]>(page, '__PROMPTS__'),
        forwarded: (await w<Json[]>(page, '__FORWARDED__')).map((c) => c.name),
        display: await w<string[]>(page, '__DISPLAY__'),
        sizes: (await w<Json[]>(page, '__SIZES__')).length,
        lazyRead,
        pdfs,
      }),
    })
  } finally {
    server.close()
    rmSync(path.dirname(project), { recursive: true, force: true })
  }
})

test('拒绝反向 tools/call：guest 拿到 isError，server 零调用、图不变，画布如实报错', async ({ page }) => {
  const project = workspace(CORPUS)
  const { server, frame, open } = await boot(page, {
    project,
    stem: 'c01_line',
    decisions: ['deny'],
  })
  try {
    await expect(frame.getByText('已同步')).toBeVisible({ timeout: 60_000 })
    await dragElement(page, frame, legendBBox(open), -60, 40)
    await expect
      .poll(() => w<string[]>(page, '__DENIED__'), { timeout: 30_000 })
      .toEqual(['tavotto_apply_overrides'])
    expect(await w<Json[]>(page, '__FORWARDED__')).toEqual([])
    expect(await serverPatches(server, open.session_id as string)).toEqual([])
    // 不把拒绝当成功：画布显示渲染失败，并给出重试
    await expect(frame.getByText('已同步')).toHaveCount(0, { timeout: 30_000 })
    await shot(page, 'deny-apply')
  } finally {
    server.close()
    rmSync(path.dirname(project), { recursive: true, force: true })
  }
})

test('工作区授权被拒：open 不建会话、不带 UI', async () => {
  const project = workspace(CORPUS)
  const server = new RealServer('decline')
  try {
    await server.init()
    const r = await server.call('tavotto_open_figure', {
      project_path: project,
      stem: 'c01_line',
    })
    expect(sc(r).code).toBe('workspace_confirmation_declined')
    expect(sc(r).session_id ?? null).toBeNull()
    expect(server.serverRequests).toEqual(['elicitation/create'])
    const h = sc(await server.call('tavotto_health', {}))
    expect(h.sessions).toEqual([])
  } finally {
    server.close()
    rmSync(path.dirname(project), { recursive: true, force: true })
  }
})

test('hostContext：英文宿主 → 英文画布；host-context-changed 切回中文；主题读不读如实记录', async ({
  page,
}) => {
  const project = workspace(CORPUS)
  const { server, frame } = await boot(page, {
    project,
    stem: 'c01_line',
    decisions: [],
    hostContext: { locale: 'en-US', theme: 'dark' },
  })
  try {
    await expect(frame.getByText('In sync')).toBeVisible({ timeout: 60_000 })
    await shot(page, 'hostctx-en-dark')
    await expect(frame.getByRole('button', { name: /Export PDF\+PNG/ })).toBeVisible()
    // 主题：宿主说 dark，画布是否跟随——记录事实，不在 spike 里判红
    const themeFacts = await frame.locator('html').evaluate((el) => ({
      dataTheme: el.getAttribute('data-theme'),
      colorScheme: getComputedStyle(el).colorScheme,
      bodyBg: getComputedStyle(document.body).backgroundColor,
    }))
    test.info().annotations.push({
      type: 'theme-dark-host',
      description: JSON.stringify(themeFacts),
    })
    // 会话中途切语言：WorkBuddy 文档说 locale「不变」，这里只记录事实不判——
    // 2026-09-23 实测属性页 / 预检列表切过去了，顶栏（McpApp 的 mc() 文案）没有重渲染
    await page.evaluate(() =>
      (window as unknown as { __setContext: (p: unknown) => void }).__setContext({ locale: 'zh-CN' }),
    )
    await expect(frame.getByText('整张图')).toBeVisible({ timeout: 30_000 })
    await shot(page, 'hostctx-after-zh-switch')
    test.info().annotations.push({
      type: 'midsession-locale-switch',
      description: JSON.stringify({
        header_synced_zh: await frame.getByText('已同步').count(),
        header_synced_en: await frame.getByText('In sync').count(),
      }),
    })
  } finally {
    server.close()
    rmSync(path.dirname(project), { recursive: true, force: true })
  }
})

test('大图（448 元素）：open 结果省略 manifest，画布经 tavotto_session_state 取件进 ready——取件本身也要过权限闸', async ({
  page,
}) => {
  const project = workspace(LARGE)
  const { server, frame, open } = await boot(page, {
    project,
    stem: 'big_fig',
    decisions: ['always', 'always'],
  })
  try {
    expect((open.elided as Json | undefined)?.fields).toContain('manifest')
    await expect(frame.locator('[data-element-svg] svg').first()).toBeVisible({
      timeout: 120_000,
    })
    await expect(frame.getByText('已同步')).toBeVisible({ timeout: 60_000 })
    await shot(page, 'large-ready')
    const forwarded = (await w<Json[]>(page, '__FORWARDED__')).map((c) => c.name)
    expect(forwarded).toEqual(['tavotto_session_state'])
    expect(forwarded).not.toContain('tavotto_open_figure')
    // WorkBuddy 的权限模型下，大图在**显示之前**就要用户点一次授权
    expect(await w<string[]>(page, '__PROMPTS__')).toEqual(['tavotto_session_state'])
    const title = (
      (
        await server.call('tavotto_session_state', {
          session_id: open.session_id as string,
        })
      ).structuredContent as Json
    ).manifest as Json
    const t = (title.elements as Json[]).find((e) => e.gid === 'axes_0.title')!
    await dragElement(page, frame, t.bbox as number[], 40, 10)
    await expect
      .poll(async () => (await serverPatches(server, open.session_id as string)).length, { timeout: 120_000 })
      .toBe(1)
    expect(await w<string[]>(page, '__PROMPTS__')).toEqual([
      'tavotto_session_state',
      'tavotto_apply_overrides',
    ])
  } finally {
    server.close()
    rmSync(path.dirname(project), { recursive: true, force: true })
  }
})
