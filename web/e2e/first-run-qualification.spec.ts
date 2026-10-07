import { spawnSync } from 'node:child_process'
import {
  copyFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  statSync,
} from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Locator, Page, Request } from '@playwright/test'
import { expect, openElementsTab, startApp, test } from './fixtures'
import { card, expectPageNotShifted, pill, primary, workPanelRect } from './prepCard'

/**
 * 真实首跑资格（T11：C23 / C24 / C27）——真后端、真 worker、真浏览器，经产品的正式入口。
 *
 * 待测项目起点**只有**脚本 / 数据 / 依赖声明（夹具 `script_only_first_run`，不带 `truth.json`）：没有图、没有注册表、
 * 没有执行日志。路径含中文与空格（`我的 项目`）。原生参考在**另一个**临时根里用同一份夹具跑一次（stdin 答菜单）。
 *
 * 判据的主语是脚本自己写在项目**父目录**的执行日志 `t11_exec_log.jsonl`（解释器 / argv / cwd / 菜单 / 选项 / 画出的 y），
 * 不是 toast、也不是 store 里的期望值。后端版的同一条旅程见
 * `tests/test_foundation_script_first_run.py::test_t11_s1_*`，这条是它的浏览器对应物。
 *
 * 步骤（T13b 引导卡）：打开项目 → 页面不下移、右下卡自动弹出（导入即扫描给出唯一待准备目标，弹出时不建会话）→ 开始准备 →
 * 卡里选运行目录（推荐项预选）→ 卡里填两个必填参数（没填齐时「继续」置灰、任何卡都不说可以运行）→ 继续 → 运行 → 卡里答 input →
 * 画好了 → 进入编辑（不重跑；卡片收起，取景适应窗口，元素树点得到）→ 改标题 → ⌘S 存进项目 → 停实例、同一数据 / 配置目录重开、
 * 刷新、从项目里打开该排版 → 双击进入图内编辑触发冷重放（冻结配置 + 回答转录，不再弹输入框）、编辑仍在 → 导出 PDF。
 *
 * 导出这一步走的是**界面**（导出对话框），不是 HTTP。
 */

const REPO = path.resolve(import.meta.dirname, '..', '..')
const FIXTURE = path.join(REPO, 'tests', 'fixtures', 'foundation', 'script_only_first_run')
const PROJECT_NAME = '我的 项目'
const SCRIPT = 'tools/spectrum.py'
const ARGV = ['--scale', '1.5', '--label', '峰 值 A']
const NEW_TITLE = 'T11 已编辑'
const LAYOUT = 'T11 首跑'
/** 产品把排版名里的空格规整成下划线（文件名、列表里的名字都是它） */
const LAYOUT_STORED = LAYOUT.replace(/ /g, '_')


interface ExecRecord {
  executable: string
  prefix: string
  argv: string[]
  cwd: string
  menu: string[]
  mode: string
  y: number[]
}

const execLog = (project: string): string => path.join(path.dirname(project), 't11_exec_log.jsonl')

const execs = (project: string): ExecRecord[] => {
  const log = execLog(project)
  return existsSync(log)
    ? readFileSync(log, 'utf-8')
        .split('\n')
        .filter(Boolean)
        .map((l) => JSON.parse(l) as ExecRecord)
    : []
}

/** 逐文件显式拷贝，排除 truth.json（真值不进待测项目） */
function copyFixture(dest: string): void {
  mkdirSync(dest, { recursive: true })
  const walk = (src: string, dst: string) => {
    mkdirSync(dst, { recursive: true })
    for (const e of readdirSync(src, { withFileTypes: true })) {
      if (e.name === 'truth.json' || e.name === '__pycache__') continue
      const s = path.join(src, e.name)
      const d = path.join(dst, e.name)
      if (e.isDirectory()) walk(s, d)
      else copyFileSync(s, d)
    }
  }
  walk(FIXTURE, dest)
}

/** 渲染响应（manifest）里标题元素的文字：响应是 ensure_ascii 的 JSON，必须解析后再比 */
function titleOf(body: string): string | undefined {
  try {
    const m = JSON.parse(body) as {
      manifest?: { elements?: { gid?: string; editable?: { prop?: string; value?: unknown }[] }[] }
    }
    const el = m.manifest?.elements?.find((e) => e.gid === 'axes_0.title')
    const v = el?.editable?.find((f) => f.prop === 'text')?.value
    return typeof v === 'string' ? v : undefined
  } catch {
    return undefined
  }
}

/** 展开元素树直到目标 treeitem 可见（树是异步填充的，以目标可见为准，带总限时） */
async function expandTreeUntil(page: Page, name: RegExp, timeoutMs = 60_000) {
  const target = page.getByRole('treeitem', { name }).first()
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if ((await target.count()) && (await target.isVisible())) return target
    const g = page.locator('[role="treeitem"][aria-expanded="false"]').first()
    if (await g.count()) await g.click()
    await page.waitForTimeout(150)
  }
  return target
}

/**
 * 真的点得到：框落在视口里，且框内的点经 `elementFromPoint` 命中的是它自己或它的后代——不是盖在上面的别的层，
 * 也不是被挤出可视区、裁掉之后命中的别处（`isVisible` 对被 overflow 裁掉的元素照样为真，不能当判据）
 */
async function expectHittable(target: Locator, what: string) {
  await expect
    .poll(
      () =>
        target.evaluate((el) => {
          const r = el.getBoundingClientRect()
          if (r.width < 2 || r.height < 2) return `empty ${Math.round(r.width)}x${Math.round(r.height)}`
          const x = r.left + Math.min(6, r.width / 2)
          const y = r.top + r.height / 2
          if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) return `offscreen y=${Math.round(y)}`
          const hit = document.elementFromPoint(x, y)
          if (hit && el.contains(hit)) return 'hit'
          const owner = hit?.closest('[data-prep-card]') ? ' (preparation card)' : ''
          return `covered by <${hit?.tagName.toLowerCase() ?? 'null'}>${owner}`
        }),
      { timeout: 15_000, message: `${what} 应当点得到` },
    )
    .toBe('hit')
}

function listFiles(root: string): string[] {
  const out: string[] = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name)
      if (e.isDirectory()) walk(p)
      else out.push(path.relative(root, p).split(path.sep).join('/'))
    }
  }
  walk(root)
  return out.sort()
}

const sameDir = (a: string, b: string) => realpathSync.native(a) === realpathSync.native(b)

test(
  '真实首跑资格：只有脚本与数据 → 引导卡里准备并运行 → 编辑 → 存进项目 → 重开冷重放 → 导出',
  { tag: '@feature:assets.run-script-figure' },
  async ({ app, page }) => {
  test.setTimeout(600_000)
  const python = process.env.TAVOTTO_PYTHON
  expect(python, '需要 TAVOTTO_PYTHON（装有 matplotlib / numpy 的解释器）').toBeTruthy()

  const refRoot = realpathSync.native(mkdtempSync(path.join(os.tmpdir(), 'tavotto-t11-ref-')))
  const testRoot = realpathSync.native(mkdtempSync(path.join(os.tmpdir(), 'tavotto-t11-test-')))
  const keepRoot = realpathSync.native(mkdtempSync(path.join(os.tmpdir(), 'tavotto-t11-keep-')))
  const mplDir = path.join(keepRoot, 'mpl')
  const ref = path.join(refRoot, PROJECT_NAME)
  const project = path.join(testRoot, PROJECT_NAME)
  let second: Awaited<ReturnType<typeof startApp>> | null = null
  try {
    // ---- 原生参考：另一个根、同名目录，stdin 答菜单（1 = smooth）
    copyFixture(ref)
    const native = spawnSync(python!, ['tools/spectrum.py', '--scale', '1.5', '--label', '峰 值 A'], {
      cwd: ref,
      input: '1\n',
      encoding: 'utf-8',
      // Windows 上的解释器没有 SystemRoot 起不来（随机数初始化失败），其余仍是最小环境
      env: {
        PATH: process.env.PATH ?? '',
        ...(process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {}),
        MPLBACKEND: 'Agg',
        MPLCONFIGDIR: mplDir,
      },
      timeout: 180_000,
    })
    expect(native.status, `原生参考失败：${native.stderr}`).toBe(0)
    const [nativeRun] = execs(ref)
    expect(execs(ref)).toHaveLength(1)
    expect(nativeRun.mode).toBe('smooth')
    expect(nativeRun.menu).toEqual(['raw', 'smooth'])
    expect(sameDir(nativeRun.cwd, ref)).toBe(true)

    // ---- 待测项目起点：只有三个文件；没有图、没有注册表、没有执行日志
    copyFixture(project)
    expect(listFiles(project)).toEqual(['data/values.csv', 'requirements.txt', 'tools/spectrum.py'])
    expect(sameDir(ref, project)).toBe(false)
    expect(existsSync(execLog(project))).toBe(false)
    expect(existsSync(path.join(project, 'tavotto_registry.json'))).toBe(false)

    // ---- 起实例，走正式入口
    const a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })

    // 记下编辑渲染请求（patches）与响应，供「改标题」一步取证
    const renders: { patches: { gid?: string; prop?: string; value?: unknown }[]; body: string }[] = []
    const pending: Promise<void>[] = []
    const onRequest = (req: Request) => {
      if (new URL(req.url()).pathname !== '/api/engine/render' || req.method() !== 'POST') return
      let patches: { gid?: string; prop?: string; value?: unknown }[] = []
      try {
        patches = (JSON.parse(req.postData() ?? '{}') as { patches?: typeof patches }).patches ?? []
      } catch {
        /* 非 JSON 的请求体不是编辑渲染 */
      }
      pending.push(
        req
          .response()
          .then((r) => r?.text())
          .then((body) => {
            renders.push({ patches, body: body ?? '' })
          })
          .catch(() => undefined),
      )
    }
    page.on('request', onRequest)

    // 入口：导入即扫描发现了唯一待准备的目标 → 右下引导卡自动弹出（页面不下移；弹出时不建会话）。夹具的 `savefig("spectrum.pdf")`
    // 是字面量，打开项目时静态扫描会先把图名写进注册表（T00 裁决的既有边界）；T11 起扫描只把「登记了且真有可编辑的图」
    // 算作已连接，所以这里仍是唯一待准备的目标；素材库脚本行同一判据（T13b）：不说「已关联」。页面一出现就点（T11 复核过：
    // 那一刻点下去也不丢）。
    const sessions: string[] = []
    page.on('request', (r) => {
      if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/engine/preparation-sessions') sessions.push(r.url())
    })
    await page.goto(a.baseURL)
    await expect(page.locator('[data-work-panel]')).toBeVisible({ timeout: 30_000 })
    const layoutBefore = await workPanelRect(page)
    await expect(card(page)).toHaveAttribute('data-prep-state', 'discover', { timeout: 30_000 })
    const scanned = async () =>
      (await (await page.request.get(`${a.baseURL}/api/project/scan`)).json()) as {
        state?: string
        outcome?: { kind: string }
        default_target?: string
      }
    await expect.poll(async () => (await scanned()).outcome?.kind, { timeout: 60_000 }).toBe('target_found')
    expect((await scanned()).default_target).toBe(SCRIPT)
    await expect(card(page).locator('[data-prep-line]')).toHaveText(`发现绘图脚本 ${SCRIPT}`)
    // 页面不下移：卡片出现前后工作面板的位置与高度不变，顶栏贴顶、没有横条
    expect(await workPanelRect(page)).toEqual(layoutBefore)
    await expectPageNotShifted(page)
    expect(sessions).toEqual([])
    // 素材库脚本行：从没运行过，不说「已关联」
    const row = page.locator(`[data-script-row="${SCRIPT}"]`)
    if (await row.count()) await expect(row).not.toContainText('已关联')
    await primary(page).click()

    // 数据只在项目根找得到 → 先在卡里选运行目录（推荐项预选；确认只重新检查，按钮不说「运行」）
    await expect(card(page)).toHaveAttribute('data-prep-state', 'workdir', { timeout: 60_000 })
    expect(execs(project)).toHaveLength(0)
    await expect(card(page).locator('[data-workdir-option="project_root"] input')).toBeChecked()
    await expect(primary(page)).toHaveText('用项目根目录')
    await primary(page).click()

    // 两个必填参数（argparse 的 --scale / --label）还没填：参数卡、只摆必填项、「继续」置灰，任何卡都不说可以运行
    await expect(card(page)).toHaveAttribute('data-prep-state', 'args', { timeout: 60_000 })
    await expect(card(page).locator('[data-prep-line]')).toHaveText('还差 2 个参数')
    await expect(primary(page)).toBeDisabled()
    await expect(card(page)).not.toContainText('可以运行')
    expect(execs(project)).toHaveLength(0)
    const required = card(page).locator(`[data-testid="argv-form-required-${SCRIPT}"]`)
    await required.getByRole('textbox', { name: '--scale 的第 1 个值' }).fill(ARGV[1])
    await expect(card(page).locator('[data-prep-line]')).toHaveText('还差 1 个参数')
    await expect(primary(page)).toBeDisabled()
    await required.getByRole('textbox', { name: '--label 的第 1 个值' }).fill(ARGV[3])
    await expect(card(page)).toHaveAttribute('data-prep-state', 'args_changed')
    await expect(primary(page)).toBeEnabled()
    await primary(page).click() // 继续 = 按新参数重新检查
    await expect(card(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: 60_000 })
    await expect(card(page).locator('[data-prep-line]')).toHaveText('可以运行了')
    await expect(card(page).locator('[data-prep-ready-line]')).toContainText('--scale 1.5 --label "峰 值 A"')
    expect(execs(project)).toHaveLength(0)
    await expectPageNotShifted(page)

    // 运行 → 运行时 input 在卡里答（原对话框不出现）
    await primary(page).click()
    await expect(card(page)).toHaveAttribute('data-prep-state', 'input', { timeout: 120_000 })
    await expect(page.locator('[data-dialog="script-input"]')).toHaveCount(0)
    const form = card(page).locator('[data-prep-input]')
    await expect(form.locator('[data-script-input-output]')).toContainText('1) smooth')
    await form.locator('[data-script-input-answer]').fill('1')
    await primary(page).click() // 回答
    await expect(card(page)).toHaveAttribute('data-prep-state', 'completed', { timeout: 120_000 })
    await expect(card(page).locator('[data-prep-line]')).toHaveText('画好了 1 张图')
    await expectPageNotShifted(page)

    // 进入编辑：同一次捕获，不重跑；卡片随即收起、不留角标（T13b），左栏轨道、编辑区、元素树都点得到；
    // 取景适应窗口（不是停在 25% 只露一角）
    await primary(page).click()
    await expect(card(page)).toHaveCount(0)
    await expect(pill(page)).toHaveCount(0)
    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 120_000 })
    await expectHittable(page.locator('[data-rail="elements"]'), '左栏「元素」轨道')
    await expectHittable(page.locator('[data-work-panel]'), '编辑区')
    await expectPageNotShifted(page)
    const framing = await page.evaluate(() => {
      const stage = document.querySelector('[data-canvas-stage]')!.getBoundingClientRect()
      const obj = document.querySelector('[data-canvas-stage] [data-object-id]')!.getBoundingClientRect()
      return { w: obj.width / stage.width, h: obj.height / stage.height }
    })
    expect(Math.max(framing.w, framing.h), `进入编辑后图应当撑开画布（取景 ${JSON.stringify(framing)}）`).toBeGreaterThan(0.45)

    // ---- 真值（一）：恰好 1 次、argv / cwd / mode / y 与原生参考一致
    await page.waitForTimeout(1500)
    const first = execs(project)
    expect(first).toHaveLength(1)
    const [run1] = first
    expect(run1.argv).toEqual(ARGV)
    expect(sameDir(run1.cwd, project)).toBe(true)
    expect(run1.mode).toBe('smooth')
    expect(run1.menu).toEqual(nativeRun.menu)
    expect(run1.y).toEqual(nativeRun.y)

    // ---- 改一个真实元素：标题（元素树 → 标题 → 检查器里的文字字段）
    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 120_000 })
    await openElementsTab(page)
    const titleItem = await expandTreeUntil(page, /^标题/)
    await expectHittable(titleItem, '元素树里的「标题」')
    await titleItem.click()
    const inspector = page.getByLabel('右侧面板', { exact: true })
    const titleField = inspector.locator('[data-prop="text"] textarea')
    await expect(titleField).toBeVisible()
    await expect(titleField).toHaveValue('峰 值 A [smooth]') // 脚本自己的标题：label + 模式
    await titleField.fill(NEW_TITLE)
    await titleField.press('Enter')
    await expect(titleField).toHaveValue(NEW_TITLE)
    await expect(inspector.getByText('1 项已修改')).toBeVisible({ timeout: 30_000 })
    // 编辑渲染请求里带着这条 patch，响应里标题文字就是新值
    await expect
      .poll(
        async () => {
          await Promise.all(pending)
          return renders.some(
            (r) =>
              r.patches.some((p) => p.prop === 'text' && p.value === NEW_TITLE) && titleOf(r.body) === NEW_TITLE,
          )
        },
        { timeout: 60_000, message: '应当有一次编辑渲染带着 text=T11 已编辑，且响应里标题就是它' },
      )
      .toBe(true)
    expect(execs(project)).toHaveLength(1) // 编辑是热态渲染，不重跑脚本

    // ---- ⌘S 存进项目：回到画布，命名「T11 首跑」
    await page.locator('[data-context-back]').first().click()
    await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1, { timeout: 30_000 })
    await page.keyboard.press('ControlOrMeta+s')
    const dialog = page.getByRole('dialog', { name: '存进项目' })
    await expect(dialog).toBeVisible()
    await dialog.locator('#layout-save-name').fill(LAYOUT)
    await dialog.getByRole('button', { name: '存进项目' }).click()
    await expect(dialog).toBeHidden()
    const layoutFile = path.join(project, 'tavottofile', `${LAYOUT_STORED}.json`)
    await expect(page.locator('[data-save-destination]')).toHaveAttribute('title', new RegExp(`tavottofile/${LAYOUT_STORED}\\.json`))
    await expect.poll(() => existsSync(layoutFile)).toBe(true)
    interface SavedPanel {
      type: string
      fileId?: string
      fileKind?: string
      overrides?: { prop?: string; value?: unknown }[]
    }
    const saved = JSON.parse(readFileSync(layoutFile, 'utf-8')) as { canvases: { objects: SavedPanel[] }[] }
    const runtimePanels = saved.canvases
      .flatMap((c) => c.objects)
      .filter((o) => o.type === 'panel' && o.fileKind === 'runtime')
    expect(runtimePanels).toHaveLength(1)
    expect(runtimePanels[0].overrides?.some((o) => o.prop === 'text' && o.value === NEW_TITLE)).toBe(true)
    expect(execs(project)).toHaveLength(1)

    // ---- 停实例；同一份数据 / 配置目录重开（fixtures 的 stop 会删整个临时根，先把它们存下来）
    const port = a.port
    const keptData = path.join(keepRoot, 'data')
    const keptConfig = path.join(keepRoot, 'config')
    cpSync(a.dataDir, keptData, { recursive: true })
    const srcConfig = path.join(path.dirname(a.dataDir), 'config')
    if (existsSync(srcConfig)) cpSync(srcConfig, keptConfig, { recursive: true })
    else mkdirSync(keptConfig, { recursive: true })
    await a.stop()
    second = await startApp({
      figures: project,
      port,
      env: { TAVOTTO_DATA_DIR: keptData, TAVOTTO_CONFIG_DIR: keptConfig },
    })

    // ---- 刷新 → 从「项目里的排版」打开 → 编辑仍在
    const before = execs(project).length
    renders.length = 0
    await page.reload()
    await expect(page.locator('[data-canvas-stage]')).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: /^(文档|排版)：/ }).click()
    await page.getByRole('menuitem', { name: /^打开(文档|排版)/ }).click()
    const openDialog = page.getByRole('dialog')
    await openDialog.getByRole('button', { name: new RegExp(LAYOUT_STORED) }).click()
    await expect(openDialog).toBeHidden()
    await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1, { timeout: 30_000 })
    // 图是渲染出来的（不是 DOM 文字）。重开后画布先用缓存预览（磁盘自动保存 + 烘焙过的编辑），这时脚本还没被重放；
    // 双击进入图内编辑才会走权威渲染——冷 worker 按冻结配置 + 回答转录重放脚本
    await page.locator('[data-canvas-stage] [data-object-id]').first().dblclick()
    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 180_000 })
    await expect
      .poll(
        async () => {
          await Promise.all(pending)
          return renders.some(
            (r) =>
              r.patches.some((p) => p.prop === 'text' && p.value === NEW_TITLE) && titleOf(r.body) === NEW_TITLE,
          )
        },
        { timeout: 180_000, message: '重开后应当有一次渲染带着保存的 text 覆盖，且标题是 T11 已编辑' },
      )
      .toBe(true)
    // 界面上读回：元素树里选中标题，检查器的文字字段就是编辑后的值
    await openElementsTab(page)
    await (await expandTreeUntil(page, /^标题/)).click()
    await expect(
      page.getByLabel('右侧面板', { exact: true }).locator('[data-prop="text"] textarea'),
    ).toHaveValue(NEW_TITLE)
    // 冷重放：冻结配置 + 那一次执行的回答转录；没有再弹输入框
    const replays = execs(project).slice(before)
    expect(replays.length).toBeGreaterThanOrEqual(1)
    for (const r of replays) {
      expect(r.argv).toEqual(ARGV)
      expect(r.mode).toBe('smooth')
      expect(r.y).toEqual(nativeRun.y)
      expect(sameDir(r.cwd, project)).toBe(true)
    }
    await expect(page.locator('[data-dialog="script-input"]')).toHaveCount(0)
    await expect(page.locator('[data-script-input-answer]')).toHaveCount(0)
    await expect(card(page)).toHaveCount(0)

    // ---- 导出 PDF（界面）：文件真的落盘、%PDF- 开头
    await page.locator('[data-context-back]').first().click()
    await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1, { timeout: 30_000 })
    await page.keyboard.press('ControlOrMeta+e')
    const exp = page.getByRole('dialog')
    await expect(exp).toBeVisible()
    for (const fmt of ['PNG', 'TIFF', 'EPS']) {
      const box = exp.getByRole('checkbox', { name: fmt, exact: true })
      if ((await box.count()) && (await box.isChecked())) await box.uncheck()
    }
    const pdfBox = exp.getByRole('checkbox', { name: 'PDF', exact: true })
    if (!(await pdfBox.isChecked())) await pdfBox.check()
    const confirm = exp.locator('input[data-export-confirm]')
    if (await confirm.count()) await confirm.check()
    const started = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/export/start' && r.request().method() === 'POST',
    )
    const t0 = Date.now() - 1000
    await exp.getByRole('button', { name: /开始导出/ }).click()
    const job = (await (await started).json()) as { export_dir?: string }
    expect(job.export_dir, 'start 回包里应当有导出目录').toBeTruthy()
    await expect(exp.getByText(/已保存到/)).toBeVisible({ timeout: 240_000 })
    const exportDir = job.export_dir!
    const pdfs = () =>
      (existsSync(exportDir) ? readdirSync(exportDir) : []).filter(
        (f) => f.toLowerCase().endsWith('.pdf') && statSync(path.join(exportDir, f)).mtimeMs >= t0,
      )
    await expect.poll(() => pdfs().length, { timeout: 60_000 }).toBeGreaterThanOrEqual(1)
    const bytes = readFileSync(path.join(exportDir, pdfs()[0]))
    expect(bytes.subarray(0, 5).toString('latin1')).toBe('%PDF-')
    expect(bytes.length).toBeGreaterThan(900)

    // 导出若又冷重放，也只能是同一份冻结配置
    for (const r of execs(project).slice(before)) {
      expect(r.argv).toEqual(ARGV)
      expect(r.mode).toBe('smooth')
      expect(r.y).toEqual(nativeRun.y)
    }
    // 起点三个文件逐字节未动
    expect(readFileSync(path.join(project, 'tools', 'spectrum.py')).equals(readFileSync(path.join(FIXTURE, 'tools', 'spectrum.py')))).toBe(true)
  } finally {
    await second?.stop()
    for (const d of [refRoot, testRoot, keepRoot]) rmSync(d, { recursive: true, force: true })
  }
})
