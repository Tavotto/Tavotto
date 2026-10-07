/**
 * 排版时间线抽屉（审计 T04 → ADR 0101）。
 *
 * 守的事：
 *
 * 1. **恢复之前先把当前状态存成「恢复前」节点**，而且它必须在写入**之前**落
 *    （写完再存等于存下来的已经是恢复后的内容）；存不下来就不恢复。恢复是一次
 *    commit，⌘Z 一步退回。
 * 2. **预览不改当前排版**：点一个节点打开模态预览对话框（用户 2026-09-27 反馈后
 *    从「盖在画布上」改来），文档、历史一个字节都不动；默认焦点在「关闭」上。
 * 3. **按天分组、类型标记、只看命名**；命名 / 改名走 PATCH，「存为命名节点」
 *    带 `named: true`（程序起的「恢复前」名字不带）。
 * 4. **每行说的是「什么时候 → 变了什么」**，不是把日期说两遍。
 * 5. **每行一张缩略图，而列表一份正文都不拉**（fu-thumbs）：有位图缩略图用位图，
 *    没有的退回列表端点随元信息发来的草图。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createVersion: vi.fn(),
  deleteVersion: vi.fn(),
  duplicateVersion: vi.fn(),
  fetchTimeline: vi.fn(),
  fetchVersionDoc: vi.fn(),
  updateVersion: vi.fn(),
  putVersionThumb: vi.fn(),
}))
// 缩略图合成在 jsdom 里没有 canvas；这里只关心节点有没有拍、拍的是什么
vi.mock('@/lib/timelineThumb', () => ({
  captureThumbSources: vi.fn(() => new Map()),
  composeTimelineThumb: vi.fn(async () => null),
}))

import {
  ApiError,
  createVersion,
  deleteVersion,
  duplicateVersion,
  fetchTimeline,
  fetchVersionDoc,
  updateVersion,
  type LayoutVersionMeta,
  type TimelineBudget,
} from '@/lib/api'
import { VersionDrawer } from '@/components/VersionDialog'
import { startNamedNode } from '@/store/actions'
import { NamedNodeQuickBox } from '@/components/NamedNodeQuickBox'
import { THUMB_OBJECT_LIMIT, THUMB_TEXT_CHARS } from '@/components/CanvasThumb'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useTimelineStore } from '@/store/timelineStore'
import { setCurrentProjectId } from '@/lib/session'
import { useUiStore } from '@/store/uiStore'
import { formatMessage } from '@/i18n'
import { emptyProject, type FigureDocument, type TextObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockList = vi.mocked(fetchTimeline)
const mockDoc = vi.mocked(fetchVersionDoc)
const mockCreate = vi.mocked(createVersion)
const mockUpdate = vi.mocked(updateVersion)

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const CANVAS_ID = 'c_v'
const NOW = Date.now()
const DAY = 86_400_000

const meta = (over: Partial<LayoutVersionMeta> = {}): LayoutVersionMeta => ({
  id: 'v1',
  name: '09-06 21:30',
  ts: NOW - 60_000,
  auto: true,
  kind: 'auto',
  named: false,
  description: '',
  objects: 1,
  page: { w: 150, h: 100 },
  canvasId: CANVAS_ID,
  canvasName: 'Fig 1',
  ...over,
})

const snapshot = (): FigureDocument => ({
  schema: 2,
  name: 'Fig 1',
  page: { w: 150, h: 100 },
  objects: [text('old', '版本里的那一段')],
  guides: [],
})

let root: Root

/** `fetchTimeline` 的返回按时间升序（抽屉自己 reverse 成最新在上） */
async function mount(
  list: LayoutVersionMeta[],
  budget?: TimelineBudget,
  // 默认把命名输入展开（多数用例要敲名字）；`false` = 抽屉刚打开时的样子：只有「给现在存个名字…」按钮
  naming = true,
) {
  mockList.mockResolvedValue({ versions: list, budget })
  useUiStore.setState({ versionsOpen: true })
  useTimelineStore.setState({ namingOpen: false })
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <VersionDrawer />
        <NamedNodeQuickBox />
      </TooltipProvider>,
    )
  })
  await flush()
  if (naming) await act(async () => $<HTMLButtonElement>('[data-timeline-name-open]')!.click())
  // 命名输入展开后下一帧把焦点送进名字框：等它落定，否则它会在用例中途抢走焦点
  // （比如正在行内改名的输入框被 blur，改名在空草稿上提交）
  await act(async () => {
    await new Promise((r) => requestAnimationFrame(() => r(null)))
  })
}

const flush = () =>
  act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve()
  })

const rows = () => [...document.querySelectorAll('[data-timeline-row]')] as HTMLButtonElement[]
/** 行上的「预览」钮（单击行只选中，不开模态） */
const previews = () =>
  [...document.querySelectorAll('[data-timeline-preview-button]')] as HTMLButtonElement[]
const node = (id: string) => document.querySelector(`[data-timeline-node="${id}"]`)!
const days = () =>
  [...document.querySelectorAll('[data-timeline-day] h3')].map((h) => h.textContent)
const $ = <T extends Element = HTMLElement>(sel: string) => document.querySelector(sel) as T | null

beforeEach(async () => {
  document.body.innerHTML = ''
  vi.clearAllMocks()
  useTimelineStore.setState({ preview: null, rev: 0, namingOpen: false, restoring: null, restoreToken: null })
  mockCreate.mockResolvedValue({ version: meta({ id: 'v_backup' }) })
  mockDoc.mockResolvedValue({ ...meta(), doc: snapshot() })
  mockUpdate.mockResolvedValue({ version: meta() })
  // 当前画布里有一段**不同的**文字：恢复会把它盖掉，那正是要先存一版的理由
  const pd = emptyProject()
  pd.canvases[0].id = CANVAS_ID
  pd.activeCanvasId = CANVAS_ID
  pd.canvases[0].objects = [text('now', '现在这一段')]
  await useDocumentStore.getState().switchDocument(pd, 'd_versions')
})

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  useUiStore.setState({ versionsOpen: false })
})

const ids = () => useDocumentStore.getState().doc.objects.map((o) => o.id)

/* ------------------------------ 预览与恢复 -------------------------------- */

describe('预览对话框：只读，不改当前排版', () => {
  const dialog = () => $('[data-dialog="timeline-preview"]')

  it('点节点 = 打开模态预览对话框，画的是那一刻；文档与历史一个字节都不动', async () => {
    await mount([meta()])
    const pastBefore = useDocumentStore.getState().past.length
    const docBefore = useDocumentStore.getState().doc
    await act(async () => previews()[0].click())
    await flush()
    expect(dialog()).not.toBeNull()
    expect(dialog()!.querySelector('[data-timeline-preview]')?.getAttribute('data-timeline-preview')).toBe('v1')
    // 对话框里是**那一刻**的内容，不是当前的
    expect(dialog()!.textContent).toContain('版本里的那一段')
    // 当前排版原样：同一个对象引用、历史没长
    expect(useDocumentStore.getState().doc).toBe(docBefore)
    expect(useDocumentStore.getState().past.length).toBe(pastBefore)
    expect(mockCreate).not.toHaveBeenCalled()
  })

  it('标题写「预览：时间 名字」；默认焦点在对话框容器上（回车不会误触恢复）；页脚只有「恢复到这里」', async () => {
    await mount([meta({ kind: 'named', named: true, auto: false, name: '投稿前' })])
    await act(async () => previews()[0].click())
    await flush()
    expect(dialog()!.textContent).toMatch(/预览：.*投稿前/)
    const content = $('[data-dialog="timeline-preview"]')!
    expect(document.activeElement).toBe(content)
    // 关闭只有右上角 × 一处（2026-10-07 设计审计 §10.2：此前页脚还有一颗重复的「关闭」）
    expect($('[data-timeline-preview-close]')).toBeNull()
    expect(content.querySelectorAll('[data-dialog-close]')).toHaveLength(1)
  })

  it('右上角关闭收起对话框，排版不动', async () => {
    await mount([meta()])
    await act(async () => previews()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-dialog-close]')!.click())
    expect(useTimelineStore.getState().preview).toBeNull()
    expect(ids()).toEqual(['now'])
  })

  it('与当前对比：并排时两格——那一刻与当前各一张；叠加时当前是描边', async () => {
    await mount([meta()])
    await act(async () => previews()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-timeline-view] [data-value="side"]')!.click())
    // 判据落在两格画框自己身上——差异列表里也写着「现在这一段」，按整个对话框的文字判是空的
    const frame = (k: string) => dialog()!.querySelector(`[data-timeline-frame="${k}"]`)
    expect(frame('moment')?.textContent).toContain('版本里的那一段')
    expect(frame('current')?.textContent).toContain('现在这一段')
    expect(frame('current')?.textContent).not.toContain('版本里的那一段')
    await act(async () => $<HTMLButtonElement>('[data-timeline-view] [data-value="overlay"]')!.click())
    expect(dialog()!.querySelector('[data-timeline-overlay-current]')).not.toBeNull()
  })

  it('差异列表说出「和现在比变了什么」', async () => {
    await mount([meta()])
    await act(async () => previews()[0].click())
    await flush()
    expect(dialog()!.querySelector('[data-timeline-diff]')?.textContent).toContain('现在这一段')
  })

  it('关掉抽屉，预览跟着退出', async () => {
    await mount([meta()])
    await act(async () => previews()[0].click())
    await flush()
    await act(async () => useUiStore.setState({ versionsOpen: false }))
    expect(useTimelineStore.getState().preview).toBeNull()
  })
})

describe('恢复：先存「恢复前」，再写，⌘Z 能退回', () => {
  it('先存下当前内容（关键时刻 before_restore，不是命名节点），再写入节点内容', async () => {
    await mount([meta()])
    await act(async () => previews()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
    await flush()

    expect(mockCreate).toHaveBeenCalledTimes(1)
    const sent = mockCreate.mock.calls[0][1]
    expect(sent).toMatchObject({ auto: true, moment: 'before_restore', canvasId: CANVAS_ID })
    // 名字是程序起的：**不是**命名节点，照常参与裁剪
    expect(sent.named).toBeUndefined()
    // 存下去的是**恢复前**的内容：里面是当前那一段，不是节点里的那一段
    expect(sent.doc?.objects.map((o) => o.id)).toEqual(['now'])
    // 写入确实发生了
    expect(ids()).toEqual(['old'])
    // 恢复后预览退出
    expect($('[data-timeline-preview]')).toBeNull()
  })

  it('恢复是一条历史：撤销一步回到恢复之前', async () => {
    await mount([meta()])
    await act(async () => previews()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
    await flush()
    expect(ids()).toEqual(['old'])
    await act(async () => {
      useDocumentStore.getState().undo()
    })
    expect(ids()).toEqual(['now'])
  })

  it('布局组跟着对象恢复：对象身上的 groupId 指向的是那一版的组', async () => {
    const withGroup: FigureDocument = {
      ...snapshot(),
      objects: [
        { ...text('old', '甲'), groupId: 'g1' },
        { ...text('old2', '乙'), groupId: 'g1' },
      ],
      layoutGroups: [{ id: 'g1', kind: 'row', order: ['old', 'old2'], gap: 2, align: 'start' }],
    }
    mockDoc.mockResolvedValue({ ...meta(), doc: withGroup })
    await mount([meta()])
    await act(async () => previews()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
    await flush()
    expect(useDocumentStore.getState().doc.layoutGroups?.map((g) => g.id)).toEqual(['g1'])
  })

  it('「恢复前」存不下来就不恢复：当前排版原样', async () => {
    mockCreate.mockRejectedValueOnce(new Error('disk full'))
    await mount([meta()])
    await act(async () => previews()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
    await flush()
    expect(ids()).toEqual(['now'])
  })
})

describe('抽屉的焦点（2026-10-07 设计审计 §10.2）', () => {
  it('打开时焦点交进抽屉本身；关上还给打开前的那个控件', async () => {
    const trigger = document.createElement('button')
    document.body.appendChild(trigger)
    trigger.focus()
    await mount([meta()], undefined, false)
    expect(document.activeElement).toBe($('[data-timeline-drawer]'))
    await act(async () => useUiStore.setState({ versionsOpen: false }))
    expect(document.activeElement).toBe(trigger)
    trigger.remove()
  })
})

describe('内联条的「恢复到这里」与预览恢复同一把锁（Codex #831 P1）', () => {
  it('「恢复前」还在存时画布被模态遮住、抽屉与预览都关不掉；存完照常恢复、锁摘掉', async () => {
    let done!: (v: Awaited<ReturnType<typeof createVersion>>) => void
    mockCreate.mockImplementationOnce(() => new Promise((resolve) => (done = resolve)))
    await mount([meta()], undefined, false)
    const bar = node('v1').querySelector('[data-timeline-row-actions]')!
    await act(async () => bar.querySelector<HTMLButtonElement>('[data-timeline-row-restore]')!.click())
    await flush()
    expect(mockCreate).toHaveBeenCalledTimes(1)
    // 锁：模态预览以 busy 开着（遮罩挡住画布），× 收起
    const dlg = $('[data-dialog="timeline-preview"]')
    expect(dlg).not.toBeNull()
    expect(dlg!.getAttribute('aria-busy')).toBe('true')
    expect($('[data-dialog="timeline-preview"] [data-dialog-close]')).toBeNull()
    expect($('[data-dialog-scrim]')).not.toBeNull()
    // 抽屉也关不掉（右上角 × 禁用、Esc 不收）
    expect($<HTMLButtonElement>('[data-timeline-close]')!.disabled).toBe(true)
    await act(async () => {
      $('[data-timeline-drawer]')!.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
      )
    })
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await flush()
    expect(useUiStore.getState().versionsOpen).toBe(true)
    expect(useTimelineStore.getState().preview).not.toBeNull()
    expect(ids()).toEqual(['now'])

    await act(async () => done({ version: meta({ id: 'v_backup' }) }))
    await flush()
    // 恢复写进去了，锁摘掉：预览退出、抽屉的关闭钮能用
    expect(ids()).toEqual(['old'])
    expect(useTimelineStore.getState().restoring).toBeNull()
    expect(useTimelineStore.getState().preview).toBeNull()
    expect($<HTMLButtonElement>('[data-timeline-close]')!.disabled).toBe(false)
  })

  it('「恢复前」存不下来：不写、锁摘掉、为这次恢复打开的预览收回去', async () => {
    mockCreate.mockRejectedValueOnce(new Error('disk full'))
    await mount([meta()], undefined, false)
    const bar = node('v1').querySelector('[data-timeline-row-actions]')!
    await act(async () => bar.querySelector<HTMLButtonElement>('[data-timeline-row-restore]')!.click())
    await flush()
    expect(ids()).toEqual(['now'])
    expect(useTimelineStore.getState().restoring).toBeNull()
    expect(useTimelineStore.getState().preview).toBeNull()
    expect($<HTMLButtonElement>('[data-timeline-close]')!.disabled).toBe(false)
  })
})

describe('恢复锁在取正文之前就挂上、按凭据摘（Codex #831 P1）', () => {
  it('A 的正文还在路上时再点 B 的「恢复到这里」：B 不取、不写，只有 A 写进去', async () => {
    let giveA!: (v: Awaited<ReturnType<typeof fetchVersionDoc>>) => void
    const docOf = (id: string): FigureDocument => ({ ...snapshot(), objects: [text(id, id)] })
    mockDoc.mockImplementation((_doc, id) =>
      id === 'vA'
        ? new Promise((resolve) => (giveA = resolve))
        : Promise.resolve({ ...meta({ id }), doc: docOf(`from_${id}`) }),
    )
    await mount([meta({ id: 'vA', ts: NOW - 120_000 }), meta({ id: 'vB' })], undefined, false)
    const restoreBtn = (id: string) =>
      node(id).querySelector<HTMLButtonElement>('[data-timeline-row-restore]')!
    await act(async () => restoreBtn('vA').click())
    await flush()
    // A 还在取正文：锁已挂上，所有行的「恢复到这里」一起禁用、抽屉关不掉
    expect(useTimelineStore.getState().restoring).not.toBeNull()
    expect(restoreBtn('vB').disabled).toBe(true)
    expect($<HTMLButtonElement>('[data-timeline-close]')!.disabled).toBe(true)
    await act(async () => restoreBtn('vB').click())
    await flush()
    expect(mockDoc).toHaveBeenCalledTimes(1)

    await act(async () => giveA({ ...meta({ id: 'vA' }), doc: docOf('from_vA') }))
    await flush()
    expect(ids()).toEqual(['from_vA'])
    expect(mockCreate).toHaveBeenCalledTimes(1)
    expect(useTimelineStore.getState().restoring).toBeNull()
  })

  it('同一上下文已有一次恢复在飞：再挂锁被拒；过期的持有者摘不掉后来者的锁', () => {
    const tl = () => useTimelineStore.getState()
    const first = tl().beginRestore('ctx')
    expect(first).not.toBeNull()
    expect(tl().beginRestore('ctx')).toBeNull()
    tl().endRestore(first!)
    expect(tl().restoring).toBeNull()
    const second = tl().beginRestore('ctx')
    expect(second).not.toBeNull()
    // 第一次的 finally 晚到（或重复）：不许摘掉第二次的锁
    tl().endRestore(first!)
    expect(tl().restoring).toBe('ctx')
    tl().endRestore(second!)
    expect(tl().restoring).toBeNull()
  })

  it('别的上下文挂着的旧锁不挡新上下文；旧的那次结束也摘不掉新上下文的锁', () => {
    const tl = () => useTimelineStore.getState()
    const a = tl().beginRestore('ctxA')
    const b = tl().beginRestore('ctxB')
    expect(b).not.toBeNull()
    tl().endRestore(a!)
    expect(tl().restoring).toBe('ctxB')
    tl().endRestore(b!)
    expect(tl().restoring).toBeNull()
  })
})

describe('恢复在飞时预览锁住（Codex #679 P1）', () => {
  // 「恢复前」节点还没存完就能关掉预览回去编辑的话，晚到的恢复会把新编辑整份盖掉
  const ways: Record<string, () => Promise<void>> = {
    Esc: async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    },
    右上角关闭: async () => {
      $<HTMLButtonElement>('[data-dialog-close]')?.click()
    },
    点外面: async () => {
      // Radix 在打开后的下一个宏任务里才挂上「点外面」的监听；左键按下要等随后的
      // click 才判定是不是点在外面——照真鼠标的顺序两下都发
      await new Promise((r) => setTimeout(r, 0))
      const init = { bubbles: true, cancelable: true, button: 0 }
      document.body.dispatchEvent(new PointerEvent('pointerdown', { ...init, pointerType: 'mouse' }))
      document.body.dispatchEvent(new MouseEvent('click', init))
    },
  }
  const open = async () => {
    await mount([meta()])
    await act(async () => previews()[0].click())
    await flush()
    expect($('[data-timeline-preview]')).not.toBeNull()
  }

  it.each(Object.keys(ways))('对照：没在恢复时，%s 能关掉预览', async (how) => {
    await open()
    await act(ways[how])
    await flush()
    expect(useTimelineStore.getState().preview).toBeNull()
  })

  it.each(Object.keys(ways))(
    '「恢复前」还在存时，%s 关不掉；存失败落定后照常能关',
    async (how) => {
      let fail!: (e: unknown) => void
      mockCreate.mockImplementationOnce(() => new Promise((_, reject) => (fail = reject)))
      await open()
      await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
      await flush()
      expect(mockCreate).toHaveBeenCalledTimes(1)
      expect($('[data-dialog="timeline-preview"]')!.getAttribute('aria-busy')).toBe('true')
      // 忙时右上角 × 收起（Dialog 的 busy 锁）
      expect($('[data-dialog="timeline-preview"] [data-dialog-close]')).toBeNull()

      await act(ways[how])
      await flush()
      expect(useTimelineStore.getState().preview).not.toBeNull()
      expect($('[data-timeline-preview]')).not.toBeNull()

      await act(async () => fail(new Error('disk full')))
      await flush()
      expect(ids()).toEqual(['now'])
      expect($('[data-dialog="timeline-preview"]')!.getAttribute('aria-busy')).toBeNull()
      await act(ways[how])
      await flush()
      expect(useTimelineStore.getState().preview).toBeNull()
    },
  )

  it('忙按上下文记账：A 的恢复还在飞时换了项目，新上下文里的预览照常能关', async () => {
    mockCreate.mockImplementationOnce(() => new Promise(() => {}))
    await open()
    await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
    await flush()
    expect($('[data-dialog="timeline-preview"]')!.getAttribute('aria-busy')).toBe('true')
    // 排版 id 相同的另一个项目：只有项目代际变了
    await act(async () => useTimelineStore.getState().clear())
    await flush()
    await act(async () => previews()[0].click())
    await flush()
    expect($('[data-timeline-preview]')).not.toBeNull()
    expect($('[data-dialog="timeline-preview"]')!.getAttribute('aria-busy')).toBeNull()
    await act(ways.Esc)
    await flush()
    expect(useTimelineStore.getState().preview).toBeNull()
  })

  it('「恢复前」存完之后照常恢复、预览退出', async () => {
    let done!: (v: Awaited<ReturnType<typeof createVersion>>) => void
    mockCreate.mockImplementationOnce(() => new Promise((resolve) => (done = resolve)))
    await open()
    await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
    await flush()
    await act(ways.Esc)
    await flush()
    expect(useTimelineStore.getState().preview).not.toBeNull()
    await act(async () => done({ version: meta({ id: 'v_backup' }) }))
    await flush()
    expect(ids()).toEqual(['old'])
    expect(useTimelineStore.getState().preview).toBeNull()
  })
})

/* ------------------------------ 分组与筛选 -------------------------------- */

describe('按天分组、类型标记、只看命名', () => {
  it('今天 / 昨天 / 更早的日期，各自一组，组内最新在上', async () => {
    await mount([
      meta({ id: 'old', ts: NOW - 5 * DAY }),
      meta({ id: 'y', ts: NOW - DAY }),
      meta({ id: 't1', ts: NOW - 120_000 }),
      meta({ id: 't2', ts: NOW - 60_000 }),
    ])
    const labels = days()
    expect(labels[0]).toBe('今天')
    expect(labels[1]).toBe('昨天')
    expect(labels).toHaveLength(3)
    expect(
      [...document.querySelectorAll('[data-timeline-node]')].map((n) =>
        n.getAttribute('data-timeline-node'),
      ),
    ).toEqual(['t2', 't1', 'y', 'old'])
  })

  it('每个节点带类型标记：命名 / 关键时刻（说是哪个时刻）/ 自动 / 手动', async () => {
    await mount([
      meta({ id: 'a', ts: NOW - 4000, kind: 'auto' }),
      meta({ id: 'm', ts: NOW - 3000, kind: 'moment', moment: 'export' }),
      meta({ id: 'h', ts: NOW - 2000, kind: 'manual', auto: false }),
      meta({ id: 'n', ts: NOW - 1000, kind: 'named', named: true, auto: false, name: '投稿前' }),
    ])
    const kind = (id: string) =>
      node(id).querySelector('[data-timeline-kind]')!.getAttribute('data-timeline-kind')
    expect(['a', 'm', 'h', 'n'].map(kind)).toEqual(['auto', 'moment', 'manual', 'named'])
    expect(node('m').textContent).toContain('导出')
    expect(node('n').getAttribute('data-timeline-named')).toBe('true')
    expect(node('n').textContent).toContain('投稿前')
  })

  it('老后端不发 kind：按 auto 推，不推成命名', async () => {
    await mount([meta({ kind: undefined, named: undefined, auto: false, name: '看着像名字' })])
    expect(node('v1').querySelector('[data-timeline-kind]')!.getAttribute('data-timeline-kind')).toBe(
      'manual',
    )
  })

  it('「命名」视图：只留命名节点，空组整组不出现', async () => {
    await mount([
      meta({ id: 'y', ts: NOW - DAY }),
      meta({ id: 'a', ts: NOW - 2000 }),
      meta({ id: 'n', ts: NOW - 1000, kind: 'named', named: true, auto: false, name: '投稿前' }),
    ])
    await act(async () => $<HTMLButtonElement>('[data-timeline-filter] [data-value="named"]')!.click())
    expect([...document.querySelectorAll('[data-timeline-node]')].map((n) => n.getAttribute('data-timeline-node'))).toEqual(['n'])
    expect(days()).toEqual(['今天'])
  })

  it('命名节点超出字节上限：照实说，不删', async () => {
    await mount(
      [meta({ kind: 'named', named: true, auto: false, name: '投稿前' })],
      { namedBytes: 26 << 20, limit: 24 << 20, namedOver: true },
    )
    const notice = $('[data-timeline-budget]')
    expect(notice?.textContent).toContain('26.0')
    expect(notice?.textContent).toContain('24')
    expect(rows()).toHaveLength(1)
  })
})

/* ------------------------------ 命名 -------------------------------------- */

describe('命名与改名', () => {
  it('抽屉刚打开：只有「给现在存个名字…」按钮，没有一直开着的输入框；点开才展开，并带说明', async () => {
    await mount([meta()], undefined, false)
    expect($('[data-timeline-name-input]')).toBeNull()
    const open = $<HTMLButtonElement>('[data-timeline-name-open]')!
    expect(open.textContent).toContain('给现在存个名字')
    await act(async () => open.click())
    await flush()
    await act(async () => {
      await new Promise((r) => requestAnimationFrame(() => r(null)))
    })
    expect($('[data-timeline-name-open]')).toBeNull()
    expect(document.activeElement).toBe($('[data-timeline-name-input]'))
    expect(document.querySelector('[data-timeline-drawer]')?.textContent).toContain('不会被自动清理')
  })

  it('Esc 只收起命名输入、不关抽屉', async () => {
    await mount([meta()])
    const input = $<HTMLInputElement>('[data-timeline-name-input]')!
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect($('[data-timeline-name-input]')).toBeNull()
    expect($('[data-timeline-name-open]')).not.toBeNull()
    expect(useUiStore.getState().versionsOpen).toBe(true)
  })

  it('抽屉关掉再打开：命名输入不记得上次展开', async () => {
    await mount([meta()])
    await act(async () => useUiStore.setState({ versionsOpen: false }))
    await act(async () => useUiStore.setState({ versionsOpen: true }))
    await flush()
    expect($('[data-timeline-name-input]')).toBeNull()
  })

  it('保存成功：输入收起', async () => {
    mockCreate.mockResolvedValueOnce({ version: meta({ id: 'v_named' }) })
    await mount([meta()])
    const input = $<HTMLInputElement>('[data-timeline-name-input]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, '投稿前')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
    await flush()
    expect($('[data-timeline-name-input]')).toBeNull()
  })

  describe('⌥⌘S / 命令面板：顶部就地小框（不开抽屉）', () => {
    const typeName = async (text: string) => {
      const input = $<HTMLInputElement>('[data-timeline-quick-name-input]')!
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      await act(async () => {
        setter.call(input, text)
        input.dispatchEvent(new Event('input', { bubbles: true }))
      })
      return input
    }
    const openQuick = async () => {
      await act(async () => useUiStore.setState({ versionsOpen: false }))
      await act(async () => startNamedNode())
      await flush()
    }

    it('小框出现并聚焦、带说明；抽屉不打开；回车保存后关闭', async () => {
      mockCreate.mockResolvedValueOnce({ version: meta({ id: 'v_named' }) })
      await mount([meta()], undefined, false)
      await openQuick()
      expect(useUiStore.getState().versionsOpen).toBe(false)
      expect($('[data-timeline-drawer]')).toBeNull()
      const input = $<HTMLInputElement>('[data-timeline-quick-name-input]')!
      expect(document.activeElement).toBe(input)
      expect($('[data-timeline-quick-name]')?.textContent).toContain('不会被自动清理')
      await typeName('投稿前')
      await act(async () => {
        input.form!.requestSubmit()
      })
      await flush()
      expect(mockCreate.mock.calls[0][1]).toMatchObject({ auto: false, named: true, name: '投稿前' })
      expect($('[data-timeline-quick-name]')).toBeNull()
      expect(useTimelineStore.getState().namingOpen).toBe(false)
      expect(useUiStore.getState().versionsOpen).toBe(false)
    })

    it('失败（超上限）：话写在小框里，小框不关、名字不丢', async () => {
      mockCreate.mockRejectedValueOnce(new Error('命名节点已经占满了'))
      await mount([meta()], undefined, false)
      await openQuick()
      const input = await typeName('再一个')
      await act(async () => {
        input.form!.requestSubmit()
      })
      await flush()
      expect(useTimelineStore.getState().namingOpen).toBe(true)
      expect($<HTMLInputElement>('[data-timeline-quick-name-input]')!.value).toBe('再一个')
      expect($('[data-timeline-quick-name] [role="alert"]')?.textContent).toContain('占满')
    })

    it('Esc 关闭；点外面关闭', async () => {
      await mount([meta()], undefined, false)
      await openQuick()
      await act(async () => {
        $('[data-timeline-quick-name-input]')!.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
        )
      })
      expect($('[data-timeline-quick-name]')).toBeNull()
      await openQuick()
      await act(async () => {
        document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
      })
      expect($('[data-timeline-quick-name]')).toBeNull()
    })

    it('关闭后焦点回到打开前的元素（Esc、保存成功两条路径）', async () => {
      mockCreate.mockResolvedValueOnce({ version: meta({ id: 'v_named' }) })
      await mount([meta()], undefined, false)
      const before = document.createElement('button')
      document.body.appendChild(before)
      before.focus()
      await openQuick()
      expect(document.activeElement).toBe($('[data-timeline-quick-name-input]'))
      await act(async () => {
        $('[data-timeline-quick-name-input]')!.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
        )
      })
      expect(document.activeElement).toBe(before)
      await act(async () => startNamedNode())
      await flush()
      const input = await typeName('投稿前')
      await act(async () => {
        input.form!.requestSubmit()
      })
      await flush()
      expect($('[data-timeline-quick-name]')).toBeNull()
      expect(document.activeElement).toBe(before)
    })

    it('点小框里面不关', async () => {
      await mount([meta()], undefined, false)
      await openQuick()
      await act(async () => {
        $('[data-timeline-quick-name-input]')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
      })
      expect($('[data-timeline-quick-name]')).not.toBeNull()
    })

    it('A 里存失败的那句话，换到 B 就不再挂着', async () => {
      mockCreate.mockRejectedValueOnce(new Error('命名节点已经占满了'))
      await mount([meta()], undefined, false)
      await openQuick()
      const input = await typeName('再一个')
      await act(async () => {
        input.form!.requestSubmit()
      })
      await flush()
      expect($('[data-timeline-quick-name] [role="alert"]')?.textContent).toContain('占满')
      await act(async () => {
        await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
      })
      await flush()
      expect($('[data-timeline-quick-name] [role="alert"]')).toBeNull()
    })
  })

  it('存失败（超上限）：话留在抽屉里，输入不收、名字不丢', async () => {
    mockCreate.mockRejectedValueOnce(new Error('命名节点已经占满了'))
    await mount([meta()])
    const input = $<HTMLInputElement>('[data-timeline-name-input]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, '再一个')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
    await flush()
    expect($<HTMLInputElement>('[data-timeline-name-input]')!.value).toBe('再一个')
    expect($('[data-timeline-error-kind="action"]')?.textContent).toContain('占满')
  })

  it('「存为命名节点」：带 named 与用户起的名字，拍的是当前画布', async () => {
    mockCreate.mockResolvedValueOnce({ version: meta({ id: 'v_named' }) })
    await mount([meta()])
    const input = $<HTMLInputElement>('[data-timeline-name-input]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, '投稿前')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
    await flush()
    expect(mockCreate).toHaveBeenCalledTimes(1)
    expect(mockCreate.mock.calls[0][1]).toMatchObject({
      auto: false,
      named: true,
      name: '投稿前',
      canvasId: CANVAS_ID,
    })
    expect(mockCreate.mock.calls[0][1].doc.objects.map((o) => o.id)).toEqual(['now'])
  })

  it('名字为空时按钮不可点：命名节点的名字只能是用户起的', async () => {
    await mount([meta()])
    expect($<HTMLButtonElement>('[data-timeline-save-named]')!.disabled).toBe(true)
  })

  it('双击节点 → 行内改名 → 回车提交 PATCH', async () => {
    await mount([meta()])
    await act(async () => {
      rows()[0].dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    })
    const input = $<HTMLInputElement>('[data-timeline-rename]')!
    expect(input).not.toBeNull()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, '初稿')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      input.blur()
    })
    await flush()
    expect(mockUpdate).toHaveBeenCalledWith('d_versions', 'v1', { name: '初稿' })
  })
})

/* ---------------------------- 交叠的重取（Codex P2） ---------------------------- */

describe('列表重取交叠：只认最新那一次', () => {
  it('晚到的旧响应不把新节点盖回去', async () => {
    let resolveOld!: (v: { versions: LayoutVersionMeta[] }) => void
    mockList.mockImplementationOnce(
      () => new Promise((r) => (resolveOld = r as typeof resolveOld)),
    )
    useUiStore.setState({ versionsOpen: true })
    const el = document.createElement('div')
    document.body.appendChild(el)
    root = createRoot(el)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <VersionDrawer />
        </TooltipProvider>,
      )
    })
    // 第一次重取还挂着；节点建好（bump）触发第二次，它先回来
    mockList.mockResolvedValueOnce({ versions: [meta({ id: 'old' }), meta({ id: 'new', ts: NOW })] })
    await act(async () => useTimelineStore.getState().bump())
    await flush()
    expect(rows()).toHaveLength(2)
    // 第一次这才回来，带的是**拍节点之前**的列表
    await act(async () => resolveOld({ versions: [meta({ id: 'old' })] }))
    await flush()
    expect(
      [...document.querySelectorAll('[data-timeline-node]')].map((n) => n.getAttribute('data-timeline-node')),
    ).toEqual(['new', 'old'])
  })

  it('换了项目之后才回来的响应不落地（项目代际）', async () => {
    setCurrentProjectId('p_A')
    let resolveOld!: (v: { versions: LayoutVersionMeta[] }) => void
    mockList.mockImplementationOnce(
      () => new Promise((r) => (resolveOld = r as typeof resolveOld)),
    )
    useUiStore.setState({ versionsOpen: true })
    const el = document.createElement('div')
    document.body.appendChild(el)
    root = createRoot(el)
    await act(async () => {
      root.render(
        <TooltipProvider>
          <VersionDrawer />
        </TooltipProvider>,
      )
    })
    // 发请求时在 p_A；回来之前项目换成了 p_B（这里只换项目、不触发新的重取，
    // 量的是 pj 这一条守卫本身，而不是「新的重取把序号推过去」）
    await act(async () => setCurrentProjectId('p_B'))
    await act(async () => resolveOld({ versions: [meta({ id: 'from_project_a' })] }))
    await flush()
    setCurrentProjectId(null)
    expect(document.querySelector('[data-timeline-node="from_project_a"]')).toBeNull()
  })

  it('已经换去看别的节点：前一个节点正文取失败，不报错', async () => {
    await mount([meta({ id: 'a' }), meta({ id: 'b', ts: NOW })])
    let rejectA!: (e: Error) => void
    mockDoc.mockImplementationOnce(() => new Promise((_, rej) => (rejectA = rej)))
    await act(async () => node('a').querySelector<HTMLButtonElement>('[data-timeline-preview-button]')!.click())
    await act(async () => node('b').querySelector<HTMLButtonElement>('[data-timeline-preview-button]')!.click())
    await flush()
    expect(useTimelineStore.getState().preview?.meta.id).toBe('b')
    await act(async () => rejectA(new Error('boom')))
    await flush()
    expect($('[data-timeline-error]')).toBeNull()
  })
})

/* ------------------------ 换上下文（Codex #679 P2） ------------------------ */

describe('换项目 / 换排版：旧上下文的列表当场不再显示', () => {
  type Listed = { versions: LayoutVersionMeta[] }
  /** 下一次列表请求挂起，返回放行它的函数 */
  const holdNextList = () => {
    let release!: (v: Listed) => void
    mockList.mockImplementationOnce(() => new Promise((r) => (release = r as typeof release)))
    return (v: Listed) => act(async () => release(v))
  }
  const switchDoc = () =>
    act(async () => {
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    })
  const nodeIds = () =>
    [...document.querySelectorAll('[data-timeline-node]')].map((n) => n.getAttribute('data-timeline-node'))
  const openMenuOf = async (id: string) => {
    await act(async () => {
      node(id)
        .querySelector('[data-timeline-more]')!
        .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
    })
    await flush()
  }
  const menuItem = (label: string) =>
    [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((m) => m.textContent === label)!

  it('换排版：A 的节点当场消失、显示「正在读取」；B 的列表到了才显示 B', async () => {
    await mount([meta({ id: 'a1' })])
    expect(nodeIds()).toEqual(['a1'])
    const releaseB = holdNextList()
    await switchDoc()
    await flush()
    expect(nodeIds()).toEqual([])
    expect($('[data-timeline-loading]')).not.toBeNull()
    expect(mockList).toHaveBeenLastCalledWith('d_other', expect.anything())
    await releaseB({ versions: [meta({ id: 'b1' })] })
    await flush()
    expect(nodeIds()).toEqual(['b1'])
    expect($('[data-timeline-loading]')).toBeNull()
  })

  it('换项目（排版 id 恰好相同）：一样当场清空，等新项目的列表', async () => {
    await mount([meta({ id: 'a1' })])
    const releaseB = holdNextList()
    await act(async () => useTimelineStore.getState().clear())
    await flush()
    expect(nodeIds()).toEqual([])
    expect($('[data-timeline-loading]')).not.toBeNull()
    await releaseB({ versions: [meta({ id: 'b1' })] })
    await flush()
    expect(nodeIds()).toEqual(['b1'])
  })

  it('新上下文第一次就取失败：说出错误，不退回显示 A 的列表', async () => {
    await mount([meta({ id: 'a1' })])
    mockList.mockRejectedValueOnce(new Error('后端没回应'))
    await switchDoc()
    await flush()
    expect(nodeIds()).toEqual([])
    expect($('[data-timeline-error]')?.textContent).toContain('后端没回应')
    expect($('[data-timeline-loading]')).toBeNull()
  })

  it('对照：同一上下文里重取失败，旧列表留着、错误照说', async () => {
    await mount([meta({ id: 'a1' })])
    mockList.mockRejectedValueOnce(new Error('后端没回应'))
    await act(async () => useTimelineStore.getState().bump())
    await flush()
    expect(nodeIds()).toEqual(['a1'])
    expect($('[data-timeline-error]')?.textContent).toContain('后端没回应')
  })

  it('A 的错误不挂到 B 下面', async () => {
    await mount([meta({ id: 'a1' })])
    mockList.mockRejectedValueOnce(new Error('后端没回应'))
    await act(async () => useTimelineStore.getState().bump())
    await flush()
    expect($('[data-timeline-error]')).not.toBeNull()
    holdNextList() // B 的列表还没到：没有一次成功的重取替它把错误清掉
    await switchDoc()
    await flush()
    expect($('[data-timeline-error]')).toBeNull()
  })

  it('换排版时别的排版的预览一并退出', async () => {
    await mount([meta({ id: 'a1' })])
    await act(async () => previews()[0].click())
    await flush()
    expect(useTimelineStore.getState().preview?.meta.id).toBe('a1')
    await switchDoc()
    await flush()
    expect(useTimelineStore.getState().preview).toBeNull()
  })

  it('A 里的行操作换了排版才做完：它的重取不发，也不挡住 B 的列表', async () => {
    await mount([meta({ id: 'a1' })])
    let finishDup!: () => void
    vi.mocked(duplicateVersion).mockImplementationOnce(
      () => new Promise((r) => (finishDup = () => r({ version: meta({ id: 'a2' }) }))),
    )
    await openMenuOf('a1')
    await act(async () => menuItem('复制节点').click())
    await flush()
    expect(duplicateVersion).toHaveBeenCalledWith('d_versions', 'a1')
    const releaseB = holdNextList()
    await switchDoc()
    await flush()
    const callsBefore = mockList.mock.calls.length
    await act(async () => finishDup())
    await flush()
    // 旧闭包的 onChanged 不再拿 A 的排版 id 去重取（那一次会把 B 在飞的请求判成过期）
    expect(mockList.mock.calls.length).toBe(callsBefore)
    await releaseB({ versions: [meta({ id: 'b1' })] })
    await flush()
    expect(nodeIds()).toEqual(['b1'])
  })

  it('确认删除的时候换了排版：确认之后一个请求都不发', async () => {
    await mount([meta({ id: 'a1' })])
    await openMenuOf('a1')
    await act(async () => menuItem('删除节点').click())
    await flush()
    const confirm = useUiStore.getState().confirm
    expect(confirm).not.toBeNull()
    await switchDoc()
    await flush()
    await act(async () => confirm!.resolve(true))
    await flush()
    expect(deleteVersion).not.toHaveBeenCalled()
  })

  it('命名输入：A 里存失败的那句话，换到 B 就不再挂着', async () => {
    mockCreate.mockRejectedValueOnce(new Error('命名节点已经占满了'))
    await mount([meta()])
    const input = $<HTMLInputElement>('[data-timeline-name-input]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, '再一个')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
    await flush()
    expect($('[data-timeline-error-kind="action"]')?.textContent).toContain('占满')
    await switchDoc()
    await flush()
    expect($('[data-timeline-error-kind="action"]')).toBeNull()
  })
})

/* ------------------- 换走之后才回来（Codex #679，afterAwait） ------------------- */

describe('A 里发起、换到 B 之后才完成：不改 B 的任何本地状态（清单见 lib/timelineContext）', () => {
  type Settle = () => Promise<void>
  const pending = <T,>() => {
    let resolve!: (v: T) => void
    let reject!: (e: Error) => void
    const promise = new Promise<T>((res, rej) => {
      resolve = res
      reject = rej
    })
    return { promise, resolve, reject }
  }
  const setValue = async (input: HTMLInputElement, value: string) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  const drawerName = () => $<HTMLInputElement>('[data-timeline-name-input]')!
  const spinning = (sel: string) => $(sel)?.querySelector('.animate-spin') != null
  const openMenuItem = async (id: string, label: string) => {
    await act(async () => {
      node(id)
        .querySelector('[data-timeline-more]')!
        .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
    })
    await flush()
    const item = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
      (m) => m.textContent === label,
    )!
    await act(async () => item.click())
    await flush()
  }
  const openPreviewAndRestore = async () => {
    await act(async () => previews()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
    await flush()
  }
  const cases: {
    name: string
    nodes?: LayoutVersionMeta[]
    /** 在 A 里发起，返回「让它完成」 */
    start: () => Promise<Settle>
    /** 已经换到 B、A 还没完成时（例如 B 里开始敲名字） */
    during?: () => Promise<void>
    check: () => void
  }[] = [
    {
      name: '抽屉「存为命名节点」成功：B 名字框里敲的名字不被清掉，B 不显示在忙',
      start: async () => {
        const create = pending<{ version: LayoutVersionMeta }>()
        mockCreate.mockImplementationOnce(() => create.promise)
        await setValue(drawerName(), 'A 的名字')
        await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
        await flush()
        return async () => act(async () => create.resolve({ version: meta({ id: 'a2' }) }))
      },
      during: async () => {
        expect(spinning('[data-timeline-save-named]')).toBe(false)
        await setValue(drawerName(), 'B 的名字')
      },
      check: () => {
        expect(drawerName().value).toBe('B 的名字')
        expect(spinning('[data-timeline-save-named]')).toBe(false)
      },
    },
    {
      name: '抽屉「存为命名节点」失败：错误不挂到 B',
      start: async () => {
        const create = pending<{ version: LayoutVersionMeta }>()
        mockCreate.mockImplementationOnce(() => create.promise)
        await setValue(drawerName(), 'A 的名字')
        await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
        await flush()
        return async () => act(async () => create.reject(new Error('命名节点已经占满了')))
      },
      check: () => expect($('[data-timeline-error]')).toBeNull(),
    },
    {
      name: '行内改名失败：错误不挂到 B',
      start: async () => {
        const upd = pending<{ version: LayoutVersionMeta }>()
        mockUpdate.mockImplementationOnce(() => upd.promise)
        await act(async () => {
          rows()[0].dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
        })
        const input = $<HTMLInputElement>('[data-timeline-rename]')!
        await setValue(input, '初稿')
        await act(async () => input.blur())
        await flush()
        expect(mockUpdate).toHaveBeenCalledWith('d_versions', 'a1', { name: '初稿' })
        return async () => act(async () => upd.reject(new Error('改名失败了')))
      },
      check: () => expect($('[data-timeline-error]')).toBeNull(),
    },
    {
      name: '复制失败：错误不挂到 B',
      start: async () => {
        const dup = pending<{ version: LayoutVersionMeta }>()
        vi.mocked(duplicateVersion).mockImplementationOnce(() => dup.promise)
        await openMenuItem('a1', '复制节点')
        return async () => act(async () => dup.reject(new Error('复制失败了')))
      },
      check: () => expect($('[data-timeline-error]')).toBeNull(),
    },
    {
      name: '删除：确认框开着时换了排版，确认之后不发删除',
      start: async () => {
        await openMenuItem('a1', '删除节点')
        const confirm = useUiStore.getState().confirm
        expect(confirm).not.toBeNull()
        return async () => act(async () => confirm!.resolve(true))
      },
      check: () => expect(deleteVersion).not.toHaveBeenCalled(),
    },
    {
      name: '恢复：「恢复前」节点存完才换走——不把 A 的节点写进 B',
      start: async () => {
        const create = pending<{ version: LayoutVersionMeta }>()
        mockCreate.mockImplementationOnce(() => create.promise)
        await openPreviewAndRestore()
        expect(mockCreate).toHaveBeenCalledTimes(1)
        return async () => act(async () => create.resolve({ version: meta({ id: 'v_backup' }) }))
      },
      check: () => {
        expect(ids()).toEqual([])
        expect(useUiStore.getState().status).toBeNull()
      },
    },
    {
      name: '恢复：「恢复前」节点存失败才回来——状态条不报 A 的失败',
      start: async () => {
        const create = pending<{ version: LayoutVersionMeta }>()
        mockCreate.mockImplementationOnce(() => create.promise)
        await openPreviewAndRestore()
        return async () => act(async () => create.reject(new Error('存不下')))
      },
      check: () => expect(useUiStore.getState().status).toBeNull(),
    },
    {
      name: '恢复：跨画布的确认框开着时换走——确认之后不存、不写',
      nodes: [meta({ id: 'a1', canvasId: 'c_gone', canvasName: '已删的画布' })],
      start: async () => {
        await openPreviewAndRestore()
        const confirm = useUiStore.getState().confirm
        expect(confirm).not.toBeNull()
        return async () => act(async () => confirm!.resolve(true))
      },
      check: () => {
        expect(mockCreate).not.toHaveBeenCalled()
        expect(ids()).toEqual([])
      },
    },
    {
      name: '抽屉「存为命名节点」成功：不收起 B 里开着的命名输入',
      start: async () => {
        const create = pending<{ version: LayoutVersionMeta }>()
        mockCreate.mockImplementationOnce(() => create.promise)
        await setValue(drawerName(), 'A 的名字')
        await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
        await flush()
        return async () => act(async () => create.resolve({ version: meta({ id: 'a2' }) }))
      },
      check: () => {
        expect($('[data-timeline-name-input]')).not.toBeNull()
      },
    },
  ]

  it('A 的保存在 B 里完成、再切回 A：A 不一直显示在忙（忙标记只摘自己的）', async () => {
    await mount([meta({ id: 'a1' })])
    const create = pending<{ version: LayoutVersionMeta }>()
    mockCreate.mockImplementationOnce(() => create.promise)
    await setValue(drawerName(), 'A 的名字')
    await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
    await flush()
    expect(spinning('[data-timeline-save-named]')).toBe(true)
    await act(async () => {
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    })
    await act(async () => create.resolve({ version: meta({ id: 'a2' }) }))
    await flush()
    // 切回 A（同一项目、同一排版 id = 同一个上下文）
    await act(async () => {
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_versions')
    })
    await flush()
    expect(spinning('[data-timeline-save-named]')).toBe(false)
  })

  it.each(cases)('$name', async ({ nodes, start, during, check }) => {
    await mount(nodes ?? [meta({ id: 'a1' })])
    const settle = await start()
    useUiStore.setState({ status: null })
    mockList.mockResolvedValue({ versions: [meta({ id: 'b1' })] })
    await act(async () => {
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    })
    await flush()
    expect(document.querySelector('[data-timeline-node="b1"]')).not.toBeNull()
    await during?.()
    const listCalls = mockList.mock.calls.length
    await settle()
    await flush()
    check()
    // 共同的一条：旧上下文的完成不拿 A 的排版 id 去重取（节点建好 bump 出来的重取属于
    // B、取的是 B，那是服务端事实经列表反映，照常）
    expect(mockList.mock.calls.slice(listCalls).map((c) => c[0])).not.toContain('d_versions')
    expect(document.querySelector('[data-timeline-node="b1"]')).not.toBeNull()
  })
})

/* ------------------- 错误槽：旧上下文的完成不顶掉 B 的错误（Codex #679） ------------------- */

describe('A 在飞 → B 自己出错 → A 才完成：B 的错误留着', () => {
  const pending = <T,>() => {
    let resolve!: (v: T) => void
    let reject!: (e: Error) => void
    const promise = new Promise<T>((res, rej) => {
      resolve = res
      reject = rej
    })
    return { promise, resolve, reject }
  }
  const setValue = async (input: HTMLInputElement, value: string) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  const duplicateInA = async () => {
    const dup = pending<{ version: LayoutVersionMeta }>()
    vi.mocked(duplicateVersion).mockImplementationOnce(() => dup.promise)
    await act(async () => {
      node('a1')
        .querySelector('[data-timeline-more]')!
        .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
    })
    await flush()
    const item = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
      (m) => m.textContent === '复制节点',
    )!
    await act(async () => item.click())
    await flush()
    return dup
  }
  const saveNamedInA = async () => {
    const create = pending<{ version: LayoutVersionMeta }>()
    mockCreate.mockImplementationOnce(() => create.promise)
    await setValue($<HTMLInputElement>('[data-timeline-name-input]')!, 'A 的名字')
    await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
    await flush()
    return create
  }
  const cases: { name: string; start: () => Promise<{ finish: () => void }> }[] = [
    {
      name: '复制成功（完成时清错误）',
      start: async () => {
        const dup = await duplicateInA()
        return { finish: () => dup.resolve({ version: meta({ id: 'a2' }) }) }
      },
    },
    {
      name: '复制失败（完成时写 A 的错误）',
      start: async () => {
        const dup = await duplicateInA()
        return { finish: () => dup.reject(new Error('A 的复制失败')) }
      },
    },
    {
      name: '存为命名节点失败',
      start: async () => {
        const create = await saveNamedInA()
        return { finish: () => create.reject(new Error('A 的命名失败')) }
      },
    },
  ]

  it.each(cases)('$name', async ({ start }) => {
    await mount([meta({ id: 'a1' })])
    const { finish } = await start()
    mockList.mockRejectedValueOnce(new Error('B 的时间线读不出来'))
    await act(async () => {
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    })
    await flush()
    expect($('[data-timeline-error]')?.textContent).toContain('B 的时间线读不出来')
    await act(async () => finish())
    await flush()
    expect($('[data-timeline-error]')?.textContent).toContain('B 的时间线读不出来')
    expect($('[data-timeline-loading]')).toBeNull()
  })

  it('命名输入：A 的保存晚失败，不顶掉 B 里那一次保存的错误', async () => {
    await mount([meta({ id: 'a1' })])
    const createA = pending<{ version: LayoutVersionMeta }>()
    mockCreate.mockImplementationOnce(() => createA.promise)
    const name = () => $<HTMLInputElement>('[data-timeline-name-input]')!
    const save = () => act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
    await setValue(name(), 'A 的名字')
    await save()
    await flush()
    await act(async () => {
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    })
    await flush()
    mockCreate.mockRejectedValueOnce(new Error('B 的命名失败'))
    await setValue(name(), 'B 的名字')
    await save()
    await flush()
    const alert = () => $('[data-timeline-error-kind="action"]')?.textContent
    expect(alert()).toContain('B 的命名失败')
    await act(async () => createA.reject(new Error('A 的命名失败')))
    await flush()
    expect(alert()).toContain('B 的命名失败')
  })
})

/* --------------------- 正在编辑的输入按上下文作用域（Codex #679） --------------------- */

describe('换项目 / 换排版：用户正在编辑的草稿与确认框当场作废', () => {
  const setValue = async (input: HTMLInputElement, value: string) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  const switches = {
    换排版: () =>
      act(async () => {
        await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
      }),
    // 排版 id 恰好相同的另一个项目：只有项目代际变了
    换项目: () => act(async () => useTimelineStore.getState().clear()),
  }
  const drafts: {
    name: string
    nodes?: LayoutVersionMeta[]
    start: () => Promise<void>
    check: () => Promise<void>
  }[] = [
    {
      name: '抽屉名字框',
      start: async () => {
        await setValue($<HTMLInputElement>('[data-timeline-name-input]')!, 'A 的草稿')
      },
      check: async () => {
        expect($<HTMLInputElement>('[data-timeline-name-input]')!.value).toBe('')
        await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
        await flush()
        expect(mockCreate).not.toHaveBeenCalled()
      },
    },
    {
      name: '行内改名框（B 里恰好有同 id 的节点）',
      start: async () => {
        await act(async () => {
          rows()[0].dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
        })
        await setValue($<HTMLInputElement>('[data-timeline-rename]')!, 'A 的改名')
      },
      check: async () => {
        expect(document.querySelector('[data-timeline-node="a1"]')).not.toBeNull()
        expect($('[data-timeline-rename]')).toBeNull()
        expect(mockUpdate).not.toHaveBeenCalled()
      },
    },
    {
      name: '删除节点的确认框',
      start: async () => {
        await act(async () => {
          node('a1')
            .querySelector('[data-timeline-more]')!
            .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
        })
        await flush()
        const item = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
          (m) => m.textContent === '删除节点',
        )!
        await act(async () => item.click())
        await flush()
        expect(useUiStore.getState().confirm).not.toBeNull()
      },
      check: async () => {
        expect(useUiStore.getState().confirm).toBeNull()
        expect(deleteVersion).not.toHaveBeenCalled()
      },
    },
    {
      name: '跨画布恢复的确认框',
      nodes: [meta({ id: 'a1', canvasId: 'c_gone', canvasName: '已删的画布' })],
      start: async () => {
        await act(async () => previews()[0].click())
        await flush()
        await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
        await flush()
        expect(useUiStore.getState().confirm).not.toBeNull()
      },
      check: async () => {
        expect(useUiStore.getState().confirm).toBeNull()
        expect(mockCreate).not.toHaveBeenCalled()
      },
    },
  ]
  const cases = drafts.flatMap((d) =>
    (Object.keys(switches) as (keyof typeof switches)[]).map((how) => ({ ...d, how })),
  )

  it.each(cases)('$how：$name', async ({ how, nodes, start, check }) => {
    await mount(nodes ?? [meta({ id: 'a1' })])
    await start()
    mockList.mockResolvedValue({ versions: [meta({ id: 'a1' })] })
    await switches[how]()
    await flush()
    await check()
  })
})

/* ------------- 操作失败的话不被紧跟着的刷新清掉（Codex #679：错误分两槽） ------------- */

describe('行操作失败：错误留在抽屉里，紧跟着的列表刷新不清掉它', () => {
  const setValue = async (input: HTMLInputElement, value: string) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  const menu = async (id: string, label: string) => {
    await act(async () => {
      node(id)
        .querySelector('[data-timeline-more]')!
        .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
    })
    await flush()
    const item = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
      (m) => m.textContent === label,
    )!
    await act(async () => item.click())
    await flush()
  }
  const budgetFull = () =>
    new ApiError('x', 409, { code: 'named_budget_exceeded', params: { used: '25.0', limit: '24' } })

  const ops: { name: string; fail: () => void; run: () => Promise<void> }[] = [
    {
      name: '复制命名节点（命名节点已满）',
      fail: () => vi.mocked(duplicateVersion).mockRejectedValueOnce(budgetFull()),
      run: () => menu('n1', '复制节点'),
    },
    {
      name: '改名',
      fail: () => mockUpdate.mockRejectedValueOnce(budgetFull()),
      run: async () => {
        await act(async () => {
          node('n1').querySelector('[data-timeline-row]')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
        })
        const input = $<HTMLInputElement>('[data-timeline-rename]')!
        await setValue(input, '新名字')
        await act(async () => input.blur())
        await flush()
      },
    },
    {
      name: '删除名字',
      fail: () => mockUpdate.mockRejectedValueOnce(new Error('删不掉名字')),
      run: () => menu('n1', '删除名字'),
    },
    {
      name: '删除节点',
      fail: () => vi.mocked(deleteVersion).mockRejectedValueOnce(new Error('删不掉节点')),
      run: async () => {
        await menu('n1', '删除节点')
        await act(async () => useUiStore.getState().confirm!.resolve(true))
        await flush()
      },
    },
  ]

  it.each(ops)('$name', async ({ fail, run }) => {
    await mount([meta({ id: 'n1', named: true, kind: 'named', name: '投稿前', auto: false })])
    const listCalls = mockList.mock.calls.length
    fail()
    await run()
    // 失败之后照常刷新了一次列表（服务器那边可能已经变了）……
    expect(mockList.mock.calls.length).toBeGreaterThan(listCalls)
    // ……而刷新成功没有把操作的错误清掉
    const err = $('[data-timeline-error] [data-timeline-error-kind="action"]')
    expect(err).not.toBeNull()
    expect(err!.textContent!.length).toBeGreaterThan(0)
  })

  it('命名节点已满说的是那句话本身；再刷新一次也还在', async () => {
    await mount([meta({ id: 'n1', named: true, kind: 'named', name: '投稿前', auto: false })])
    vi.mocked(duplicateVersion).mockRejectedValueOnce(budgetFull())
    await menu('n1', '复制节点')
    const text = () => $('[data-timeline-error-kind="action"]')?.textContent ?? ''
    expect(text()).toContain('24')
    await act(async () => useTimelineStore.getState().bump())
    await flush()
    expect(text()).toContain('24')
  })

  it('对照：下一次操作成功就清掉操作的错误', async () => {
    await mount([meta({ id: 'n1', named: true, kind: 'named', name: '投稿前', auto: false })])
    vi.mocked(duplicateVersion).mockRejectedValueOnce(budgetFull())
    await menu('n1', '复制节点')
    expect($('[data-timeline-error-kind="action"]')).not.toBeNull()
    vi.mocked(duplicateVersion).mockResolvedValueOnce({ version: meta({ id: 'n2' }) })
    await menu('n1', '复制节点')
    expect($('[data-timeline-error-kind="action"]')).toBeNull()
  })

  it('对照：列表读失败后下一次读成功，读列表的错误照常清掉', async () => {
    await mount([meta({ id: 'n1' })])
    mockList.mockRejectedValueOnce(new Error('列表读不出来'))
    await act(async () => useTimelineStore.getState().bump())
    await flush()
    expect($('[data-timeline-error-kind="load"]')?.textContent).toContain('列表读不出来')
    await act(async () => useTimelineStore.getState().bump())
    await flush()
    expect($('[data-timeline-error-kind="load"]')).toBeNull()
  })
})

/* ----------------- 行上的显式入口：单击只选中，双击改名不被预览抢先 ----------------- */

describe('行：单击选中、「预览」钮开预览、双击或「改名」钮改名（Codex #679）', () => {
  it('单击行只选中（露出本行的钮），不开模态', async () => {
    await mount([meta({ id: 'a1' })])
    await act(async () => rows()[0].click())
    await flush()
    expect(useTimelineStore.getState().preview).toBeNull()
    expect(rows()[0].getAttribute('aria-pressed')).toBe('true')
    expect(mockDoc).not.toHaveBeenCalled()
  })

  it('真实双击的事件序列（click → click → dblclick）进入改名，预览不抢先', async () => {
    await mount([meta({ id: 'a1' })])
    const row = rows()[0]
    await act(async () => {
      row.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }))
      row.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 2 }))
      row.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, detail: 2 }))
    })
    await flush()
    expect(useTimelineStore.getState().preview).toBeNull()
    expect($('[data-timeline-rename]')).not.toBeNull()
  })

  it('一行的动作只有一颗 ⋯（预览 / 改名 / 复制 / 删除）；选中行下面一条「预览 · 恢复到这里」', async () => {
    await mount([meta({ id: 'a1' })])
    const row = node('a1')
    // 行里不再有单独的改名图标；⋯ 有可达名
    expect(row.querySelector('button[data-timeline-rename-button]')).toBeNull()
    const more = row.querySelector<HTMLButtonElement>('[data-timeline-more]')!
    expect(more.getAttribute('aria-label')).toBeTruthy()
    await act(async () => {
      more.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
    })
    await flush()
    expect($('[data-timeline-menu-preview]')).not.toBeNull()
    expect($('[data-timeline-delete]')!.className).toContain('text-danger')
    await act(async () => $<HTMLElement>('[data-timeline-rename-button]')!.click())
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10))
    })
    expect($('[data-timeline-rename]')).not.toBeNull()
    expect(document.activeElement).toBe($('[data-timeline-rename]'))
    await act(async () => $<HTMLInputElement>('[data-timeline-rename]')!.blur())
    // 选中这一行：内联条露出来（data 锚点在每一行上，靠 CSS 收起 / 露出）
    const bar = row.querySelector('[data-timeline-row-actions]')!
    expect(bar.className).toContain('hidden')
    await act(async () => row.querySelector<HTMLButtonElement>('[data-timeline-row]')!.click())
    expect(bar.className).not.toContain('hidden')
    await act(async () => bar.querySelector<HTMLButtonElement>('[data-timeline-preview-button]')!.click())
    await flush()
    expect(useTimelineStore.getState().preview?.meta.id).toBe('a1')
  })
})

describe('预览取正文失败：下一次预览把这句话作废（Codex #679）', () => {
  it('A 取失败 → B 预览成功 → 关闭：抽屉里没有 A 的旧错', async () => {
    await mount([meta({ id: 'a' }), meta({ id: 'b', ts: NOW })])
    mockDoc.mockRejectedValueOnce(new Error('A 的正文取不回来'))
    await act(async () => node('a').querySelector<HTMLButtonElement>('[data-timeline-preview-button]')!.click())
    await flush()
    expect($('[data-timeline-error-kind="preview"]')?.textContent).toContain('A 的正文取不回来')
    await act(async () => node('b').querySelector<HTMLButtonElement>('[data-timeline-preview-button]')!.click())
    await flush()
    expect(useTimelineStore.getState().preview?.meta.id).toBe('b')
    await act(async () => useTimelineStore.getState().setPreview(null))
    await flush()
    expect($('[data-timeline-error]')).toBeNull()
  })

  it('对照：预览错误不碰操作槽——复制失败的话，开一次预览之后仍在', async () => {
    await mount([meta({ id: 'a' })])
    vi.mocked(duplicateVersion).mockRejectedValueOnce(new Error('复制失败了'))
    await act(async () => {
      node('a')
        .querySelector('[data-timeline-more]')!
        .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
    })
    await flush()
    const item = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
      (m) => m.textContent === '复制节点',
    )!
    await act(async () => item.click())
    await flush()
    await act(async () => node('a').querySelector<HTMLButtonElement>('[data-timeline-preview-button]')!.click())
    await flush()
    expect($('[data-timeline-error-kind="action"]')?.textContent).toContain('复制失败了')
  })
})

/* ----------------------- 命名请求在途时不重复提交（Codex #679） ----------------------- */

describe('存为命名节点：第一次请求还没回来，再回车 / 再提交都不再发', () => {
  const setValue = async (input: HTMLInputElement, value: string) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }

  it('抽屉的名字框：连按两次回车只发一次 POST', async () => {
    await mount([meta({ id: 'a1' })])
    let release!: (v: { version: LayoutVersionMeta }) => void
    mockCreate.mockImplementationOnce(() => new Promise((r) => (release = r)))
    const input = $<HTMLInputElement>('[data-timeline-name-input]')!
    await setValue(input, '投稿前')
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    await act(async () => $<HTMLButtonElement>('[data-timeline-save-named]')!.click())
    expect(mockCreate).toHaveBeenCalledTimes(1)
    await act(async () => release({ version: meta({ id: 'n1' }) }))
    await flush()
    expect(mockCreate).toHaveBeenCalledTimes(1)
  })
})

describe('恢复确认框里的「当前画布」名字取活文档（Codex #679）', () => {
  it('当前画布刚改名：确认框说的是新名字', async () => {
    await mount([meta({ id: 'a1', canvasId: 'c_gone', canvasName: '已删的画布' })])
    const st = useDocumentStore.getState()
    await act(async () => st.renameCanvas(st.activeCanvasId, '刚改的名字'))
    await act(async () => previews()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
    await flush()
    const confirm = useUiStore.getState().confirm
    expect(confirm).not.toBeNull()
    expect(formatMessage(confirm!.body)).toContain('刚改的名字')
    await act(async () => confirm!.resolve(false))
  })
})

/* ------------------------------ 每行说什么 -------------------------------- */

describe('列表每行说什么', () => {
  it('时间 + 变了什么；后端按时间生成的名字不再重复一遍', async () => {
    await mount([
      meta({ id: 'v1', objects: 3, ts: NOW - 2000 }),
      meta({ id: 'v2', objects: 5, ts: NOW - 1000 }),
    ])
    const [newest, oldest] = rows().map((r) => r.textContent ?? '')
    expect(newest).toContain('新增 2 个对象')
    expect(oldest).toContain('3')
    expect(newest.match(/09-06 21:30/g) ?? []).toHaveLength(0)
  })
})

/* --------------------------- 列表里的缩略图 -------------------------------- */

const sketch = (fileId: string, label: string) => ({
  page: { w: 150, h: 100 },
  objects: [
    { type: 'panel', x: 2, y: 2, w: 40, h: 30, fileId, fileKind: 'pdf' },
    { type: 'text', x: 4, y: 40, w: 30, h: 6, text: label },
  ],
})

const thumbs = () => [...document.querySelectorAll('[data-canvas-thumb]')] as SVGElement[]

describe('每行一张缩略图', () => {
  it('有位图缩略图的节点用它（那一刻带 overrides 的样子），没有的退回草图', async () => {
    await mount([
      meta({ id: 'v1', ts: NOW - 2000, sketch: sketch('a.pdf', '第一版') }),
      meta({ id: 'v2', ts: NOW - 1000, thumb: 'webp', sketch: sketch('b.pdf', '第二版') }),
    ])
    const img = node('v2').querySelector('img[data-timeline-thumb]')!
    expect(img.getAttribute('src')).toContain('/api/versions/d_versions/v2/thumb')
    expect(node('v2').querySelector('[data-canvas-thumb]')).toBeNull()
    expect(node('v1').querySelector('[data-canvas-thumb]')).not.toBeNull()
  })

  it('打开面板一份正文都不拉：草图跟着列表一次回来', async () => {
    await mount([
      meta({ id: 'v1', ts: NOW - 3000, sketch: sketch('a.pdf', '一') }),
      meta({ id: 'v2', ts: NOW - 2000, sketch: sketch('b.pdf', '二') }),
      meta({ id: 'v3', ts: NOW - 1000, sketch: sketch('c.pdf', '三') }),
    ])
    expect(thumbs()).toHaveLength(3)
    expect(mockDoc).not.toHaveBeenCalled()
    expect(mockList).toHaveBeenCalledTimes(1)
    await act(async () => previews()[0].click())
    expect(mockDoc).toHaveBeenCalledTimes(1)
  })

  it('要多大的草图由缩略图组件说了算，随请求发给后端', async () => {
    await mount([meta({ sketch: sketch('a.pdf', '一') })])
    expect(mockList).toHaveBeenCalledWith('d_versions', {
      objects: THUMB_OBJECT_LIMIT,
      textChars: THUMB_TEXT_CHARS,
    })
  })

  it('画不出来的节点留同尺寸占位，不画一张比例是编的图', async () => {
    await mount([meta({ id: 'v_old', sketch: undefined, canvasId: 'c_other', canvasName: 'Fig 2' })])
    expect(thumbs()).toHaveLength(0)
    const placeholder = rows()[0].querySelector('span[aria-hidden]')
    expect(placeholder?.className).toContain('border-dashed')
    expect(rows()[0].textContent).toContain('Fig 2')
  })
})

describe('来自哪张画布：只在它能区分什么的时候才说', () => {
  it('单画布项目、节点就来自当前画布：不再每行重复同一个画布名', async () => {
    await mount([meta()])
    expect(rows()[0].textContent).not.toContain('Fig 1')
  })

  it('节点来自另一张画布：照说', async () => {
    await mount([meta({ canvasId: 'c_other', canvasName: 'Fig 2' })])
    expect(rows()[0].textContent).toContain('Fig 2')
  })

  it('旧节点不知道自己来自哪张画布：照实说，不猜成当前画布', async () => {
    await mount([meta({ canvasId: undefined, canvasName: undefined })])
    expect(rows()[0].textContent).toContain('画布未知')
    expect(rows()[0].textContent).not.toContain('Fig 1')
  })

  it('项目里不止一张画布：每个节点都说自己来自哪张', async () => {
    const pd = useDocumentStore.getState()
    await act(async () => {
      useDocumentStore.setState({
        canvases: [...pd.canvases, { ...pd.canvases[0], id: 'c_two', name: 'Fig 2' }],
      })
    })
    await mount([meta()])
    expect(rows()[0].textContent).toContain('Fig 1')
  })
})
