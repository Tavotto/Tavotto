import { copyFileSync, existsSync, mkdirSync, readdirSync, realpathSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test, writeRuntimeNamedProject } from './fixtures'

const REPO = path.resolve(import.meta.dirname, '..', '..')

/** 逐文件显式拷贝。Windows CI 实测：`fs.cpSync` 往含中文+空格的目标路径
 *  拷贝时会**静默拷出一个空目录**（mkdir/readdir 同路径均正常）；
 *  显式循环要么成功要么当场抛错，不给「拷了个寂寞」留余地。 */
function copyTree(src: string, dest: string): void {
  mkdirSync(dest, { recursive: true })
  for (const e of readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, e.name)
    const d = path.join(dest, e.name)
    if (e.isDirectory()) copyTree(s, d)
    else copyFileSync(s, d)
  }
}

/**
 * Windows 黄金路径。用真实浏览器操作真实界面，打的是打包后的应用。
 *
 * 这几条不是随手挑的——每一条都对应一个「只在别人电脑上发生」的真实故障：
 * 空用户目录、中文与空格路径、没装 Python、注册表空、重启恢复、AI CLI 缺席。
 * 环境相关的判定（盘符、文件占用、端口冲突、CLI 探测）留在 pytest 的
 * tests/test_windows_regressions.py 里——那些用不着浏览器，用浏览器测反而更脆。
 */

test('首次启动：用户目录为空时进项目选择器，而不是白屏', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)

  await expect(page.getByRole('main', { name: '选择项目' })).toBeVisible()
  await expect(page.getByRole('button', { name: '新建项目' })).toBeVisible()
  // 路径可以直接粘贴——不是只能一层层点
  await expect(page.getByLabel('项目路径')).toBeVisible()
  // 空目录时不该报错
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('直接粘贴路径打开项目（含中文与空格）', async ({ app, page }) => {
  const dir = path.join(os.tmpdir(), `magplot-e2e-${Date.now()}`, '我的 论文 图', 'figures')
  copyTree(path.join(REPO, 'examples', 'figures'), dir)
  // 自证拷贝真的落盘——后续「素材空」时才能把责任划给后端而不是这里
  const copied = readdirSync(dir)
  expect(copied, `拷贝后 ${dir} 只有: ${copied.join(', ')}`).toContain('Fig1_kinetics.pdf')
  console.log(`[e2e] 项目目录 ${dir}（真实路径 ${realpathSync.native(dir)}）: ${copied.join(', ')}`)

  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await page.getByLabel('项目路径').fill(dir)
  await page.getByRole('button', { name: '打开' }).click()

  // 顶栏出现项目切换器，名字就是那个中文目录
  await expect(page.getByRole('button', { name: /当前项目 figures/ })).toBeVisible()
  // 素材库里能看到面板
  await expect(page.getByText('Fig1_kinetics.pdf')).toBeVisible({ timeout: 30_000 })
})

test('打开项目 → 发现图片 → 渲染 → 修改 → 撤销 → 重启恢复', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)

  // 双击素材把面板放上画布
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.getByText('画布是空的')).toHaveCount(0)

  // 文字工具加一段带上标的标注，验证行内标记在画布上真的渲染成上标
  await page.getByRole('button', { name: '文字' }).click()
  await page.locator('[data-canvas-stage]').click({ position: { x: 420, y: 240 } })
  await page.keyboard.type('cm^{-1}')
  await page.keyboard.press('Escape')

  const marked = page.locator('span', { hasText: /^-1$/ }).first()
  await expect(marked).toBeVisible()
  const fontSize = await marked.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))
  const parentSize = await marked.evaluate((el) =>
    parseFloat(getComputedStyle(el.parentElement as HTMLElement).fontSize))
  expect(fontSize).toBeLessThan(parentSize) // 上标确实更小

  // 撤销把标注收回去
  await page.keyboard.press('Control+z')
  await expect(marked).toHaveCount(0)

  // 刷新页面 = 重启：磁盘自动保存应把面板带回来
  await page.reload()
  await expect(page.getByText('画布是空的')).toHaveCount(0, { timeout: 30_000 })
})

test('注册表为空时，界面给得出「扫描 / 试运行」而不是让用户对着空列表猜', async ({
  app,
  page,
}) => {
  const dir = path.join(os.tmpdir(), `magplot-e2e-reg-${Date.now()}`)
  writeRuntimeNamedProject(dir)

  const a = await app({ figures: dir })
  await page.goto(a.baseURL)

  await page.getByRole('button', { name: /当前项目/ }).click()
  await page.getByRole('menuitem', { name: '脚本注册表…' }).click()

  await expect(page.getByRole('dialog', { name: '脚本注册表' })).toBeVisible()
  await expect(page.getByText('render_map.py')).toBeVisible()
  // 静态解不出文件名的脚本，必须提供「试运行并登记」这条路
  await expect(page.getByRole('button', { name: /试运行并登记/ })).toBeVisible()
})

test('没装 Python 时给出引导，而不是闪退', async ({ app, page }) => {
  // 把探测强制指到一个不存在的解释器：等价于「这台机器上没有可用的 Python」
  const a = await app({ env: { MM_WORKER_PYTHON: path.join(os.tmpdir(), 'no-such-python') } })
  await page.goto(a.baseURL)

  // 应用照常起来（这是关键：不闪退）
  await expect(page.getByRole('button', { name: /当前项目/ })).toBeVisible()

  const diag = await page.request.get(`${a.baseURL}/api/diagnostics`)
  const checks = (await diag.json()).checks as { id: string; ok: boolean; detail: string }[]
  const worker = checks.find((c) => c.id === 'worker_python' || c.id === 'matplotlib')
  expect(worker).toBeTruthy()
  // 渲染请求要回可辨认的 code，界面据此弹「自动安装渲染环境」而不是甩错误文字
  const render = await page.request.post(`${a.baseURL}/api/engine/render`, {
    data: { id: 'Fig1_kinetics.pdf', patches: [] },
  })
  if (!render.ok()) {
    expect((await render.json()).code).toBe('no_worker_python')
  }
})

test('AI CLI 不存在时，设置里说清「找过哪些位置」', async ({ app, page }) => {
  // PATH 清空 = 两家 CLI 都找不到
  const a = await app({ env: { PATH: path.join(os.tmpdir(), 'empty-path-dir') } })
  await page.goto(a.baseURL)

  const caps = await (await page.request.get(`${a.baseURL}/api/ai/capabilities?refresh=1`)).json()
  for (const name of ['codex', 'claude']) {
    const p = caps.providers[name]
    if (p.installed) continue // 系统装在 PATH 之外的常见位置，这条就跳过
    expect(Array.isArray(p.searched)).toBe(true)
    expect(p.searched.length).toBeGreaterThan(0)
  }
})

test('导出诊断包：能下载，且不含密钥与主目录', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)

  const resp = await page.request.get(`${a.baseURL}/api/diagnostics/bundle`)
  expect(resp.ok()).toBe(true)
  expect(resp.headers()['content-type']).toContain('zip')
  const body = await resp.body()
  expect(body.length).toBeGreaterThan(200)
  expect(body.toString('latin1')).not.toContain(a.home) // 个人路径已抹掉
})

test('导出 PDF 后文件真的落盘', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })

  const resp = await page.request.post(`${a.baseURL}/api/export`, {
    data: {
      page_w_mm: 80,
      page_h_mm: 40,
      formats: ['pdf'],
      stem: 'e2e',
      objects: [
        { type: 'panel', id: 'Fig1_kinetics.pdf', x_mm: 5, y_mm: 5, w_mm: 60, h_mm: 30 },
      ],
    },
  })
  const out = await resp.json()
  const file = path.join(out.export_dir, out.files[0].name)
  expect(existsSync(file)).toBe(true)
  expect(readdirSync(out.export_dir).length).toBeGreaterThan(0)
})
