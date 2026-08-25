import { test, expect, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import { createServer, type Server } from 'node:http'
import { existsSync, readFileSync } from 'node:fs'
import type { AddressInfo } from 'node:net'
import path from 'node:path'

/**
 * 浏览器 playground 的端到端证明（ADR 0007 的 Definition of Done）：
 *
 *     普通 .py → File API → Dedicated Worker → **真 Pyodide**（CDN 拉真包）
 *     → 真 matplotlib Figure → 真 FigState/manifest → 真 CanvasStage
 *     → 语义选中 → 真 override → Pyodide 重渲染 → undo → 原样
 *
 * 这套用例**不 mock Worker**，Pyodide 与 matplotlib 从钉死的 CDN 版本真实
 * 下载执行——它慢（冷缓存分钟级）、要联网，是有意的：这是 Phase II 存在
 * 与否的证明，放在专门的 e2e 里跑，不摊给每个小测试。
 *
 * 附带的性质：
 *   * 哨兵测试：上传的源码内容**不出现在任何一个网络请求**里（隐私承诺
 *     「Tavotto 不上传你的代码」的机器可验证形式）；
 *   * 死循环：脚本阶段的硬超时把 Worker 杀掉并给出诚实的错误与出口；
 *   * 预热：`/try` 打开后空闲时只把 **Pyodide 核心**拉下来，**一个科学栈
 *     的 wheel 都不拉**；`saveData` 下什么都不拉；
 *   * 源文件完整性：界面上那句「未改动」等于 Worker 里读回来的 sha256 与
 *     真实源码的 sha256 逐位相同——展开的完整性明细必须是这个数。
 *
 * 前置：python scripts/build_browser_playground.py（产物在 web/dist-playground）。
 */

const DIST = path.resolve(import.meta.dirname, '..', 'dist-playground')

// 案例源码直接读 .py 文件（examples.ts 是 vite 的 ?raw import，Playwright 的
// 加载器不认）；e2e 断言的哈希必须与 bundle 里那份逐字节相同——单一真源保证
const KINETICS_SOURCE = readFileSync(
  path.resolve(import.meta.dirname, '..', 'src', 'playground', 'examples', 'kinetics.py'),
  'utf-8',
)

//: 冷缓存 + CDN 下载 + WASM 实例化，给足；见 §69——只放宽这一个 spec
test.describe.configure({ mode: 'serial' })
test.setTimeout(420_000)

let server: Server
let origin = ''

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.zip': 'application/zip',
  '.json': 'application/json; charset=utf-8',
}

test.beforeAll(async () => {
  test.skip(!existsSync(path.join(DIST, 'index.html')),
    '先跑 python scripts/build_browser_playground.py 生成 dist-playground')
  server = createServer((req, res) => {
    const pathname = new URL(req.url ?? '/', 'http://x').pathname
    const rel = pathname === '/' ? '/index.html' : pathname
    const file = path.join(DIST, rel)
    if (!file.startsWith(DIST) || !existsSync(file)) {
      res.writeHead(404).end('not found')
      return
    }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] ?? 'application/octet-stream' })
    res.end(readFileSync(file))
  })
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok))
  origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
})

test.afterAll(async () => {
  await new Promise<void>((ok) => server?.close(() => ok()))
})

/** 记录整页所有网络请求（URL + POST body），供哨兵断言。 */
function recordRequests(page: Page): { url: string; body: string }[] {
  const seen: { url: string; body: string }[] = []
  page.on('request', (req) => {
    seen.push({ url: req.url(), body: req.postData() ?? '' })
  })
  return seen
}

/** 界面里那种省略过的短哈希：`ba7816b…15ad`。 */
const shortSha = (text: string) => {
  const hex = createHash('sha256').update(text, 'utf8').digest('hex')
  return `${hex.slice(0, 7)}…${hex.slice(-4)}`
}

/** 只看打到钉死的 Pyodide CDN 上的请求。 */
const cdnHits = (reqs: { url: string }[]) =>
  reqs.filter((r) => r.url.startsWith('https://cdn.jsdelivr.net/pyodide/')).map((r) => r.url)

/** 等编辑器就位：权威 SVG 挂进画布。 */
async function waitForEditor(page: Page) {
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({
    timeout: 360_000,
  })
}

/** SVG 里一个 gid 元素的屏幕位置（图内元素命中层就铺在它上面）。 */
async function gidBox(page: Page, gid: string) {
  return page.evaluate((id) => {
    const n = document.querySelector(`[data-element-svg] svg [id="${id}"]`)
    if (!n) return null
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, gid)
}

test('黄金路径：示例脚本 → 真 Pyodide → 语义拖动标题 → 重渲染 → 撤销还原', async ({ page }) => {
  const requests = recordRequests(page)
  await page.goto(`${origin}/?lang=zh`)

  // 空状态两条路平级：拖放区 + **一按就跑的示例** + 隐私说明
  await expect(page.getByText('拖入一个 Matplotlib 脚本')).toBeVisible()
  await expect(page.getByText(/不会把它上传到服务器/)).toBeVisible()
  const sampleCta = page.getByRole('button', { name: /直接试一个示例/ })
  await expect(sampleCta).toBeVisible()

  // 预热：壳渲染完之后空闲时把 Pyodide 核心拉下来，**科学栈一个 wheel 都不拉**
  await expect
    .poll(() => cdnHits(requests).some((u) => /pyodide\.asm\.wasm$/.test(u)), { timeout: 60_000 })
    .toBe(true)
  expect(
    cdnHits(requests).filter((u) => /\.whl$/.test(u)),
    '预热只到核心为止：科学栈要等 import 分类说了话才下载',
  ).toEqual([])

  // 一次点击 → 真 Python 源码经真 Pyodide 执行（不是预烤的 manifest）
  await sampleCta.click()
  // 真话进度：阶段列表出现（不是一个空转的 spinner）
  await expect(page.getByText('加载 Python 运行时')).toBeVisible()

  await waitForEditor(page)

  // 源码证明：文件名 + 未改动。这句话不是口号——它等于「主线程用 Web Crypto
  // 算的原文 sha256」==「Worker 里 Python 从虚拟 FS 读回来算的 sha256」
  const sourceChip = page.getByRole('button', { name: /kinetics\.py · 未改动/ })
  await expect(sourceChip).toBeVisible()
  await expect(page.getByRole('button', { name: /0 条修改/ })).toBeVisible()

  // 语义元素真的在：标题 gid 落在权威 SVG 里
  const before = await gidBox(page, 'axes_0.title')
  expect(before, '权威 SVG 里应有 axes_0.title').not.toBeNull()

  // 用鼠标把标题拖走：语义选中 + 真 override（pos_frac）
  const cx = before!.x + before!.w / 2
  const cy = before!.y + before!.h / 2
  await page.mouse.move(cx, cy)
  await page.mouse.down()
  for (let i = 1; i <= 12; i++) await page.mouse.move(cx + i * 3, cy + i * 2)
  await page.mouse.up()

  // override 进了账本，Pyodide 重渲染回来的 SVG 里标题真的挪了
  await expect(page.getByRole('button', { name: /1 条修改/ })).toBeVisible({ timeout: 60_000 })
  await expect
    .poll(async () => (await gidBox(page, 'axes_0.title'))?.x ?? 0, { timeout: 60_000 })
    .toBeGreaterThan(before!.x + 10)

  // 技术视图展开的是**真实的 patch 表示**
  await page.getByRole('button', { name: /1 条修改/ }).click()
  await expect(page.getByText(/"prop":\s*"pos_frac"/)).toBeVisible()

  // 撤销 = 空 patch 列表全量重放 = 回到原样
  await page.getByRole('button', { name: '撤销' }).click()
  await expect(page.getByRole('button', { name: /0 条修改/ })).toBeVisible()
  await expect
    .poll(async () => (await gidBox(page, 'axes_0.title'))?.x ?? 0, { timeout: 60_000 })
    .toBeLessThan(before!.x + 5)

  // 源码仍然未改动；只读源码面板能打开
  await expect(sourceChip).toBeVisible()
  await sourceChip.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('Reaction kinetics')).toBeVisible()
  // 完整性明细里是**真哈希**：与在 node 里对同一份源码算出来的逐位相同
  await dialog.getByText('完整性', { exact: true }).click()
  await expect(dialog.getByText(`SHA-256 ${shortSha(KINETICS_SOURCE)}`)).toBeVisible()
  await page.keyboard.press('Escape')

  // 整个流程只碰了两类地址：本页静态资源 + 钉死的 Pyodide CDN
  const outside = requests.filter(
    (r) => !r.url.startsWith(origin) && !r.url.startsWith('https://cdn.jsdelivr.net/pyodide/'),
  )
  expect(outside, `不该有别的外呼:\n${outside.map((r) => r.url).join('\n')}`).toEqual([])
})

test('省流量模式：一个字节的 Pyodide 都不预热', async ({ page }) => {
  // Network Information API 只有 Chromium 有；这里显式伪造它说「省流量」
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'connection', {
      value: { saveData: true, effectiveType: '4g' },
      configurable: true,
    })
  })
  const requests = recordRequests(page)
  await page.goto(`${origin}/?lang=zh`)
  await expect(page.getByRole('button', { name: /直接试一个示例/ })).toBeVisible()
  // 给足空闲窗口（requestIdleCallback 的 timeout 是 3s），确认它**没有**发生
  await page.waitForTimeout(8_000)
  expect(cdnHits(requests), 'saveData 下不许在背景里替用户花流量').toEqual([])
  // 预热不是正确性依赖：照样能开会话（这里只验按下去有反应，不等整轮下载）
  await page.getByRole('button', { name: /直接试一个示例/ }).click()
  await expect(page.getByText('加载 Python 运行时')).toBeVisible()
})

test('品牌回站：跟着界面语言走，中文访客不会被送回英文首页', async ({ page }) => {
  await page.goto(`${origin}/?lang=zh`)
  const zhBrand = page.getByRole('link', { name: 'Tavotto 官网' })
  await expect(zhBrand).toBeVisible()
  expect(await zhBrand.getAttribute('href')).toBe('../zh/')

  await page.goto(`${origin}/?lang=en`)
  const enBrand = page.getByRole('link', { name: 'Tavotto homepage' })
  await expect(enBrand).toBeVisible()
  expect(await enBrand.getAttribute('href')).toBe('../')
  // `/try/` 下的相对路径落在站点根上——不是一个指向 playground 自己的死链
  expect(new URL('../', `${origin}/try/`).pathname).toBe('/')
  expect(new URL('../zh/', `${origin}/try/`).pathname).toBe('/zh/')
})

test('哨兵：上传的源码内容不出现在任何网络请求里', async ({ page }) => {
  const SENTINEL = 'TAVOTTO_SENTINEL_7f3c9a1b'
  const src = [
    `# ${SENTINEL}`,
    'import matplotlib.pyplot as plt',
    `fig, ax = plt.subplots(figsize=(2.6, 2))`,
    `ax.plot([0, 1, 2], [1, 0, 2])`,
    `ax.set_title("${SENTINEL}")`,
    'fig.savefig("sentinel.pdf")',
    '',
  ].join('\n')

  const requests = recordRequests(page)
  await page.goto(`${origin}/?lang=zh`)
  await page.locator('input[type=file]').setInputFiles({
    name: 'sentinel_case.py',
    mimeType: 'text/x-python',
    buffer: Buffer.from(src, 'utf-8'),
  })
  await waitForEditor(page)
  // 图真的按源码画出来了：标题元素在权威 SVG 里（matplotlib 的 SVG 把文字
  // 序列化成字形路径，不是 <text>——所以按 gid 断言，不按文字内容）
  await expect
    .poll(async () => (await gidBox(page, 'axes_0.title')) != null, { timeout: 60_000 })
    .toBe(true)

  const leaked = requests.filter(
    (r) => r.url.includes(SENTINEL) || r.body.includes(SENTINEL),
  )
  expect(leaked, '源码内容绝不能出现在任何请求里').toEqual([])
  // 也不落进任何持久化存储（会话只活在内存里；localStorage 只有语言偏好）
  const stored = await page.evaluate(() => JSON.stringify(Object.entries(localStorage)))
  expect(stored).not.toContain(SENTINEL)
  // 顺带：除静态资源与 CDN 之外零外呼
  const outside = requests.filter(
    (r) => !r.url.startsWith(origin) && !r.url.startsWith('https://cdn.jsdelivr.net/pyodide/'),
  )
  expect(outside).toEqual([])
})

test('完整性核对独立于用户解释器：脚本改掉自己并伪造 hashlib，界面仍报「已改动」', async ({ page }) => {
  // codex 审查 P2 指出的那条（连着两轮）：用户脚本跑在**同一个解释器**里、
  // 而且跑在核对之前，所以它可以
  //   ① 改完自己的文件再换掉 hashlib / open，让 Python 侧继续回报原摘要；
  //   ② 留一个内容是原样的诱饵文件，再把引擎记的脚本名改到诱饵头上——
  //      这一层即使摘要挪到了 JS，只要**路径**还取自 Python 跑完之后的回应
  //      就照样能骗过去。
  // 界面上那句「未改动」是当作独立验证展示的，所以摘要与路径**两样都**
  // 必须在用户代码之外定下来。
  //
  // 这条用例把那个场景原样跑一遍：**如果哪天有人把摘要挪回 Python 里，它会红。**
  const src = [
    'import hashlib, sys',
    'import matplotlib.pyplot as plt',
    '',
    'fig, ax = plt.subplots(figsize=(2.6, 2))',
    'ax.plot([0, 1, 2], [1, 0, 2])',
    'ax.set_title("Tamper")',
    'fig.savefig("tamper.pdf")',
    '',
    '# 1) 先把原样存下来，再改掉自己这个文件',
    'with open(__file__, "rb") as f:',
    '    _orig = f.read()',
    'with open(__file__, "ab") as f:',
    '    f.write(b"\\n# appended after execution\\n")',
    '',
    '# 2) 让 Python 侧无论怎么算，都得出**原样**那个摘要',
    '_real = hashlib.sha256',
    'hashlib.sha256 = lambda *a, **k: _real(_orig)',
    '',
    '# 3) 再留一个内容是原样的诱饵，并把引擎记的脚本名改到它头上',
    '#    （sys.modules 绕开静态 import 分类）',
    'with open("/workspace/decoy.py", "wb") as f:',
    '    f.write(_orig)',
    'sys.modules["browser"]._ACTIVE.script_name = "decoy.py"',
    '',
  ].join('\n')

  await page.goto(`${origin}/?lang=zh`)
  await page.locator('input[type=file]').setInputFiles({
    name: 'tamper_case.py',
    mimeType: 'text/x-python',
    buffer: Buffer.from(src, 'utf-8'),
  })
  await waitForEditor(page)

  // 文件真的被改过了 → 必须报「已改动」，而且是那条常驻的危险横幅
  const alarm = page.getByRole('alert')
  await expect(alarm).toBeVisible({ timeout: 60_000 })
  await expect(alarm).toContainText('意外改动')
  await expect(page.getByRole('button', { name: /tamper_case\.py · 意外改动/ })).toBeVisible()
  // 绝不能出现「未改动」——那正是这条用例要挡住的谎
  await expect(page.getByText('· 未改动')).toHaveCount(0)
})

test('Python 够不着 js：拿不到 Worker 全局，也就伪造不了完整性核对', async ({ page }) => {
  // codex 审查第三轮 P2。前两轮把摘要与路径都挪出了 Python，但只要脚本能
  // `import js`，它就能 `js.eval` 改掉 Worker 的任何全局——换掉
  // `crypto.subtle.digest` 让它先削掉追加的尾巴再算，甚至直接
  // `self.postMessage` 伪造一整条响应。**Python 一旦够得着 js，这个 Worker
  // 里就没有任何东西可信**，完整性核对连同协议本身一起失效。
  //
  // 静态分类拦不住：`browser_imports` 有意放行 try/except 里的可选 import，
  // 而 `__import__('js')` 它根本看不见。所以防线在 `loadPyodide` 的
  // `jsglobals`，而且那个对象必须是**无原型**的：普通 `{}` 还挂着
  // `Object.prototype`，`constructor.constructor` 就是一台 Function 构造器，
  // 一句 `return globalThis` 照样把 Worker 全局捞回来。两条路这里都跑。
  const src = [
    'import matplotlib.pyplot as plt',
    'fig, ax = plt.subplots(figsize=(2.6, 2))',
    'ax.plot([0, 1, 2], [1, 0, 2])',
    'ax.set_title("JsEsc")',
    'fig.savefig("jsesc.pdf")',
    '',
    '# 改掉自己，尾巴是一串已知字节',
    'with open(__file__, "ab") as f:',
    '    f.write(b"\\n# appended\\n")',
    '',
    '# try/except 里的可选 import 正是分类器有意放行的那一档；拿到 js 之后',
    '# 换掉 Worker 的 crypto.subtle.digest：算之前先把那串尾巴削回去。',
    'try:',
    '    import js',
    "    js.eval(\"(() => { const real = crypto.subtle.digest.bind(crypto.subtle); const tail = new TextEncoder().encode('\\\\n# appended\\\\n'); crypto.subtle.digest = (alg, data) => { const u = new Uint8Array(data); let strip = u.length >= tail.length; for (let i = 0; i < tail.length && strip; i++) if (u[u.length - tail.length + i] !== tail[i]) strip = false; return real(alg, strip ? u.slice(0, u.length - tail.length) : u); }; })()\")",
    '    _escaped = True',
    'except Exception:',
    '    _escaped = False',
    '',
    '# 第二条路：即使 js.eval 没了，只要 jsglobals 那个对象还挂着',
    '# Object.prototype，`constructor.constructor` 就是一台 Function 构造器，',
    '# 一句 "return globalThis" 就把 Worker 全局捞回来了。',
    'try:',
    '    _g = __import__("js").constructor.constructor("return globalThis")()',
    '    if _g is not None:',
    '        _escaped = True',
    'except Exception:',
    '    pass',
    '',
    '# 第三条路：pyodide.code.run_js 根本不经过 js 模块，jsglobals 管不到它',
    'try:',
    '    _rj = __import__("pyodide.code", fromlist=["run_js"]).run_js',
    '    if _rj("globalThis") is not None:',
    '        _escaped = True',
    'except Exception:',
    '    pass',
    '',
    '# 逃逸成不成功要**看得见**：成功就多产出一张叫 ESCAPED 的图。',
    '# 这一条钉的是 jsglobals；上面那段 digest 掉包钉的是可信原语的捕获。',
    '# 两道防线各有各的判据，少一道都得有用例红。',
    'if _escaped:',
    '    f2, a2 = plt.subplots(figsize=(2, 1.5))',
    '    a2.set_title("escaped")',
    '    f2.savefig("ESCAPED.pdf")',
    '',
  ].join('\n')

  await page.goto(`${origin}/?lang=zh`)
  await page.locator('input[type=file]').setInputFiles({
    name: 'jsesc.py',
    mimeType: 'text/x-python',
    buffer: Buffer.from(src, 'utf-8'),
  })
  await waitForEditor(page)

  // ① js 根本够不着：脚本没能产出那张 ESCAPED 图（有的话这里会是图选择器）
  await expect(page.getByText('ESCAPED', { exact: false })).toHaveCount(0)
  // ② 就算够着了也骗不到摘要：文件确实被改过，界面如实报出来
  await expect(page.getByRole('alert')).toContainText('意外改动', { timeout: 60_000 })
  await expect(page.getByRole('button', { name: /jsesc\.py · 意外改动/ })).toBeVisible()
  await expect(page.getByText('· 未改动')).toHaveCount(0)
})

test('没有 Web Crypto 时降级成「未核对」，而不是把整个会话弄死', async ({ page }) => {
  // codex 审查第四轮 P2。完整性模型自己写着「算不出哈希是**查不了**」，
  // 可我把可信摘要绑定写在了模块求值期——非安全上下文（局域网的 http://
  // 地址就是）根本没有 `crypto.subtle`，那一句直接抛，而且抛在装
  // `onmessage` **之前**：整个 Worker 起不来，会话以 worker_crashed 收场。
  // 为一条状态指示把编辑器弄死，与「预热是优化不是依赖」同一类错误。
  // 主线程那半边：addInitScript 只作用于页面
  await page.addInitScript(() => {
    Object.defineProperty(globalThis.crypto, 'subtle', { value: undefined, configurable: true })
  })
  // **Worker 那半边**：addInitScript 进不了 Worker 的全局，得把 worker 脚本
  // 本身截下来在最前面插一句。这一步不能省——finding 说的正是模块求值期那句
  // 绑定会把 Worker 整个带崩，而那只在 Worker 里复现。
  await page.route('**/pyodide.worker*.js', async (route) => {
    const res = await route.fetch()
    const body = await res.text()
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/javascript; charset=utf-8' },
      body:
        "Object.defineProperty(self.crypto,'subtle',{value:undefined,configurable:true});\n" +
        body,
    })
  })
  await page.goto(`${origin}/?lang=zh`)
  await page.getByRole('button', { name: /直接试一个示例/ }).click()

  // 编辑器照常起来——这是这条用例的重点
  await waitForEditor(page)
  // 状态是「未核对」，**不是**「未改动」（没验过就不许说没改）
  await expect(page.getByRole('button', { name: /kinetics\.py · 未核对/ })).toBeVisible()
  await expect(page.getByText('· 未改动')).toHaveCount(0)
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('draw_event 里改写自己：load 之后 open 之前的窗口也要核到', async ({ page }) => {
  // 网站 PR 上那条审查意见：load 时采的摘要在 `open` **之前**，而 `open` 会
  // 再画一遍——脚本注册的 `draw_event` 回调正是在那一刻才动手改自己的源文件。
  // 只信 load 那一次，编辑器会带着「未改动」开起来，而文件已经变了。
  // 修法是进编辑态本身就复核一次（recheckSeq 从 1 起）。
  const src = [
    'import matplotlib.pyplot as plt',
    '',
    '_n = [0]',
    'def _on_draw(evt):',
    '    _n[0] += 1',
    '    if _n[0] == 2:',
    '        with open(__file__, "ab") as f:',
    '            f.write(b"\\n# rewritten during draw\\n")',
    '',
    'fig, ax = plt.subplots(figsize=(2.6, 2))',
    'ax.plot([0, 1, 2], [1, 0, 2])',
    'ax.set_title("DrawEvent")',
    'fig.canvas.mpl_connect("draw_event", _on_draw)',
    'fig.savefig("drawevt.pdf")',
    '',
  ].join('\n')

  await page.goto(`${origin}/?lang=zh`)
  await page.locator('input[type=file]').setInputFiles({
    name: 'drawevt.py',
    mimeType: 'text/x-python',
    buffer: Buffer.from(src, 'utf-8'),
  })
  await waitForEditor(page)

  // 进编辑态之后那次复核必须逮到它
  await expect(page.getByRole('alert')).toContainText('意外改动', { timeout: 60_000 })
  await expect(page.getByText('· 未改动')).toHaveCount(0)
})

test('不支持的依赖：在下载科学栈之前拒绝，并给桌面版出口', async ({ page }) => {
  const requests = recordRequests(page)
  await page.goto(`${origin}/?lang=zh`)
  await page.locator('input[type=file]').setInputFiles({
    name: 'needs_rdkit.py',
    mimeType: 'text/x-python',
    buffer: Buffer.from('import rdkit\nimport matplotlib.pyplot as plt\n', 'utf-8'),
  })
  await expect(page.getByText(/浏览器 playground 里没有 rdkit/)).toBeVisible({
    timeout: 360_000,
  })
  await expect(page.getByRole('link', { name: /下载 Tavotto/ }).first()).toBeVisible()
  // 分类发生在包下载之前：matplotlib 的包一个都没拉
  const pkgFetches = requests.filter((r) => /matplotlib.*\.whl/.test(r.url))
  expect(pkgFetches).toEqual([])
})

test('死循环：脚本阶段硬超时，Worker 被杀，错误诚实', async ({ page }) => {
  await page.goto(`${origin}/?lang=zh`)
  await page.locator('input[type=file]').setInputFiles({
    name: 'spin.py',
    mimeType: 'text/x-python',
    buffer: Buffer.from('while True:\n    pass\n', 'utf-8'),
  })
  await expect(page.getByText(/超过了浏览器 playground 的时限/)).toBeVisible({
    timeout: 400_000,
  })
  await expect(page.getByRole('button', { name: '换一个脚本' })).toBeVisible()
})
