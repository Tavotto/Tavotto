/**
 * 英文错误恢复路径（issue #30）。
 *
 * 在 en-US 界面语言下真实触发各类失败，逐条验证四件事：
 *   1. 不泄漏中文（系统文案必须是英文；用户内容与诊断原文除外——本 spec
 *      刻意使用纯 ASCII 的项目名，凡是可见错误面出现 CJK 即为泄漏）；
 *   2. 显示稳定、本地化的错误文案（来自 errors:backend.<code> 等表）；
 *   3. 给出用户可执行的下一步（按钮 / 设置入口 / 重试路径）；
 *   4. 不丢失当前项目与未保存编辑（出错后画布还在、还能继续操作）。
 *
 * 本 spec 只挂在 chromium-en project 下（playwright.config 的基础 chromium
 * project 显式 testIgnore 它——spec 自带 en-US locale，两个 project 都跑等于
 * 同一份内容跑两遍）。
 *
 * **本文件里有按平台跳过的用例，先看清它们跑在哪条腿上**：CI 有两条 e2e 腿——
 * `windows-exe-smoke`（windows-latest，打包产物）与 `posix-e2e`
 * （ubuntu-latest，`python -m tavotto`）。两条 POSIX 权限用例在后者上执行，
 * `file_locked` 那条在前者上执行。这个配对由
 * `tests/test_e2e_leg_topology.py` 看住：**每条 skip 都必须点得出一条会执行
 * 它的腿**，配不上当场红——收得到不等于跑得过（issue #30）。
 */
import { spawn } from 'node:child_process'
import { copyFileSync, chmodSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { expect, test } from './fixtures'

const REPO = path.resolve(import.meta.dirname, '..', '..')

test.use({ locale: 'en-US' })

function copyTree(src: string, dest: string): void {
  mkdirSync(dest, { recursive: true })
  for (const e of readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, e.name)
    const d = path.join(dest, e.name)
    if (e.isDirectory()) copyTree(s, d)
    else copyFileSync(s, d)
  }
}

const CJK = /[㐀-鿿豈-﫿]/

/** 断言一个可见错误面上没有中文（系统文案泄漏）。 */
async function expectNoCjk(el: Locator, label: string): Promise<void> {
  const text = (await el.textContent()) ?? ''
  expect(CJK.test(text), `${label} 泄漏了中文：${text.slice(0, 200)}`).toBe(false)
  expect(text.trim().length, `${label} 是空的`).toBeGreaterThan(0)
}

async function openFigures(page: Page, baseURL: string): Promise<void> {
  await page.goto(baseURL)
  await expect(page.getByText('Fig1_kinetics.pdf')).toBeVisible({ timeout: 30_000 })
}

/** 把面板放上画布并进入图内编辑（错误场景大多在 build 阶段暴露）。 */
async function placeAndEdit(page: Page): Promise<void> {
  // Prompt 09 起，双击素材卡 = 打开这张图（快速编辑工作区），**当场就在图内
  // 编辑态**——不再需要先「加入画布」再点一次「编辑图内元素」。
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
}

test('无可用渲染环境：英文的环境缺件卡片 + 可点的修复出口', async ({ app, page }) => {
  // 显式覆盖失效时解释器优先级会**按设计**回退到本机任何带科学栈的解释器
  // （pool._prioritized_candidates 第 5 档）。这台机器上存在可用解释器时，
  // 「无环境」这个状态真实触发不了——如实跳过，真实验证在 nightly 的
  // 「无 Python」档（smoke_app --expect-source bundled 那条链）。
  const a = await app({
    env: { TAVOTTO_WORKER_PYTHON: path.join(os.tmpdir(), 'no-such-python') },
  })
  const diag = await (await fetch(`${a.baseURL}/api/diagnostics`)).json()
  const worker = (diag.checks as { id: string; ok: boolean }[]).find(
    (c) => c.id === 'worker_python' || c.id === 'matplotlib',
  )
  test.skip(!!worker?.ok, '本机存在可用的科学栈解释器（按设计回退），环境缺件场景由 nightly 无 Python 档验证')

  await openFigures(page, a.baseURL)
  await placeAndEdit(page)

  // 缺渲染环境不是「出错」而是缺件：右栏给出环境卡片与能点的出口
  const panel = page.getByLabel('Right panel', { exact: true })
  await expect(
    panel.getByText(/rendering environment|No usable Python/i).first(),
  ).toBeVisible({ timeout: 60_000 })
  await expectNoCjk(panel, 'worker-python 环境卡片')
  // 可执行的下一步：换环境 / 自动安装，至少给一个
  await expect(
    panel.getByRole('button', { name: /Install automatically|Use a different Python|Apply/i }).first(),
  ).toBeVisible()

  // 项目与画布未丢
  await expect(page.getByText(/Canvas is empty/)).toHaveCount(0)
})

test('脚本缺包（missing_dependency）：英文缺包卡片，指名包与修复路径', async ({ app, page }) => {
  // 脚本 import 一个不存在的包：任何解释器都会以 ModuleNotFoundError 收场，
  // 后端按契约报结构化 missing_dependency（绝不自动 pip install）
  const dir = path.join(os.tmpdir(), `tavotto-en-misspkg-${Date.now()}`)
  copyTree(path.join(REPO, 'examples', 'figures'), dir)
  const script = readdirSync(dir).find((f) => f.toLowerCase().includes('fig1') && f.endsWith('.py'))!
  writeFileSync(
    path.join(dir, script),
    'import tavotto_e2e_nonexistent_pkg  # noqa\n' + 'raise SystemExit\n',
    'utf-8',
  )

  const a = await app({ figures: dir })
  await openFigures(page, a.baseURL)
  await placeAndEdit(page)

  const panel = page.getByLabel('Right panel', { exact: true })
  await expect(
    panel.getByText(/tavotto_e2e_nonexistent_pkg/).first(),
  ).toBeVisible({ timeout: 120_000 })
  await expectNoCjk(panel, 'missing-dependency 缺包卡片')
  // 修复路径必须**可操作**，不是一句话。盯控件的无障碍名而不是散文：
  // 文案会改（ADR 0019 的修复卡把旧的「换渲染环境」那段整个换掉了，
  // 这条断言当时红在措辞上，而真问题是新卡片一度**没有**换环境的出口）。
  // 无障碍名是契约的一部分，比措辞稳。
  await expect(panel.getByLabel(/rendering interpreter path/i).first()).toBeVisible()
  // 未知包（curated 与项目声明都解析不出）时**不给一键安装**，只给手动指名
  await expect(panel.getByLabel(/package to install/i).first()).toBeVisible()
})

test('render 失败（脚本抛异常）：英文报错 + 重试按钮，画布不丢', async ({ app, page }) => {
  const dir = path.join(os.tmpdir(), `tavotto-en-crash-${Date.now()}`)
  copyTree(path.join(REPO, 'examples', 'figures'), dir)
  // 让 Fig1 的脚本在 build 时当场抛
  const script = readdirSync(dir).find((f) => f.toLowerCase().includes('fig1') && f.endsWith('.py'))!
  writeFileSync(path.join(dir, script), 'raise RuntimeError("boom from e2e")\n', 'utf-8')

  const a = await app({ figures: dir })
  await openFigures(page, a.baseURL)
  await placeAndEdit(page)

  const panel = page.getByLabel('Right panel', { exact: true })
  // ErrorBlock：错误 + 可展开的 traceback + 重试
  await expect(panel.getByText(/RuntimeError|boom from e2e|failed/i).first()).toBeVisible({
    timeout: 120_000,
  })
  await expect(panel.getByRole('button', { name: /Retry|Try again/i }).first()).toBeVisible()
  // 系统包装文案不得是中文；诊断原文（traceback）本来就是英文
  await expectNoCjk(panel, 'render-crash 错误面')
  await expect(page.getByText(/Canvas is empty/)).toHaveCount(0)
})

test('项目目录不可读：ProjectPicker 英文报错，改对路径可继续', async ({ app, page }) => {
  test.skip(
    process.platform === 'win32',
    'POSIX 权限位；本条在 CI 的 posix-e2e 腿（ubuntu-latest）上执行（issue #30）',
  )
  const locked = path.join(os.tmpdir(), `tavotto-en-locked-${Date.now()}`)
  mkdirSync(locked, { recursive: true })
  chmodSync(locked, 0o000)

  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(page.getByRole('main')).toBeVisible()
  const input = page.getByRole('textbox').first()
  await input.fill(locked)
  await input.press('Enter')

  const alert = page.getByRole('alert')
  await expect(alert).toBeVisible()
  await expectNoCjk(alert, '项目无权限错误')

  // 恢复：换一个能读的目录，同一条路走通
  const ok = path.join(os.tmpdir(), `tavotto-en-ok-${Date.now()}`, 'figures')
  copyTree(path.join(REPO, 'examples', 'figures'), ok)
  await input.fill(ok)
  await input.press('Enter')
  await expect(page.getByText('Fig1_kinetics.pdf')).toBeVisible({ timeout: 30_000 })
  chmodSync(locked, 0o755)
})

test('导出目录不可写：导出失败给英文报错，且不丢项目', async ({ app, page }) => {
  test.skip(
    process.platform === 'win32',
    'POSIX 权限位；本条在 CI 的 posix-e2e 腿（ubuntu-latest）上执行（issue #30）',
  )
  const a = await app()
  await openFigures(page, a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })

  // 先把每项目导出目录设到一个可建的位置，再收走写权限——设置端点会当场
  // 建目录（那是它自己的前置校验），失败必须发生在导出那一步
  const deny = path.join(os.tmpdir(), `tavotto-en-deny-${Date.now()}`, 'out')
  const res = await page.request.patch(`${a.baseURL}/api/project/settings`, {
    data: { export_dir: deny },
  })
  expect(res.ok(), await res.text()).toBe(true)
  chmodSync(deny, 0o500)

  await page.getByRole('button', { name: /^Export$/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  const start = dialog.getByRole('button', { name: /^Export$/ })
  // 预检有阻断/无法核验项时要先勾显式确认（英文成文 “I understand …”）
  const confirm = dialog.locator('label', { hasText: /I understand/ }).locator('input')
  if (await start.isDisabled().catch(() => false)) {
    await confirm.check()
  }
  await start.click()

  /*
   * 导出失败现在带**具体的错误码**（`errors:backend.*`，这里是
   * `tmp_dir_failed`）；`Operation failed` 只是查不到对应译文时的兜底。
   * 只钉兜底那一句等于要求产品**永远说不出具体原因**——这条用例要的是
   * 「有一句英文报错 + 有一条恢复出口」，不是某一句特定的话（ADR 0031）。
   *
   * 这条用例带 `test.skip(win32)`，**在 CI 里从来没跑过**（issue #30）：
   * 断言陈旧了没有任何门禁会说话，只有本机全量跑才看得见。
   */
  const err = dialog
    .getByText(/Couldn't create a temporary file in the export folder|Operation failed/i)
    .first()
  await expect(err).toBeVisible({ timeout: 120_000 })
  await expectNoCjk(err, '导出失败错误')
  // 失败必须留一条出去的路，否则用户只能关掉对话框重来
  await expect(dialog.getByRole('button', { name: /Try again/i })).toBeVisible()

  // 不丢项目：关掉对话框画布还在
  await page.keyboard.press('Escape')
  await expect(page.getByText(/Canvas is empty/)).toHaveCount(0)
  chmodSync(deny, 0o755)
})

test('AI CLI 不可用：设置里英文说明找过哪些位置', async ({ app, page }) => {
  const a = await app({ env: { PATH: path.join(os.tmpdir(), 'empty-path') } })
  await openFigures(page, a.baseURL)

  // CLI 探测**有意**在 PATH 之外搜惯例安装位置（npm 全局 / WindowsApps…）：
  // 这台机器上真装了 codex/claude 的话「都没找到」状态触发不了——如实跳过
  // （CI runner 上没有这两个 CLI，这条在那儿真实运行）
  const caps = await (await fetch(`${a.baseURL}/api/ai/capabilities?refresh=1`)).json()
  const usable = (caps.agents ?? []).some((x: { usable?: boolean }) => x.usable)
  test.skip(usable, '本机在惯例位置装有编码 Agent，「一个都没有」状态触发不了')

  // 选中一个可参数化面板后打开助手 → 英文说明「两个 CLI 都没找到」+ 设置入口。
  // noCli 提示渲染在「Scope and agent」弹层里（AiPanel 的 agent 分区），
  // 不点开弹层它不在 DOM 里——issue #122 记录了「面板顶层无提示」的 UX 疑点。
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await page.getByRole('button', { name: /Assistant/i }).click()
  const panel = page.getByLabel('Right panel', { exact: true })
  await expect(panel).toBeVisible()
  await panel.getByRole('button', { name: /Scope and agent/i }).click()
  // 弹层 portal 到文档根部的 dialog，不在 Right panel 子树里
  const scopeDialog = page.getByRole('dialog')
  await expect(
    scopeDialog.getByText(/No usable coding agent was detected/i).first(),
  ).toBeVisible({ timeout: 30_000 })
  // 可执行的下一步：打开编码 Agent 设置
  await expect(
    scopeDialog.getByRole('button', { name: /Open Coding Agent settings/i }).first(),
  ).toBeVisible()
  await expectNoCjk(panel, 'AI 面板')
  await expectNoCjk(scopeDialog, 'Scope and agent 弹层')
})

/** 打开左侧「图内元素」树并展开全部分组（en-US 名字）。 */
async function openElementTree(page: Page): Promise<void> {
  const nav = page.getByRole('navigation').getByRole('button', { name: 'Figure elements' })
  if ((await nav.getAttribute('aria-expanded')) !== 'true') await nav.click()
  await page.locator('[role="treeitem"]').first().waitFor({ timeout: 30_000 })
  for (let i = 0; i < 8; i++) {
    const g = page.locator('[role="treeitem"][aria-expanded="false"]').first()
    if (!(await g.count())) break
    await g.click()
    await page.waitForTimeout(120)
  }
}

test('原图被独占占用（file_locked）：英文报错说清该关掉谁，改动不丢', async ({ app, page }) => {
  test.skip(
    process.platform !== 'win32',
    '独占锁只在 Windows 上真实存在；本条在 CI 的 windows-exe-smoke 腿上执行（issue #30）',
  )
  const dir = path.join(os.tmpdir(), `tavotto-en-locked-file-${Date.now()}`)
  copyTree(path.join(REPO, 'examples', 'figures'), dir)

  const a = await app({ figures: dir })
  await openFigures(page, a.baseURL)
  await placeAndEdit(page)

  // 写回入口只在面板真有 override 时才亮：选中标题、改一次字号
  await openElementTree(page)
  await page.getByRole('treeitem', { name: /^Title/ }).first().click()
  const panel = page.getByLabel('Right panel', { exact: true })
  const size = panel.getByRole('textbox', { name: 'Font size' })
  await size.fill('12')
  await size.press('Enter')
  await expect(panel.getByText('1 modified')).toBeVisible({ timeout: 30_000 })

  /*
   * 真的独占占用，不是模拟的错误码：PowerShell 以 FileShare.Read 打开原始
   * PDF——**允许别人读、不允许改名/删除**，这正是 Acrobat / 看图工具打开一个
   * 文件时的形状，于是写回最后那步 `os.replace` 抛 PermissionError
   * （后端把它转成 409 `file_locked`，见 app.py 的 _write_back_error）。
   * 用 `-Command` 起一个常驻进程，断言跑完再杀掉；`finally` 保证不留句柄。
   */
  const target = path.join(dir, 'Fig1_kinetics.pdf')
  const holder = spawn(
    'powershell',
    [
      '-NoProfile',
      '-Command',
      `$f=[System.IO.File]::Open('${target.replace(/'/g, "''")}',` +
        `[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::Read);` +
        `Start-Sleep -Seconds 300;$f.Close()`,
    ],
    { stdio: 'ignore' },
  )
  try {
    // 句柄真的开出来再动手（起 PowerShell 比点一次按钮慢得多）
    await page.waitForTimeout(3_000)

    await page.getByRole('button', { name: /Write back to the original file/i }).first().click()
    const dialog = page.getByRole('dialog').first()
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: /^Write back$/ }).click()

    // 稳定的英文文案（errors:backend.file_locked），不是后端拼好的中文原句
    await expect(
      dialog.getByText(/locked by another program/i).first(),
    ).toBeVisible({ timeout: 120_000 })
    // 可执行的下一步：告诉用户去关掉谁
    await expect(dialog.getByText(/Close whatever has it open/i).first()).toBeVisible()
    await expectNoCjk(dialog, 'file_locked 错误面')
  } finally {
    holder.kill()
  }

  // 失败之后改动仍在（写回是事务，原文件与热态都不该被动过）
  await page.keyboard.press('Escape')
  await expect(panel.getByText('1 modified')).toBeVisible()
})

test('updater 离线：检查更新失败给英文报错，界面可继续', async ({ app, page }) => {
  // 经环境代理把出网请求指向一个立即拒绝的端口 = 可靠的「离线」
  const a = await app({
    env: { HTTPS_PROXY: 'http://127.0.0.1:9', HTTP_PROXY: 'http://127.0.0.1:9' },
  })
  await openFigures(page, a.baseURL)

  const res = await page.request.get(`${a.baseURL}/api/update/check`)
  // 后端如实报失败（不是 500 traceback）
  expect([200, 502, 503]).toContain(res.status())

  // 界面侧：设置 → 更新 → 检查，错误以英文显示
  await page.getByRole('button', { name: /Settings/i }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: /Updates?/i }).click()
  await dialog.getByRole('button', { name: /Check now|Check for updates/i }).click()
  const err = dialog.getByRole('alert').first()
  await expect(err).toBeVisible({ timeout: 60_000 })
  // 英文包装 + 诊断原文（urlopen 错误英文原样）；不得漏出「检查失败:」
  await expect(err).toContainText(/Check failed/i)
  await expectNoCjk(err, '更新检查失败')
})

// 「Windows 文件占用（file_locked）」的界面用例现在**在上面**，不再缺着。
//
// 留一段来路说明，因为它是一个「决定被写反的前提挡住」的例子：这里原来写着
// 「e2e workflow 目前只有 Ubuntu 腿，写了也永远进不去 win32 分支」，并据此
// 决定不写这条用例。那个前提**写反了**——当时唯一执行 `pnpm e2e` 的是
// ci.yml 的 `windows-exe-smoke`（windows-latest），恒跳过的恰恰是本文件里
// 两条 POSIX 用例。前提反了，从它推出来的「不写」也就跟着错了（issue #30）。
//
// 三条腿上的分工现在是：界面这半场由上面那条用例在 windows-exe-smoke 上真跑
// （真独占句柄，不是模拟的错误码）；后端行为由 tests/test_windows_regressions.py
// 看护；中英文案由 tests/test_error_codes.py 对拍；jsdom 那一档的文案分支在
// web/src/components/inspector/WriteBackDialog.test.tsx。
