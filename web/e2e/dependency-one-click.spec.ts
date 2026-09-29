import { mkdirSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Locator, Page, Route } from '@playwright/test'
import { expect, test } from './fixtures'

/**
 * 缺包的一键修复（2026-09-29）：不懂 Python 的用户在素材库脚本行上只看到一句话和一个主按钮，点一次，进度
 * 只有一行（下载私有 Python 时带 MB），装好后那一行自动重跑、图出来。
 *
 * 后端这一半在别处有真跑的用例（`tests/test_deprepair*.py`）；这里要的是界面在「这台电脑没有 Python、要先下载
 * 一份」这个形状下的表现，而 CI 机器上总有 Python——所以**缺包那次试运行、计划、安装、进度事件**由用例伺服
 * （`page.route`），装好之后的重跑走真后端、真 worker：判据的主语是脚本真正产出的那张图。
 *
 * 进度事件走 SSE（`/api/events`）：每次连上发一批、带 `retry`，浏览器断开后按它重连取下一批——这样每一步
 * 的画面都能单独量，而不是一次收完只看得到终局。
 */

const SCRIPT = 'fit_curve.py'
const PLAN_ID = 'e2e-one-click-plan'
const MB = 1048576

function writeProject(dir: string): void {
  mkdirSync(dir, { recursive: true })
  // 真能画出图的脚本：缺包那一次是伺服的，装好后的重跑是真的
  writeFileSync(
    path.join(dir, SCRIPT),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'fig, ax = plt.subplots()',
      'ax.plot([0, 1, 2], [0, 1, 4])',
      'fig.savefig("curve.png")',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(path.join(dir, 'tavotto_registry.json'), JSON.stringify({ version: 1, scripts: {} }), 'utf-8')
}

const PRIVATE_PYTHON = {
  id: 'cpython-3.13-e2e',
  version: '3.13.15',
  target: 'e2e',
  source_host: 'github.com',
  download_bytes: 25 * MB,
  required: true,
  cached: false,
  network_required: true,
  origin: 'download',
}

const REQUIREMENT = {
  import_name: 'openpyxl',
  distribution: 'openpyxl',
  specifier: '',
  requirement: 'openpyxl',
  resolution_source: 'curated',
  confidence: 'high',
  installable: true,
}

/** 与后端 `deprepair.offer()` 同形：受管目标可用、要先下载私有 Python */
const OFFER = {
  import_name: 'openpyxl',
  script: SCRIPT,
  requirement: REQUIREMENT,
  targets: [
    {
      kind: 'tavotto_managed',
      venv: '',
      python: '',
      modifies_user_environment: false,
      creates_environment: true,
      available: true,
      reason: '',
      private_python: PRIVATE_PYTHON,
    },
  ],
  rounds_remaining: 3,
  system_rejected: [],
  python_supported: { min: '3.10', max: '3.14' },
}

/** 可控的 SSE：每次连上发出队列里的下一批（没有就发空），`retry` 让浏览器很快再连 */
function serveEvents(page: Page) {
  const batches: string[] = []
  const handler = async (route: Route) => {
    const batch = batches.shift() ?? ''
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: `retry: 150\n\n${batch}`,
    })
  }
  return {
    install: () => page.route('**/api/events**', handler),
    push(...events: Record<string, unknown>[]) {
      batches.push(events.map((e) => `event: engine.dependency\ndata: ${JSON.stringify(e)}\n\n`).join(''))
    },
  }
}

const progress = (state: string, extra: Record<string, unknown> = {}) => ({
  plan_id: PLAN_ID,
  state,
  log: '',
  error: null,
  code: '',
  import_name: 'openpyxl',
  distribution: 'openpyxl',
  target_kind: 'tavotto_managed',
  script: SCRIPT,
  ...extra,
})

/** 整个框都在视口里、中心那一点点得到它 */
async function expectReachable(page: Page, loc: Locator, what: string) {
  await expect(loc, `${what} 应当可见`).toBeVisible()
  const b = (await loc.boundingBox())!
  const vp = page.viewportSize()!
  expect(b.x, `${what} 左边出了视口`).toBeGreaterThanOrEqual(0)
  expect(b.y, `${what} 上边出了视口`).toBeGreaterThanOrEqual(0)
  expect(b.x + b.width, `${what} 右边出了视口`).toBeLessThanOrEqual(vp.width + 0.5)
  expect(b.y + b.height, `${what} 下边出了视口`).toBeLessThanOrEqual(vp.height + 0.5)
  const hit = await loc.evaluate((el) => {
    const r = el.getBoundingClientRect()
    const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
    return !!top && (top === el || el.contains(top))
  })
  expect(hit, `${what} 中心被别的元素挡住了`).toBe(true)
}

/** 卡片里此刻真正画出来的文字块：一句话、一个按钮、折叠标题——按元素数，不按子串 */
const renderedBlocks = (card: Locator) =>
  card.evaluate((root) => {
    const out: string[] = []
    const walk = (el: Element) => {
      // 收起的 <details> 里的内容仍有布局框（content-visibility），只能问浏览器「画没画出来」
      if (!el.checkVisibility({ contentVisibilityAuto: true, opacityProperty: true, visibilityProperty: true })) return
      const own = [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? '').trim())
      if (own) out.push(`${el.tagName.toLowerCase()}:${(el.textContent ?? '').trim()}`)
      ;[...el.children].forEach(walk)
    }
    walk(root)
    return out
  })

test(
  '一键修复：缺包的脚本行只有一句话 + 一个主按钮，点一次、进度一行（第几步 / 下载 MB），装好后自动重跑出图',
  { tag: ['@feature:assets.dependency-one-click-repair'] },
  async ({ app, page }) => {
    const dir = path.join(os.tmpdir(), `tavotto-e2e-oneclick-${Date.now()}`)
    writeProject(dir)
    const a = await app({ figures: dir })
    await page.setViewportSize({ width: 1440, height: 900 })

    const events = serveEvents(page)
    await events.install()
    // 第一次试运行：缺包（带修复 offer）；之后的重跑放给真后端
    let probes = 0
    await page.route(/\/api\/registry\/probe(\?|$)/, async (route) => {
      probes += 1
      if (probes > 1) return route.fallback()
      await route.fulfill({
        json: {
          script: SCRIPT,
          entry: '__main__',
          stems: [],
          descriptors: [],
          tried: ['__main__'],
          registered: false,
          dropped_figures: 0,
          error: {
            code: 'missing_dependency',
            message: '缺少依赖包：openpyxl',
            params: { module: 'openpyxl' },
            dependency_repair: OFFER,
          },
        },
      })
    })
    const planBodies: unknown[] = []
    await page.route(/\/api\/engine\/dependency\/plan(\?|$)/, async (route) => {
      planBodies.push(route.request().postDataJSON())
      await route.fulfill({
        json: {
          plan: {
            ...REQUIREMENT,
            plan_id: PLAN_ID,
            target_kind: 'tavotto_managed',
            python: '',
            creates_environment: true,
            modifies_user_environment: false,
            network_required: true,
            expires_at: Date.now() / 1000 + 600,
            private_python: PRIVATE_PYTHON,
          },
        },
      })
    })
    const installBodies: unknown[] = []
    await page.route(/\/api\/engine\/dependency\/install(\?|$)/, async (route) => {
      installBodies.push(route.request().postDataJSON())
      await route.fulfill({ json: { started: true, ...progress('preparing') } })
    })

    await page.goto(a.baseURL)
    await expect(page.getByText(SCRIPT).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: `运行 ${SCRIPT} 并发现图` }).click()

    // ① 脚本行上一张卡：画出来的只有一句话、一个主按钮、「详情」折叠标题
    const card = page.locator('[data-script-dependency-repair] [data-one-click-repair="tavotto_managed"]')
    await expect(card).toBeVisible({ timeout: 30_000 })
    const button = card.locator('[data-one-click-repair-button]')
    await expectReachable(page, button, '一键修复按钮')
    expect(await renderedBlocks(card)).toEqual([
      'p:这个脚本还缺 openpyxl，点一下自动装好。',
      'button:一键修复',
      'summary:详情',
    ])
    // 下载大小、路径输入框都在折叠的「详情」里；卡片下面不再叠「可能依赖原来的 Python 环境」
    await expect(card.getByLabel('渲染解释器路径')).toBeHidden()
    await expect(card.locator('[data-one-click-cost]')).toBeHidden()
    await expect(page.getByText('可能依赖原来的 Python 环境')).toBeHidden()
    await card.locator('[data-repair-advanced] > summary').click()
    await expect(card.locator('[data-one-click-cost]')).toHaveText('需要联网，约下载 25 MB。')
    await card.locator('[data-repair-advanced] > summary').click()

    // ② 点一次：形成计划并直接开始（安装请求只带 plan_id），没有第二步确认
    await button.click()
    await expect.poll(() => installBodies.length).toBe(1)
    expect(installBodies[0]).toEqual({ plan_id: PLAN_ID })
    expect(planBodies).toEqual([{ module: 'openpyxl', script: SCRIPT, target: 'tavotto_managed' }])
    await expect(page.getByText('准备环境并继续')).toHaveCount(0)

    // ③ 下载私有 Python：进度只有一行，MB 来自进度里的字节数
    events.push(
      progress('downloading_python', {
        result: { download: { stage: 'downloading', done_bytes: 10 * MB, total_bytes: 25 * MB } },
      }),
    )
    const line = page.locator('[data-script-dependency-repair] [data-repair-line]')
    await expect(line).toHaveText('正在下载 Python… 10 / 25 MB', { timeout: 15_000 })
    await expect(page.locator('[data-script-dependency-repair] [data-repair-download]')).toHaveAttribute(
      'aria-valuenow',
      '40',
    )
    await expectReachable(page, line, '进度')
    const progressCard = page.locator('[data-script-dependency-repair] .shadow-card')
    // 进行中画出来的只有一行进度、「取消」与折叠的「详情」（四个阶段与日志在里面）
    expect(await renderedBlocks(progressCard)).toEqual(['p:正在下载 Python… 10 / 25 MB', 'button:取消', 'summary:详情'])

    // ④ 创建环境 → 安装：那一行跟着换；下载条随下载那一段结束消失
    events.push(progress('creating_env'))
    await expect(line).toHaveText('正在准备 Python 环境…（2/4）', { timeout: 15_000 })
    await expect(page.locator('[data-script-dependency-repair] [data-repair-download]')).toHaveCount(0)
    events.push(progress('installing'))
    await expect(line).toHaveText('正在安装 openpyxl…（3/4）', { timeout: 15_000 })

    // ⑤ 装好：那一行自动重跑（真后端、真 worker），图出来，修复卡收起
    events.push(progress('done', { result: { version: '3.1.5' } }))
    await expect(page.getByText('已发现 1 张图')).toBeVisible({ timeout: 120_000 })
    expect(probes).toBe(2)
    await expect(page.locator('[data-script-dependency-repair]')).toHaveCount(0)
  },
)
