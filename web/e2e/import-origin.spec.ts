import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test, type RunningApp } from './fixtures'
import { card, openDetails, pill, primary } from './prepCard'

/**
 * Import Origin Resolver 的最终验收（PR7，计划 P7.6）——真后端、真 worker、真浏览器，经产品的正式入口（右下准备引导卡）。
 *
 * 待测项目就是计划里的验收场景（`tests/support/e2e_origin_world.py`）：
 *
 *     我的 论文/paper/{scripts/{figure1.py,figure2.py,utils.py}, data/results.csv, requirements.txt[, .venv/]}
 *
 * `figure1.py`：`import numpy as np; import matplotlib.pyplot as plt; import docx; from utils import load_results`
 * （`unknown` / `orphan` 场景把 `docx` 换成那个来源有问题的库）。路径含中文与空格。
 *
 * 判据的主语是脚本自己写在项目**之外**的执行日志（解释器 / prefix / cwd / 库从哪个文件来 / 画出的 y）与磁盘上的真实状态
 * （受管环境目录、`.dist-info`、宿主解释器仍 import 不到包），**不是**界面上的文案，也不是 store 里的期望值。
 *
 *   ① 复用已有环境：导入即扫描（零执行）→ 建议（只建议，不切换）→ 明确的「检查候选」（会执行，如实标注）→ 运行时用项目自带的
 *      `.venv` 跑 → 捕获 Figure → 进入编辑；全程不装任何东西；
 *   ② 受管环境安装：缺 `python-docx` → 卡片一句话 + 一个主按钮（装什么 / 装到哪一行）→ 一次授权 → 本地离线 wheelhouse 里
 *      真 pip 装进新建的受管环境 → worker 验证 → 重跑 → 捕获；宿主解释器与 bundled runtime 一个字节没动；
 *   ③ 来源无法验证：卡片说「尚无法确定」、不装、详情里有人话原因；主按钮是「仍然运行」（不是安装）；
 *   ④ 需要确认：预填 → 用户确认包名 → 确认页标「用户指定、未经核实」→ 装 → 装后核验（先填错包名：核验不过、不激活，再改对）。
 *
 * 全程不联网（`PIP_NO_INDEX` + 本地 wheelhouse）；受管环境在离线机器上建出来靠 `tests/support/e2e_offline_app.py`
 * （基础栈换空表、宿主 site-packages 用 `.pth` 挂进去，放宽了什么见 `support/offline_world.py`）。
 */

test.skip(
  process.platform === 'win32',
  '离线入口与夹具世界是 POSIX 的 Python 脚本；本条在 CI 的 posix-e2e 腿上执行（windows-exe-smoke 跑的是打包产物，没有 tests/ 目录）',
)

const REPO = path.resolve(import.meta.dirname, '..', '..')
const PYTHON = process.env.TAVOTTO_PYTHON ?? path.join(REPO, '.venv', 'bin', 'python')
const SRC = path.join(REPO, 'src')
const LONG = 240_000

interface World {
  root: string
  project: string
  log: string
  wheelhouse: string
  extra_path: string
  venv_python: string | null
}

interface ExecRecord {
  executable: string
  prefix: string
  cwd: string
  docx?: string
  lib?: string
  lib_value?: number
  y: number[]
}

type Scenario = 'existing' | 'missing' | 'unknown' | 'orphan'

function buildWorld(root: string, scenario: Scenario): World {
  const r = spawnSync(PYTHON, [path.join(REPO, 'tests', 'support', 'e2e_origin_world.py'), root, scenario], {
    encoding: 'utf-8',
    env: { ...process.env, PYTHONPATH: [SRC, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
  })
  if (r.status !== 0) throw new Error(`造夹具世界失败（${scenario}）：\n${r.stdout}\n${r.stderr}`)
  return JSON.parse(r.stdout) as World
}

const execs = (w: World): ExecRecord[] =>
  existsSync(w.log)
    ? readFileSync(w.log, 'utf-8')
        .split('\n')
        .filter(Boolean)
        .map((l) => JSON.parse(l) as ExecRecord)
    : []

/** 两条路径是不是同一个位置（macOS 的 /var ↔ /private/var）：按真实路径比，不比字符串 */
const real = (p: string): string => {
  try {
    return realpathSync(p)
  } catch {
    // 还不存在的路径（比如没建出来的受管环境目录）：解析最近的存在的祖先，再接回剩下的
    const parent = path.dirname(p)
    return parent === p ? p : path.join(real(parent), path.basename(p))
  }
}
const inside = (p: string, dir: string) => {
  const r = real(dir)
  const q = real(p)
  return q === r || q.startsWith(r + path.sep)
}

function installEnv(w: World, extra: Record<string, string> = {}): Record<string, string> {
  return { PIP_FIND_LINKS: w.wheelhouse, PIP_NO_INDEX: '1', PIP_DISABLE_PIP_VERSION_CHECK: '1', ...extra }
}

/** 后端的同一份事实（不看界面文案）：环境 */
async function environment(page: Page, a: RunningApp) {
  const res = await page.request.get(`${a.baseURL}/api/engine/environment`)
  expect(res.ok()).toBe(true)
  return (await res.json()) as {
    project: {
      source: string
      consent: string
      managed: { exists: boolean; installed: unknown[]; generations: unknown[] }
      recommendation: {
        candidates: { id: string; name: string; status: string; checked: boolean; python_relative: string | null }[]
        decision: { mode: string; needs_decision: boolean }
        check: { executes_user_code: boolean; side_effect_free: boolean }
      }
    }
  }
}

/** 准备会话报告：从页面自己收到的响应里读（零额外请求，读的就是界面画的那一份） */
function collectReports(page: Page) {
  const bodies: string[] = []
  page.on('response', (r) => {
    if (r.url().includes('/api/engine/preparation-sessions')) {
      void r.text().then((t) => bodies.push(t)).catch(() => undefined)
    }
  })
  return () =>
    bodies
      .map((t) => {
        try {
          return JSON.parse(t) as Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any
        } catch {
          return null
        }
      })
      .filter((j): j is Record<string, any> => !!j && Array.isArray(j.checks)) // eslint-disable-line @typescript-eslint/no-explicit-any
}

/** 打开项目 → 卡自动弹出（导入即扫描、零执行）→ 选 figure1 → 开始准备 → 运行目录 = 项目根（脚本在 scripts/ 下读 data/results.csv） */
async function startToWorkdir(page: Page, a: RunningApp) {
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto(a.baseURL)
  await expect(page.locator('[data-work-panel]')).toBeVisible({ timeout: 60_000 })
  await expect(card(page)).toHaveAttribute('data-prep-state', 'choose', { timeout: 90_000 })
  await card(page).locator('[data-scan-target="scripts/figure1.py"]').click()
  await primary(page).click()
  await expect(card(page)).toHaveAttribute('data-prep-state', 'workdir', { timeout: 120_000 })
  await expect(card(page).locator('[data-workdir-option="project_root"] input')).toBeChecked()
  await primary(page).click()
}

async function cleanup(a: RunningApp | undefined, root: string) {
  // 先等应用真正退出再删目录：worker 还握着项目目录时 rmSync 会 EBUSY；清理报错不许盖住主失败
  await a?.stop().catch(() => undefined)
  try {
    rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  } catch (err) {
    console.warn(`cleanup failed: ${String(err)}`)
  }
}

/** 受管环境里装进了哪些发行包（读磁盘上的 `.dist-info`，不问后端） */
function managedDistInfos(a: RunningApp): string[] {
  const out: string[] = []
  const envs = path.join(a.dataDir, 'environments')
  if (!existsSync(envs)) return out
  const walk = (dir: string, depth: number) => {
    if (depth > 7) return
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name)
      if (e.isDirectory()) {
        if (e.name.endsWith('.dist-info')) out.push(e.name)
        else walk(p, depth + 1)
      }
    }
  }
  walk(envs, 0)
  return out
}

/** 宿主（内置 / 当前进程）解释器此刻还 import 得到这个包吗：不许——Tavotto 从不往那里装 */
function hostCanImport(module: string): boolean {
  return spawnSync(PYTHON, ['-I', '-c', `import ${module}`], { encoding: 'utf-8' }).status === 0
}

test(
  '① 复用已有环境：扫描（零执行）→ 只建议 → 检查候选（会执行，如实标注）→ 用项目自带的 .venv 跑 → 捕获 Figure → 进入编辑，全程不装',
  async ({ app, page }) => {
    test.setTimeout(600_000)
    const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-origin-existing-'))
    let a: RunningApp | undefined
    try {
      const w = buildWorld(root, 'existing')
      expect(w.venv_python).toBeTruthy()
      a = await app({ figures: w.project })
      const reports = collectReports(page)

      // —— 建议：后端的同一份事实。.venv 是候选（项目线索），还没检查过；默认是检测模式，没有任何「要用户先决定」
      const before = await environment(page, a)
      const rec = before.project.recommendation
      const venv = rec.candidates.find((c) => c.python_relative === '.venv/bin/python')
      expect(venv, '候选里应当有项目自带的 .venv').toBeTruthy()
      expect(venv!.status).toBe('unchecked')
      expect(venv!.checked).toBe(false)
      expect(rec.decision.mode).toBe('detect')
      expect(rec.decision.needs_decision).toBe(false)
      expect(before.project.consent).toBe('none')
      expect(before.project.source).not.toBe('project_venv') // 只建议，不切换
      // 检查要起候选解释器、会执行用户包：接口自己如实标注
      expect(rec.check.executes_user_code).toBe(true)
      expect(rec.check.side_effect_free).toBe(false)

      // —— 检查候选（用脚本真实的 import 集合判断「装齐了没有」）：只起被点名的那个
      const checkedRes = await page.request.post(`${a.baseURL}/api/engine/environment/check`, {
        data: { candidates: [venv!.id], coverage: true, script: 'scripts/figure1.py' },
      })
      expect(checkedRes.ok()).toBe(true)
      const checked = await checkedRes.json()
      expect(checked.checked).toEqual([venv!.id])
      expect(checked.coverage.modules.sort()).toEqual(['docx', 'matplotlib', 'numpy']) // utils 是本地模块，不在里面
      expect(checked.executed.candidate_interpreters).toBe(1)
      expect(checked.executed.side_effect_free).toBe(false)
      const after = checked.recommendation.candidates.find((c: { id: string }) => c.id === venv!.id)
      expect(after.status).toBe('healthy')
      expect(after.coverage.state).toBe('covered')
      expect(after.coverage.executed_user_code).toBe(true)
      expect(after.coverage.modules).toEqual({ docx: 'found', matplotlib: 'found', numpy: 'found' })
      // 检查 ≠ 采用：项目设置里仍没有决定，也没有任何一次执行脚本
      expect((await environment(page, a)).project.consent).toBe('none')
      expect(execs(w)).toEqual([])

      // —— 经正式入口跑：卡里选目录 → 可以运行 → 打开并运行（V2 PR4：画好一张图就自动进入编辑）
      await startToWorkdir(page, a)
      await expect(card(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: 120_000 })
      await expect(card(page)).toHaveAttribute('data-origin-view', 'ready')
      expect(execs(w)).toEqual([]) // 准备阶段不执行用户脚本
      await expect(primary(page)).toHaveText('打开并运行')
      await primary(page).click()
      await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: LONG })
      await expect(card(page)).toHaveCount(0)

      // —— 真值：恰好执行一次，用的是项目 .venv（不是宿主）、cwd 是项目根、数据是 data/results.csv、docx 来自 .venv 自己的 site-packages
      const venvRoot = path.join(w.project, '.venv')
      const runs = execs(w)
      expect(runs).toHaveLength(1)
      expect(real(runs[0].prefix)).toBe(real(venvRoot))
      expect(inside(runs[0].docx!, venvRoot)).toBe(true)
      expect(real(runs[0].cwd)).toBe(real(w.project))
      expect(runs[0].y).toEqual([2, 3.5, 1.25, 4])
      // 后端记下的环境来源：项目 venv；没有建任何受管环境、没有装任何东西
      const final = await environment(page, a)
      expect(final.project.source).toBe('project_venv')
      expect(final.project.managed.exists).toBe(false)
      expect(managedDistInfos(a)).toEqual([])
      // 会话报告里依赖那一项：没有要装的、没有来源问题
      const dep = reports()
        .at(-1)!
        .checks.find((c: { id: string }) => c.id === 'dependencies')
      expect(dep.detail?.origins).toBeUndefined()

      // —— 进入编辑是自动的（用这次捕获的图，不重跑）；卡片收起、不留角标
      await expect(pill(page)).toHaveCount(0)
      await page.waitForTimeout(1500)
      expect(execs(w)).toHaveLength(1)
    } finally {
      await cleanup(a, root)
    }
  },
)

test(
  '② 受管环境安装：缺 python-docx → 一句话 + 一个主按钮（装什么 / 装到哪）→ 一次授权 → 离线 wheelhouse 里真 pip 装进新环境 → worker 验证 → 重跑 → 捕获',
  async ({ app, page }) => {
    test.setTimeout(600_000)
    const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-origin-missing-'))
    let a: RunningApp | undefined
    try {
      const w = buildWorld(root, 'missing')
      expect(hostCanImport('docx'), '前提：宿主解释器里没有 docx（否则这条什么也证明不了）').toBe(false)
      a = await app({ offline: true, figures: w.project, env: installEnv(w) })
      const reports = collectReports(page)

      await startToWorkdir(page, a)
      // 缺的是 python-docx（curated 映射：import docx → python-docx）：卡片是「可以自动修复」——一句话 + 一个主按钮
      await expect(card(page)).toHaveAttribute('data-prep-state', 'deps', { timeout: 120_000 })
      await expect(card(page)).toHaveAttribute('data-origin-view', 'auto_fix')
      await expect(primary(page)).toHaveCount(1)
      await expect(card(page).locator('[data-prep-install]')).toContainText('python-docx')
      // 授权前什么都没发生：没有受管环境、没有执行、宿主没动
      expect((await environment(page, a)).project.managed.exists).toBe(false)
      expect(execs(w)).toEqual([])
      expect(managedDistInfos(a)).toEqual([])

      // 授权要绑定的「实际影响」（ADR 0115）：装什么、装到哪、新建环境、不改用户环境；`utils` 是本地模块，永远不在里面
      const action = reports()
        .at(-1)!
        .actions.find((x: { kind: string }) => x.kind === 'prepare_dependencies')
      expect(action, '报告里应当有后端给的 prepare_dependencies 动作').toBeTruthy()
      const impact = action.impact
      expect(impact.installs.some((r: string) => r.toLowerCase().startsWith('python-docx'))).toBe(true)
      expect(impact.installs.join(' ')).not.toMatch(/utils/i)
      expect(impact.creates_environment).toBe(true)
      expect(impact.modifies_user_environment).toBe(false)

      // 一次授权：点「打开并运行」（V2 PR4：报告里有 run_when_ready、这次只装进 Tavotto 自己的环境，它取代「安装」）——
      // 授权合同（只进受管环境、只装项目声明的）就是这一次点击的范围：装好 → 按新环境重新检查 → 执行恰好一次 → 画好一张图就自动进入编辑，没有逐包弹窗
      await expect(primary(page)).toHaveText('打开并运行')
      await primary(page).click()
      await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: LONG })
      await expect(card(page)).toHaveCount(0)

      // 真值：恰好执行一次，跑在 Tavotto 新建的受管环境里；docx 是受管环境里装的；宿主解释器仍然 import 不到
      const runs = execs(w)
      expect(runs).toHaveLength(1)
      const envRoot = path.join(a.dataDir, 'environments')
      expect(inside(runs[0].executable, envRoot) || inside(runs[0].prefix, envRoot)).toBe(true)
      expect(inside(runs[0].docx!, runs[0].prefix)).toBe(true)
      expect(real(runs[0].cwd)).toBe(real(w.project))
      expect(runs[0].y).toEqual([2, 3.5, 1.25, 4])
      expect(managedDistInfos(a).some((n) => n.toLowerCase().startsWith('python_docx-1.1.2'))).toBe(true)
      expect(hostCanImport('docx')).toBe(false)
      const env = await environment(page, a)
      expect(env.project.managed.exists).toBe(true)
      expect(env.project.source).toBe('managed_project_env')
      // 项目里没有被写进任何东西：没有新建 .venv，requirements.txt 还是用户写的那一份
      expect(existsSync(path.join(w.project, '.venv'))).toBe(false)
      expect(readFileSync(path.join(w.project, 'requirements.txt'), 'utf-8')).toBe('python-docx\n')
    } finally {
      await cleanup(a, root)
    }
  },
)

test(
  '③ 来源无法验证：卡片说「尚无法确定」、不装、详情里是人话原因，主按钮是「仍然运行」而不是安装',
  async ({ app, page }) => {
    test.setTimeout(600_000)
    const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-origin-orphan-'))
    let a: RunningApp | undefined
    try {
      const w = buildWorld(root, 'orphan')
      // orphan_lib 在 PYTHONPATH 的一个目录里，**没有任何发行包元数据**：它能被 import，但没人说得清它是谁的、该从哪装
      a = await app({
        offline: true,
        figures: w.project,
        env: installEnv(w, { PYTHONPATH: [SRC, w.extra_path].join(path.delimiter) }),
      })
      const reports = collectReports(page)

      await startToWorkdir(page, a)
      await expect(card(page)).toHaveAttribute('data-prep-state', 'origin_unknown', { timeout: 120_000 })
      await expect(card(page)).toHaveAttribute('data-origin-view', 'undetermined')
      await expect(card(page).locator('[data-prep-line]')).toHaveText('有 1 个库的来源还没法确定')
      // 一句话 + 一个主按钮；主按钮是后端报告里的「运行」改口的「仍然运行」，不是安装
      await expect(primary(page)).toHaveCount(1)
      await expect(primary(page)).toHaveText('仍然运行')
      await expect(card(page).locator('[data-prep-install]')).toHaveCount(0)
      // 解释在默认折叠的详情里：按原因码一行，人话；「Tavotto 不会替你装」
      const details = await openDetails(page)
      const reason = details.locator('[data-origin-reasons] li')
      await expect(reason).toHaveCount(1)
      await expect(reason).toHaveAttribute('data-origin-reason', 'unverified')
      await expect(details.locator('[data-origin-reasons]')).toContainText('不会替你装')
      await expect(reason).toContainText('查不清它是从哪来的')
      // 后端的同一份事实：只有计数与闭集原因码，没有名字
      const dep = reports()
        .at(-1)!
        .checks.find((c: { id: string }) => c.id === 'dependencies')
      expect(dep.detail.origins.count).toBe(1)
      expect(dep.detail.origins.by_reason).toEqual({ unverified: 1 })
      expect(dep.detail.origins.confirmable).toBeUndefined() // 没有预填，也就没有「确认包名」
      expect(JSON.stringify(dep)).not.toContain('orphan_lib')
      // 没有装任何东西、没有执行、没有建环境
      expect((await environment(page, a)).project.managed.exists).toBe(false)
      expect(managedDistInfos(a)).toEqual([])
      expect(execs(w)).toEqual([])

      // 后续选项：仍然运行（用户自己拿主意）。它不装、不建环境；跑在宿主解释器上，orphan_lib 来自 PYTHONPATH 那个目录
      await primary(page).click()
      await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: LONG })
      const runs = execs(w)
      expect(runs).toHaveLength(1)
      expect(inside(runs[0].lib!, w.extra_path)).toBe(true)
      expect(runs[0].lib_value).toBe(7)
      expect(inside(runs[0].executable, path.join(a.dataDir, 'environments'))).toBe(false)
      expect((await environment(page, a)).project.managed.exists).toBe(false)
      expect(managedDistInfos(a)).toEqual([])
    } finally {
      await cleanup(a, root)
    }
  },
)

test(
  '④ 需要确认：预填 → 用户确认包名 → 确认页标「用户指定、未经核实」→ 装 → 装后核验（先填错包名：核验不过、不激活；改对后装好、运行）',
  async ({ app, page }) => {
    test.setTimeout(600_000)
    const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-origin-confirm-'))
    let a: RunningApp | undefined
    try {
      const w = buildWorld(root, 'unknown')
      a = await app({ offline: true, figures: w.project, env: installEnv(w) })
      const reports = collectReports(page)

      await startToWorkdir(page, a)
      // 表外的名字：不猜 PyPI 名、不自动装——只给「需要确认」，一句话 + 一个主按钮
      await expect(card(page)).toHaveAttribute('data-prep-state', 'origin_confirm', { timeout: 120_000 })
      await expect(card(page)).toHaveAttribute('data-origin-view', 'needs_confirm')
      await expect(card(page).locator('[data-prep-line]')).toHaveText('有 1 个库要你确认包名')
      await expect(primary(page)).toHaveText('确认包名')
      await expect(primary(page)).toHaveCount(1)
      expect((await environment(page, a)).project.managed.exists).toBe(false)
      expect(managedDistInfos(a)).toEqual([])
      expect(execs(w)).toEqual([])
      const dep = reports()
        .at(-1)!
        .checks.find((c: { id: string }) => c.id === 'dependencies')
      expect(dep.detail.origins.confirmable).toBe(1)
      expect(JSON.stringify(dep)).not.toContain('mystery')

      // 打开确认包名的框：输入框里是后端的预填（原样），还没有任何东西被装
      await primary(page).click()
      const dialog = page.locator('[data-origin-step]')
      await expect(dialog).toHaveAttribute('data-origin-step', 'names', { timeout: 60_000 })
      const input = dialog.locator('[data-origin-input="mystery_lib"]')
      await expect(input).toBeVisible()
      const prefill = await input.inputValue()
      expect(prefill.length).toBeGreaterThan(0)
      expect(prefill.replace(/_/g, '-')).toBe('mystery-lib') // 预填 = 同名候选，只是输入框的初始值

      // 先填错：这个包能装上，但它不提供 mystery_lib——装后核验必须拦住
      await input.fill('other-lib')
      await page.locator('[data-origin-primary="review"]').click()
      await expect(dialog).toHaveAttribute('data-origin-step', 'confirm', { timeout: 60_000 })
      // 确认页：说清「你指定的、未经可信来源核实；确认后才会安装」；每个包带「用户指定，未经核实」徽标；装到哪也说
      await expect(dialog.locator('[data-origin-sentence]')).toContainText('未经可信来源核实')
      await expect(dialog.locator('[data-origin-confirmed="mystery_lib"]')).toHaveValue('other-lib')
      await dialog.locator('[data-origin-details] summary').click()
      await expect(dialog.locator('[data-origin-disclosed="mystery_lib"]')).toContainText('用户指定，未经核实')
      await expect(dialog.locator('[data-origin-place]')).toBeVisible()
      // 后端披露的同一份事实：影响里的 user_specified，trust 恒为 unverified
      const bodies = reports()
      expect(bodies.length).toBeGreaterThan(0)
      expect(execs(w)).toEqual([])
      expect(managedDistInfos(a)).toEqual([]) // 点确认之前一个包都没装

      await page.locator('[data-origin-primary="confirm"]').click()
      // 装上了，但装上的包里没有脚本要的库：核验不过 → 框里一句人话 + 原因，不激活
      await expect(dialog.locator('[data-origin-error="dependency_import_still_failed"]')).toBeVisible({
        timeout: LONG,
      })
      await expect(dialog.locator('[data-origin-error]')).toContainText('装完后核对发现')
      await dialog.locator('[data-origin-details] summary').click()
      await expect(dialog.locator('[data-origin-dist-reasons]')).toContainText('包名可能填错了')
      // 失败后回到第一步、输入保留；没有执行脚本；错的包没有变成这个项目能用的环境
      await expect(dialog).toHaveAttribute('data-origin-step', 'names')
      await expect(dialog.locator('[data-origin-input="mystery_lib"]')).toHaveValue('other-lib')
      expect(execs(w)).toEqual([])
      expect((await environment(page, a)).project.source).not.toBe('managed_project_env')

      // 改对：确认页 → 确认并安装 → 核验通过 → 框收起，卡片回到可以运行
      await dialog.locator('[data-origin-input="mystery_lib"]').fill('mystery-lib==0.3')
      await page.locator('[data-origin-primary="review"]').click()
      await expect(dialog).toHaveAttribute('data-origin-step', 'confirm', { timeout: 60_000 })
      await expect(dialog.locator('[data-origin-confirmed="mystery_lib"]')).toHaveValue('mystery-lib==0.3')
      await page.locator('[data-origin-primary="confirm"]').click()
      await expect(page.locator('[data-origin-step]')).toHaveCount(0, { timeout: LONG })
      // 装好了、核验通过：受管环境里有这个包（磁盘上的 .dist-info），脚本仍没被执行
      await expect.poll(() => managedDistInfos(a!).some((n) => n.toLowerCase().startsWith('mystery_lib-0.3')), {
        timeout: LONG,
      }).toBe(true)
      expect(execs(w)).toEqual([])
      // 装好、核验通过之后会话重新检查：表外名已被受管环境里装好的唯一 index 来源发行包满足（#938，
      // `depplan._installed_index_satisfies`），联合计划不再给 origins——卡片回到「可以运行」，主按钮是「运行」
      await expect(card(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: LONG })
      await expect(primary(page)).not.toHaveText('仍然运行')
      await expect(primary(page)).toHaveText('打开并运行')

      await primary(page).click()
      await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: LONG })
      const runs = execs(w)
      expect(runs).toHaveLength(1)
      expect(runs[0].lib_value).toBe(42) // mystery_lib 的 answer()：真是装进去的那个包在跑
      expect(inside(runs[0].lib!, path.join(a.dataDir, 'environments'))).toBe(true)
      expect(managedDistInfos(a).some((n) => n.toLowerCase().startsWith('mystery_lib-0.3'))).toBe(true)
      expect(hostCanImport('mystery_lib')).toBe(false)
    } finally {
      await cleanup(a, root)
    }
  },
)
