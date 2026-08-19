import { test, expect, type FrameLocator, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import path from 'node:path'

/**
 * Codex 内嵌画布（MCP App）在**真浏览器的真 iframe 里**跑一遍。
 *
 * 这是没有 Codex Desktop 时能做到的最强验证：一个模拟 host 实现 MCP Apps 的
 * JSON-RPC over postMessage（应答 `ui/initialize` → 推 `ui/notifications/tool-result`
 * → 应答 `tools/call`），把真正的产物 `codex-plugin/mcp/widget/canvas.html`
 * 装进 iframe，然后用鼠标拖。
 *
 * 它**不能**替代「Codex 真的会这么做」——那一条只有装上插件才知道，README 里如实
 * 写着。但它挡得住这一整类问题：握手写错、画布不挂载、拖动不发工具调用、发的不是
 * 全量 patches、响应回来了却不更新界面、iframe 里偷偷存业务状态。
 *
 * 不需要后端：host 是假的，引擎响应由用例给。页面走 `page.route` 伺服
 * （不用 srcdoc —— 590 KiB 的 HTML 塞进属性里既脆又难查）。
 */

const REPO = path.resolve(import.meta.dirname, '..', '..')
const WIDGET = path.join(REPO, 'codex-plugin', 'mcp', 'widget', 'canvas.html')
const ORIGIN = 'http://tavotto-mcp.test'

/** 一张最小但真实的 manifest：figure + axes + 可拖的标题 + 图例 */
function makeManifest(titlePt: number, titleAt: [number, number]) {
  return {
    stem: 'FigE2E',
    size_mm: [80, 60],
    elements: [
      {
        gid: 'figure',
        role: 'figure',
        label: '整图',
        bbox: [0, 0, 1, 1],
        draggable: false,
        editable: [{ prop: 'size_mm', type: 'pair', value: [80, 60] }],
      },
      {
        gid: 'axes_0',
        role: 'axes',
        label: '子图 1',
        bbox: [0.15, 0.13, 0.8, 0.72],
        draggable: false,
        resizable: true,
        editable: [
          { prop: 'position', type: 'rect', value: [0.15, 0.15, 0.8, 0.72] },
          { prop: 'spine_top', type: 'bool', value: true },
          { prop: 'spine_right', type: 'bool', value: true },
          { prop: 'spine_linewidth', type: 'number', value: 0.75 },
        ],
      },
      {
        gid: 'axes_0.title',
        role: 'title',
        label: '标题',
        bbox: [titleAt[0] - 0.12, titleAt[1] - 0.025, 0.24, 0.05],
        anchor: titleAt,
        drag_prop: 'pos_frac',
        draggable: true,
        editable: [
          { prop: 'text', type: 'text', value: 'Kinetics' },
          { prop: 'fontsize', type: 'number', value: titlePt, min: 3, max: 48, step: 0.5 },
          { prop: 'color', type: 'color', value: '#000000' },
        ],
      },
      {
        gid: 'axes_0.legend',
        role: 'legend',
        label: '图例',
        bbox: [0.62, 0.62, 0.28, 0.16],
        anchor: [0.62, 0.78],
        drag_prop: 'loc_frac',
        draggable: true,
        editable: [
          { prop: 'frameon', type: 'bool', value: false },
          { prop: 'fontsize', type: 'number', value: 9, min: 3, max: 24, step: 0.5 },
        ],
      },
    ],
  }
}

const SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="226.7pt" height="170pt" ' +
  'viewBox="0 0 226.7 170"><rect width="226.7" height="170" fill="#fff"/>' +
  '<g id="axes_0"><rect x="34" y="22" width="181" height="122" fill="none" stroke="#000"/></g>' +
  '<g id="axes_0.title"><text x="100" y="14" font-size="9">Kinetics</text></g>' +
  '<g id="axes_0.legend"><text x="150" y="120" font-size="9">A</text></g></svg>'

const OPEN_PAYLOAD = {
  ok: true,
  session_id: 's-e2e',
  project: '/tmp/figures',
  stem: 'FigE2E',
  script: 'fige2e.py',
  cost: 'light',
  manifest: makeManifest(9, [0.5, 0.06]),
  svg: SVG,
  patch_hash: 'sha256:0',
  render_revision: 1,
  warnings: [],
  registry: { parameterizable: true, conflicts: [], dynamic_names: [], stems: ['FigE2E'] },
  profile: {
    profile_id: 'lab-publication-v1',
    profile_version: '1.0.0',
    label: '课题组出版规范 v1',
  },
  preflight: {
    counts: { error: 0, warn: 1, not_verifiable: 0, suggestion: 0 },
    blocking: false,
    errors: [],
    warnings: [
      {
        id: 'page-aspect',
        severity: 'warn',
        text: '页面比例 1.333 不在规范允许的 16:9、4:3、1:1 之内',
        object_ids: [],
        gids: [],
        detail: {},
      },
    ],
    not_verifiable: [],
    suggestions: [],
  },
}

/** 模拟 host：MCP Apps 的 postMessage 桥 + 记录每一次 tools/call */
const HOST_HTML = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
  html,body{margin:0;height:100%;background:#eee} iframe{border:0;width:100%;height:100%;display:block}
</style></head><body>
<iframe id="f" src="/canvas.html"></iframe>
<script>
window.__CALLS__ = []
window.__READY__ = false
const frame = document.getElementById('f')
const post = (m) => frame.contentWindow.postMessage(m, '*')

window.addEventListener('message', (ev) => {
  const msg = ev.data
  if (!msg || typeof msg !== 'object' || msg.jsonrpc !== '2.0') return
  if (msg.method === 'ui/initialize') {
    post({ jsonrpc: '2.0', id: msg.id, result: {
      protocolVersion: msg.params.protocolVersion,
      hostInfo: { name: 'fake-codex', version: '0' },
      hostCapabilities: {},
      hostContext: { displayMode: 'fullscreen',
                     availableDisplayModes: ['inline', 'fullscreen'] },
    }})
    return
  }
  if (msg.method === 'ui/notifications/initialized') {
    window.__READY__ = true
    // host 把「带出这块画布的那次工具调用」的结果推过来（MCP Apps 标准路径）
    post({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: {
      result: { structuredContent: window.__OPEN__, content: [] },
    }})
    return
  }
  if (msg.method === 'tools/call') {
    window.__CALLS__.push(JSON.parse(JSON.stringify(msg.params)))
    post({ jsonrpc: '2.0', id: msg.id, result: window.__REPLY__(msg.params) })
    return
  }
  // host → app 的请求（ping / teardown）：有 id 就得回
  if (msg.id != null) post({ jsonrpc: '2.0', id: msg.id, result: {} })
})
</script></body></html>`

interface ToolCall {
  name: string
  arguments: Record<string, unknown>
}

async function boot(page: Page): Promise<FrameLocator> {
  const widget = readFileSync(WIDGET, 'utf-8')
  await page.route(`${ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/canvas.html') {
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: widget })
    }
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: HOST_HTML })
  })

  await page.addInitScript(
    ([open, mkManifest, svg]) => {
      const w = window as unknown as Record<string, unknown>
      w.__OPEN__ = open
      w.__SVG__ = svg
      const build = new Function(
        'pt',
        'at',
        `return (${mkManifest})(pt, at)`,
      ) as (pt: number, at: [number, number]) => unknown
      w.__REPLY__ = (params: ToolCall) => {
        if (params.name === 'tavotto_apply_overrides') {
          const patches = (params.arguments.patches ?? []) as {
            gid: string
            prop: string
            value: unknown
          }[]
          const size = patches.find((p) => p.gid === 'axes_0.title' && p.prop === 'fontsize')
          const pos = patches.find((p) => p.prop === 'pos_frac')
          return {
            content: [{ type: 'text', text: 'ok' }],
            structuredContent: {
              ok: true,
              session_id: 's-e2e',
              stem: 'FigE2E',
              manifest: build(
                typeof size?.value === 'number' ? size.value : 9,
                Array.isArray(pos?.value) ? (pos!.value as [number, number]) : [0.5, 0.06],
              ),
              svg: (svg as string).replace('Kinetics', 'Kinetics ✓'),
              patch_hash: `sha256:${patches.length}`,
              render_revision: 2 + patches.length,
              warnings: [],
              rejected: [],
              applied: patches.length,
              timings: {},
            },
          }
        }
        const clean = {
          counts: { error: 0, warn: 0, not_verifiable: 0, suggestion: 0 },
          blocking: false,
          errors: [],
          warnings: [],
          not_verifiable: [],
          suggestions: [],
        }
        if (params.name === 'tavotto_preflight') {
          return {
            content: [{ type: 'text', text: '✓ 全部通过' }],
            structuredContent: { ok: true, ...clean },
          }
        }
        if (params.name === 'tavotto_export') {
          return {
            content: [{ type: 'text', text: '已导出' }],
            structuredContent: {
              ok: true,
              files: [{ format: 'pdf', path: '/tmp/out/FigE2E.pdf' }],
              forced: false,
              preflight: clean,
            },
          }
        }
        return { isError: true, structuredContent: { ok: false, error: '未知工具' } }
      }
    },
    [OPEN_PAYLOAD, makeManifest.toString(), SVG] as const,
  )

  await page.goto(`${ORIGIN}/host.html`)
  await page.waitForFunction(() => (window as unknown as { __READY__: boolean }).__READY__, null, {
    timeout: 30_000,
  })
  const frame = page.frameLocator('#f')
  await expect(frame.getByText('FigE2E').first()).toBeVisible({ timeout: 30_000 })
  return frame
}

const calls = (page: Page) =>
  page.evaluate(() => (window as unknown as { __CALLS__: ToolCall[] }).__CALLS__)

test('画布在真 iframe 里挂起来，并把规范 / 尺寸 / 预检摆出来', async ({ page }) => {
  const frame = await boot(page)
  await expect(frame.getByText('lab-publication-v1 v1.0.0')).toBeVisible()
  await expect(frame.getByText('80.0 × 60.0 mm')).toBeVisible()
  await expect(frame.getByRole('button', { name: /0 阻断 · 1 警告/ })).toBeVisible()
  // 引擎给的 SVG 真的进了画布（而不是一个连不上的 <img>）
  await expect(frame.locator('[data-element-svg] svg').first()).toBeVisible()
  // 画布**不自己发起 open**：没有那次工具结果它就该等着，而不是编一张图出来
  expect(await calls(page)).toEqual([])
})

test('用鼠标拖图内标题 → tools/call 发全量 patches → 用响应更新画布', async ({ page }) => {
  const frame = await boot(page)

  // 图内元素的命中层就铺在 SVG 上：拿它的位置换算出标题的落点
  // （manifest 里标题的 anchor 是 figure 分数 (0.5, 0.06)）
  const svg = frame.locator('[data-element-svg]').first()
  const box = (await svg.boundingBox())!
  const x = box.x + box.width * 0.5
  const y = box.y + box.height * 0.06

  await page.mouse.move(x, y)
  await page.mouse.down()
  for (let i = 1; i <= 8; i++) await page.mouse.move(x - i * 3, y + i * 2)
  await page.mouse.up()

  await expect
    .poll(async () => (await calls(page)).map((c) => c.name), { timeout: 30_000 })
    .toContain('tavotto_apply_overrides')

  const applied = (await calls(page)).find((c) => c.name === 'tavotto_apply_overrides')!
  expect(applied.arguments.session_id).toBe('s-e2e')
  const patches = applied.arguments.patches as { gid: string; prop: string; value: unknown }[]
  // **全量列表**，而且拖出来的正是那条 figure 锚定的位置 override
  expect(Array.isArray(patches)).toBe(true)
  const moved = patches.find((p) => p.gid === 'axes_0.title' && p.prop === 'pos_frac')
  expect(moved, `拖动没产出 pos_frac：${JSON.stringify(patches)}`).toBeTruthy()
  const [px, py] = moved!.value as [number, number]
  expect(px).toBeLessThan(0.5)     // 往左拖
  expect(py).toBeGreaterThan(0.06) // 往下拖

  // 服务端回的 SVG 换上来了（「真相在服务端」这条真的成立）
  await expect(svg).toContainText('Kinetics ✓', { timeout: 30_000 })

  // 一次拖动 = 一条撤销（既有的 documentStore 事务，不是这块画布新写的）
  await expect(frame.getByRole('button', { name: '撤销' })).toBeEnabled()
})

test('预检与导出都走 tools/call，结果回到界面上', async ({ page }) => {
  const frame = await boot(page)

  // 按可访问名定位：按钮的名字是它的**文字内容**（title 只是补充说明），
  // 拿 title 当 name 找不到它
  await frame.getByRole('button', { name: /阻断/ }).click()
  await expect(frame.getByText('预检通过')).toBeVisible({ timeout: 20_000 })

  await frame.getByRole('button', { name: /导出 PDF\+PNG/ }).click()
  await expect(frame.getByText('/tmp/out/FigE2E.pdf')).toBeVisible({ timeout: 20_000 })

  const list = await calls(page)
  expect(list.map((c) => c.name)).toContain('tavotto_preflight')
  const exp = list.find((c) => c.name === 'tavotto_export')!
  expect(exp.arguments.session_id).toBe('s-e2e')
  // 没有阻断项时不该带强制标记
  expect(exp.arguments.explicit_confirm).toBe(false)
})

test('iframe 里不存业务数据（host 随时会重建它）', async ({ page }) => {
  const frame = await boot(page)
  const dump = await frame
    .locator('body')
    .evaluate(() => JSON.stringify(Object.entries(localStorage)))
  expect(dump).not.toContain('s-e2e')
  expect(dump).not.toContain('FigE2E')
})
