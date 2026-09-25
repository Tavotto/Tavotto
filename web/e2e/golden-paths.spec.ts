import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { inflateRawSync } from 'node:zlib'
import { expect, openWorkspace, test, writeRuntimeNamedProject } from './fixtures'

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
  const dir = path.join(os.tmpdir(), `tavotto-e2e-${Date.now()}`, '我的 论文 图', 'figures')
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
  // Prompt 09：双击 = 打开（快速编辑）。标注工具画的是**画布对象**，
  // 只在画布排版模式下有落点——先回排版再用它们。返回入口自审计 T01 起
  // 收成了上下文栏上唯一那颗「返回画布」。
  await page.getByRole('button', { name: /返回画布/ }).first().click()

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

test('还没连上源脚本时，界面给得出「重新扫描 / 试运行」而不是让用户对着空列表猜', async ({
  app,
  page,
}) => {
  const dir = path.join(os.tmpdir(), `tavotto-e2e-reg-${Date.now()}`)
  writeRuntimeNamedProject(dir)

  const a = await app({ figures: dir })
  await page.goto(a.baseURL)

  // 项目级动作在工作区抽屉顶上「当前」卡片的「…」里（顶栏项目名只开抽屉）
  await openWorkspace(page)
  await page.locator('[data-workspace-section="current"] button[aria-haspopup]').click()
  await page.getByRole('menuitem', { name: '项目接入状态…' }).click()

  // 断言收在对话框里：素材库脚本区现在也合法列出 render_map.py（Session 5），
  // 全页 getByText 会歧义（strict mode 三处命中）——这里测的对象是接入状态。
  const readiness = page.getByRole('dialog', { name: '项目接入状态' })
  await expect(readiness).toBeVisible()
  // 静态解不出文件名的脚本，必须提供「试运行并连接」这条路（Prompt 08 起，
  // 这个动作挂在**那张图**那一行上，不再挂在一份脚本清单上）
  await expect(readiness.getByRole('button', { name: /试运行并连接/ }).first()).toBeVisible()
  // 高级段仍然列得出项目里的每个 .py。
  // **断言收在那一段里面**：图那一行的「技术详情」（收起的 <details>）里也写着
  // 同一个脚本名，全局 `.first()` 会先命中那个隐藏节点，然后报「hidden」——
  // 量错了对象，而不是功能坏了。
  const allScripts = readiness.locator('details', { hasText: '全部脚本' })
  await allScripts.getByText(/全部脚本/).click()
  await expect(allScripts.getByText('render_map.py').first()).toBeVisible()
})

test('没装 Python 时给出引导，而不是闪退', async ({ app, page }) => {
  // 把探测强制指到一个不存在的解释器：等价于「这台机器上没有可用的 Python」
  const a = await app({ env: { TAVOTTO_WORKER_PYTHON: path.join(os.tmpdir(), 'no-such-python') } })
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

  // **先断言响应本身，再取字段**（#136）。`page.request` 是脱离页面上下文的裸
  // 请求，不带会话令牌；ADR 0008 的会话认证一旦对这个端点生效，这里拿到的是
  // 401 `session_auth_required`，而旧写法直接 `.json()` 再取字段会以一句
  // `Cannot read properties of undefined` 收场——错误码被吞掉，排障要从
  // undefined 反推。
  //
  // 主语写明：e2e 起的 app 由 fixtures 显式关掉会话认证
  // （`TAVOTTO_INSECURE_NO_AUTH=1`，自 ADR 0008 落地那次起就在），认证本身有
  // 自己的真 HTTP 套件 `tests/test_browser_auth.py`。所以这里**期望 200**；
  // 拿到别的就把状态码与响应体原样报出来，而不是让它变成 TypeError。
  const res = await page.request.get(`${a.baseURL}/api/ai/capabilities?refresh=1`)
  expect(
    res.status(),
    `裸 page.request 拿到 ${res.status()}：${(await res.text()).slice(0, 200)}`,
  ).toBe(200)
  const caps = await res.json()
  // 注册表决定有哪些 Agent，用例不再自己列名单
  expect(caps.agents.length).toBeGreaterThan(0)
  for (const agent of caps.agents) {
    if (agent.installed) continue // 系统装在 PATH 之外的常见位置，这条就跳过
    expect(agent.state).toBe('not_installed')   // 没装 ≠ 坏了
    expect(Array.isArray(agent.diagnostics.searched)).toBe(true)
    expect(agent.diagnostics.searched.length).toBeGreaterThan(0)
  }
})

/** 诊断包 zip → { 文件名: 文本 }。包是 deflate 压缩的：直接在字节流里搜字符串恒搜不到
 *  （QA 2026-09-24 指出旧判据是空的），必须先解开。只认中央目录里的条目，方法 0 / 8。 */
function unzipTexts(buf: Buffer): Map<string, string> {
  const out = new Map<string, string>()
  const eocd = buf.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]))
  expect(eocd).toBeGreaterThanOrEqual(0)
  const count = buf.readUInt16LE(eocd + 10)
  let p = buf.readUInt32LE(eocd + 16)
  for (let i = 0; i < count; i++) {
    const method = buf.readUInt16LE(p + 10)
    const size = buf.readUInt32LE(p + 20)
    const nameLen = buf.readUInt16LE(p + 28)
    const extraLen = buf.readUInt16LE(p + 30)
    const commentLen = buf.readUInt16LE(p + 32)
    const local = buf.readUInt32LE(p + 42)
    const name = buf.toString('utf8', p + 46, p + 46 + nameLen)
    const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28)
    const data = buf.subarray(start, start + size)
    out.set(name, (method === 8 ? inflateRawSync(data) : data).toString('utf8'))
    p += 46 + nameLen + extraLen + commentLen
  }
  return out
}

test('导出诊断包：能下载，且不含主目录、用户脚本的报错文字与文件名、请求参数', async ({ app, page }) => {
  // 主语（REL-05）：用户脚本 raise 的文字、脚本文件名、项目所在的主目录、请求的查询串——
  // 每一样都先证明它**确实流经了日志**（完整的 app.log 里有），再断言包里每个文件都没有。
  const CANARY = 'DIAGCANARY_q7'
  const ERR = `${CANARY}_errmsg`
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-diag-'))
  const home = path.join(root, 'home')
  const figures = path.join(home, `${CANARY}_proj`)
  copyTree(path.join(REPO, 'examples', 'figures'), figures)
  writeFileSync(path.join(figures, `${CANARY}_boom.py`), `def main():\n    raise ValueError("${ERR}")\n`)
  copyFileSync(path.join(figures, 'Fig1_kinetics.pdf'), path.join(figures, `${CANARY}_boom.pdf`))
  const regPath = path.join(figures, 'tavotto_registry.json')
  const reg = JSON.parse(readFileSync(regPath, 'utf8'))
  reg.scripts[`${CANARY}_boom.py`] = { entry: 'main', cost: 'light', stems: [`${CANARY}_boom`] }
  writeFileSync(regPath, JSON.stringify(reg))

  const a = await app({ figures, env: { HOME: home, USERPROFILE: home } })
  await page.goto(a.baseURL)
  const render = await page.request.post(`${a.baseURL}/api/engine/render`, {
    data: { id: `${CANARY}_boom.pdf`, patches: [] },
  })
  expect(render.status()).toBe(500)
  const missing = await page.request.get(`${a.baseURL}/api/render?id=${CANARY}_missing.pdf&w=10`)
  expect(missing.ok()).toBe(false)

  const resp = await page.request.get(`${a.baseURL}/api/diagnostics/bundle`)
  expect(resp.ok()).toBe(true)
  expect(resp.headers()['content-type']).toContain('zip')
  const entries = unzipTexts(await resp.body())
  expect([...entries.keys()]).toEqual(expect.arrayContaining(['report.json', 'app.log', 'README.txt']))

  // 对照：这些东西确实写进了完整日志——包里没有它们才说明是脱敏的结果
  const raw = readFileSync(path.join(a.dataDir, 'cache', 'app.log'), 'utf8')
  for (const needle of [ERR, `${CANARY}_boom.py`, `id=${CANARY}_missing`, home]) {
    expect(raw).toContain(needle)
  }
  for (const [name, text] of entries) {
    expect(text, name).not.toContain(CANARY)
    expect(text, name).not.toContain(home)
  }
  // 反证落点：包里的日志确实是这次的（不是空的才搜不到）
  const log = entries.get('app.log') ?? ''
  expect(log).toContain('引擎渲染失败')
  expect(log).toContain('"GET /api/render HTTP/1.1"')
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
