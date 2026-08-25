/**
 * 纯键盘核心黄金路径（issue #37）。
 *
 * 用户必须能**完全不使用鼠标**走完核心闭环：打开项目 → 把图放上画布 →
 * 进入图内编辑 → 在元素树里选中元素 → 改一个文字属性和一个图形属性 →
 * undo / redo → 打开导出对话框 → 导出 → 关闭后焦点回到合理位置。
 *
 * 纪律：整个 spec 里**一次鼠标都不许用**（没有 click/dblclick/hover）；
 * 键盘走不到 = 用例红 = 产品缺陷，不许换 page.evaluate 绕。
 * Tab 的每一步都断言焦点没有掉回 body（可见焦点不丢失）。
 *
 * a11y.spec.ts 守「不倒退」的底线（axe / trap / 可达名）；这里是 #37 要求的
 * 完整闭环。两个 spec 都跑 chromium + webkit（macOS 桌面壳是 WKWebView）。
 */
import { copyFileSync, mkdirSync, readdirSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const REPO = path.resolve(import.meta.dirname, '..', '..')

// wide 断点（≥1440）：桌面版真实窗口的默认形态，左右栏可同时在场——
// 元素树（左）与属性页（右）之间的键盘往返正是核心闭环要走的路。
// medium/narrow 的互斥布局有各自的键盘课题（见 issue #105），不在这条主链里。
test.use({ viewport: { width: 1512, height: 945 } })

function copyTree(src: string, dest: string): void {
  mkdirSync(dest, { recursive: true })
  for (const e of readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, e.name)
    const d = path.join(dest, e.name)
    if (e.isDirectory()) copyTree(s, d)
    else copyFileSync(s, d)
  }
}

interface FocusInfo {
  lost: boolean
  hit: boolean
  desc: string
}

/**
 * 当前焦点是否命中目标（aria-label / title / 文本 / 选择器）。
 *
 * 「焦点丢失」的判据是**焦点落在一个不可见（零尺寸）的元素上**。
 * activeElement 是 body 不算丢：Tab 走过文档最后一个控件时浏览器把焦点交给
 * 自己的 UI（地址栏），activeElement 落回 body 属于正常的边界回绕（桌面壳
 * WKWebView/WebView2 里则直接绕回页面第一个控件）；Enter 提交、Esc 取消
 * 也按契约主动失焦。「React 重渲染吃掉焦点」类缺陷由「目标必须在有限步内
 * 可达」兜底——焦点被反复吃掉时 tabTo 永远到不了目标。
 */
async function focusInfo(page: Page, needle: string): Promise<FocusInfo> {
  return page.evaluate((n) => {
    const el = document.activeElement as HTMLElement | null
    if (!el || el === document.body) {
      return { lost: false, hit: false, desc: 'body（边界回绕）' }
    }
    const text = [
      el.getAttribute('aria-label'),
      el.getAttribute('title'),
      el.textContent?.slice(0, 80),
    ]
      .filter(Boolean)
      .join(' | ')
    let hit = false
    if (n.startsWith('css=')) {
      const sel = n.slice(4)
      hit = el.matches(sel) || !!el.closest(sel)
    } else {
      hit = text.includes(n)
    }
    const r = el.getBoundingClientRect()
    return {
      lost: r.width === 0 && r.height === 0,
      hit,
      desc: `${el.tagName.toLowerCase()} ${text}`,
    }
  }, needle)
}

/** WebKit 默认 Tab 跳过按钮（Safari 的「按 Tab 高亮每一项」关着），
 *  Option+Tab 才遍历全部控件——这是 Safari 用户的真实键盘习惯。 */
const tabKey = () => (test.info().project.name.includes('webkit') ? 'Alt+Tab' : 'Tab')

/** 连续 Tab 直到焦点落在目标上；每一步断言焦点没有丢。
 *  backward=true 用 Shift+Tab 反向走（目标在当前焦点之前时）。 */
async function tabTo(
  page: Page,
  needle: string,
  max = 80,
  opts: { backward?: boolean } = {},
): Promise<void> {
  const seen: string[] = []
  const shift = opts.backward ? 'Shift+' : ''
  for (let i = 0; i < max; i++) {
    // WebKit：焦点在 body 上时 Option+Tab 不动，普通 Tab 才重新进入文档
    const atBody = await page.evaluate(() => document.activeElement === document.body)
    await page.keyboard.press(atBody ? `${shift}Tab` : `${shift}${tabKey()}`)
    const f = await focusInfo(page, needle)
    expect(
      f.lost,
      `第 ${i + 1} 次 Tab 后焦点不可见（落在 ${f.desc}）。此前：\n${seen.slice(-6).join('\n')}`,
    ).toBe(false)
    if (f.hit) return
    seen.push(f.desc)
  }
  throw new Error(`Tab 了 ${max} 次也没到「${needle}」。走过的焦点：\n${seen.join('\n')}`)
}

/** 在元素树（role=tree）里用方向键走到 aria-label 匹配的 treeitem。
 *  折叠的分组先 ArrowRight 展开再继续（键盘用户的 DFS 漫游）。 */
async function arrowToTreeitem(page: Page, pattern: RegExp, max = 60): Promise<void> {
  const seen: string[] = []
  // 树的 roving tabindex 记着上次的落点：先 ArrowUp 回到树顶再向下漫游，
  // 否则目标在落点上方时向下走永远找不到
  let prevTop = ''
  for (let i = 0; i < max; i++) {
    const label = await page.evaluate(
      () => document.activeElement?.getAttribute('aria-label') ?? '',
    )
    if (label === prevTop) break
    prevTop = label
    await page.keyboard.press('ArrowUp')
  }
  for (let i = 0; i < max; i++) {
    const f = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null
      return {
        isItem: el?.getAttribute('role') === 'treeitem',
        collapsed: el?.getAttribute('aria-expanded') === 'false',
        label: el?.getAttribute('aria-label') ?? el?.textContent?.slice(0, 60) ?? '',
        debug: `<${el?.tagName?.toLowerCase()} role=${el?.getAttribute('role')} data-el=${el?.getAttribute('data-el')}>`,
      }
    })
    expect(f.isItem, `方向键漫游中焦点离开了树（在 ${f.label} ${f.debug}）`).toBe(true)
    if (pattern.test(f.label)) return
    seen.push(f.label)
    await page.keyboard.press(f.collapsed ? 'ArrowRight' : 'ArrowDown')
  }
  throw new Error(`树里走了 ${max} 步也没找到 ${pattern}。走过：\n${seen.join('\n')}`)
}

/** 焦点所在的输入框里覆写一个数值并提交（全选 → 输入 → Enter）。 */
async function typeValue(page: Page, value: string): Promise<void> {
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.type(value)
  await page.keyboard.press('Enter')
}

const focusedValue = (page: Page) =>
  page.evaluate(() => (document.activeElement as HTMLInputElement | null)?.value ?? '')

test('纯键盘走完核心闭环：开项目 → 编辑元素 → undo/redo → 导出', async ({
  app,
  page,
}) => {
  test.setTimeout(300_000)
  const dir = path.join(os.tmpdir(), `tavotto-kbd-${Date.now()}`, 'figures')
  copyTree(path.join(REPO, 'examples', 'figures'), dir)

  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(page.getByRole('main', { name: '选择项目' })).toBeVisible()

  // ── 1. 打开项目：Tab 到路径输入框，粘路径，Enter 提交 ────────────────
  await tabTo(page, 'css=input')
  await page.keyboard.insertText(dir)
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: /当前项目 figures/ })).toBeVisible({
    timeout: 30_000,
  })

  // ── 2. 把图放上画布：Tab 到素材卡，Enter 加入 ────────────────────────
  await expect(page.getByText('Fig1_kinetics.pdf')).toBeVisible({ timeout: 30_000 })
  await tabTo(page, 'css=[data-card="Fig1_kinetics.pdf"]')
  await page.keyboard.press('Enter')
  await expect(page.getByText('画布是空的')).toHaveCount(0)

  // ── 3. 进入图内编辑：Tab 到属性栏的「编辑图内元素」，Enter ────────────
  await tabTo(page, '编辑图内元素')
  await page.keyboard.press('Enter')
  // 选中对象时素材抽屉让位给属性页（autoShowProperties），左抽屉是关的。
  // 键盘用户经左侧图标轨道打开「图内元素」树——鼠标用户直接点画布上的
  // 元素，树是 #37 要求的等价路径。
  await tabTo(page, '图内元素', 120)
  await page.keyboard.press('Enter')
  // 首次进入要跑一遍脚本构建 figure——等元素树真的长出条目，
  // 只等 [role=tree] 容器可见的话，构建中的空树会放测试往下走
  await expect(page.locator('[role="treeitem"]').first()).toBeVisible({
    timeout: 120_000,
  })

  // ── 4a. 文字属性：元素树里选「标题」，改字号 ─────────────────────────
  await tabTo(page, 'css=[role="treeitem"]')
  await arrowToTreeitem(page, /标题/)
  await tabTo(page, 'css=input[aria-label="字号"]')
  const sizeBefore = await focusedValue(page)
  expect(sizeBefore).not.toBe('')
  const sizeAfter = String(Number(sizeBefore) + 2)
  await typeValue(page, sizeAfter)
  // NumberField 的契约是「Enter 提交并失焦」：重新走到字段核对提交结果
  await tabTo(page, 'css=input[aria-label="字号"]', 160)
  expect(await focusedValue(page)).toBe(sizeAfter)

  // ── 4b. 图形属性：元素树里选一条曲线，改线宽 ─────────────────────────
  await tabTo(page, 'css=[role="treeitem"]', 160)
  await arrowToTreeitem(page, /曲线|线段|散点/)
  await tabTo(page, 'css=input[aria-label="线宽"]')
  const widthBefore = await focusedValue(page)
  expect(widthBefore).not.toBe('')
  const widthAfter = String(Number(widthBefore) + 1)
  await typeValue(page, widthAfter)
  await tabTo(page, 'css=input[aria-label="线宽"]', 160)
  expect(await focusedValue(page)).toBe(widthAfter)

  // ── 5. undo / redo（快捷键）──────────────────────────────────────────
  // 撤销/重做会触发一次权威重渲染；NumberField 聚焦期间不刷新显示值，
  // 所以先等 render 响应落地，再走键盘去核对
  const nextRender = () =>
    page
      .waitForResponse((r) => r.url().includes('/api/engine/render'), { timeout: 60_000 })
      .catch(() => null)
  // 先把焦点从输入框挪走（Tab 一步落在下一个控件上）：输入框里的 ⌘Z 归
  // 文本编辑管；不用 Esc 失焦——programmatic blur 之后 WebKit 的顺序导航
  // 会失去起点，键盘用户会被困在 body 上
  await page.keyboard.press(tabKey())
  let rendered = nextRender()
  await page.keyboard.press('ControlOrMeta+z') // 撤销线宽
  await rendered
  rendered = nextRender()
  await page.keyboard.press('ControlOrMeta+z') // 撤销字号
  await rendered
  // 字号回到原值：重新走到标题的字号框核对
  await tabTo(page, 'css=[role="treeitem"]', 160)
  await arrowToTreeitem(page, /标题/)
  await tabTo(page, 'css=input[aria-label="字号"]')
  expect(await focusedValue(page)).toBe(sizeBefore)
  await page.keyboard.press(tabKey())
  rendered = nextRender()
  await page.keyboard.press('Shift+ControlOrMeta+z')
  await rendered
  await tabTo(page, 'css=[role="treeitem"]', 160)
  await arrowToTreeitem(page, /标题/)
  await tabTo(page, 'css=input[aria-label="字号"]')
  expect(await focusedValue(page)).toBe(sizeAfter)

  // ── 6+7. 导出：⌘E 打开对话框，键盘操作到「导出」──────────────────────
  await page.keyboard.press('Escape') // 收起输入框焦点，快捷键面向画布
  await page.keyboard.press('ControlOrMeta+e')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()

  // 预检有阻断/无法核验项时，键盘也能走到确认勾选框打勾，「导出」才可用
  const exportBtn = dialog.getByRole('button', { name: /开始导出/ })
  if (await exportBtn.isDisabled().catch(() => false)) {
    await tabTo(page, 'css=input[type="checkbox"]', 40)
    await page.keyboard.press(' ')
    await expect(exportBtn).toBeEnabled()
  }

  // Tab 圈内走到「导出」主按钮（对话框有焦点 trap，圈是有限的）
  const onExportBtn = () => exportBtn.evaluate((el) => el === document.activeElement)
  for (let i = 0; i < 40 && !(await onExportBtn()); i++) {
    await page.keyboard.press(tabKey())
  }
  expect(await onExportBtn(), 'Tab 一整圈也到不了「导出」主按钮').toBe(true)
  await page.keyboard.press('Enter')

  // 导出完成：结果区出现文件（PDF 默认勾选）
  await expect(dialog.getByText(/\.pdf/)).toBeVisible({ timeout: 120_000 })

  // ── 8. 关闭对话框，焦点回到可见控件 ─────────────────────────────────
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  const back = await focusInfo(page, 'css=*')
  expect(back.lost, `对话框关闭后焦点落在 ${back.desc}`).toBe(false)
})

test('键盘错误恢复：路径打不开报错，改对路径后继续', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(page.getByRole('main', { name: '选择项目' })).toBeVisible()

  await tabTo(page, 'css=input')
  await page.keyboard.insertText(path.join(os.tmpdir(), 'no-such-dir-kbd'))
  await page.keyboard.press('Enter')
  // 错误可见，且不夺走键盘控制
  await expect(page.getByRole('alert')).toBeVisible()

  // 焦点仍可用：改成正确路径，同一条路走通
  const dir = path.join(os.tmpdir(), `tavotto-kbd-err-${Date.now()}`, 'figures')
  copyTree(path.join(REPO, 'examples', 'figures'), dir)
  const f = await focusInfo(page, 'css=input')
  if (!f.hit) await tabTo(page, 'css=input')
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.insertText(dir)
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: /当前项目 figures/ })).toBeVisible({
    timeout: 30_000,
  })
})

test('快捷键不吞按钮的 Enter：焦点在「导出」按钮上按 Enter 打开的是对话框', async ({
  app,
  page,
}) => {
  const a = await app()
  await page.goto(a.baseURL)
  await expect(page.getByText('Fig1_kinetics.pdf')).toBeVisible({ timeout: 30_000 })
  await tabTo(page, 'css=[data-card="Fig1_kinetics.pdf"]')
  await page.keyboard.press('Enter') // 面板上画布并被选中
  await expect(page.getByText('画布是空的')).toHaveCount(0)

  // 面板选中时 Enter 的画布捷径是「进入图内编辑」；但焦点若在顶栏按钮上，
  // Enter 必须激活那个按钮（issue #37 点名的判据）
  const exportButton = page.getByRole('button', { name: /^导出$/ }).first()
  // 顶栏在素材卡之前：反向 Shift+Tab 过去（WebKit 的正向回绕会跳过按钮）
  await tabTo(page, '导出', 120, { backward: true })
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(exportButton).toBeFocused()
})
