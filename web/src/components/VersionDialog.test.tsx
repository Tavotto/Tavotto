/**
 * 排版时间线抽屉（审计 T04 → ADR 0101）。
 *
 * 守的事：
 *
 * 1. **恢复之前先把当前状态存成「恢复前」节点**，而且它必须在写入**之前**落
 *    （写完再存等于存下来的已经是恢复后的内容）；存不下来就不恢复。恢复是一次
 *    commit，⌘Z 一步退回。
 * 2. **预览不改当前排版**：点一个节点只是在画布上盖一层只读的那一刻，文档、
 *    历史一个字节都不动。
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
  fetchTimeline: vi.fn(),
  fetchVersionDoc: vi.fn(),
  updateVersion: vi.fn(),
  putVersionThumb: vi.fn(),
}))
// 缩略图合成在 jsdom 里没有 canvas；这里只关心节点有没有拍、拍的是什么
vi.mock('@/lib/timelineThumb', () => ({ composeTimelineThumb: vi.fn(async () => null) }))

import {
  createVersion,
  fetchTimeline,
  fetchVersionDoc,
  updateVersion,
  type LayoutVersionMeta,
  type TimelineBudget,
} from '@/lib/api'
import { TimelinePreview, VersionDrawer } from '@/components/VersionDialog'
import { THUMB_OBJECT_LIMIT, THUMB_TEXT_CHARS } from '@/components/CanvasThumb'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useTimelineStore } from '@/store/timelineStore'
import { useUiStore } from '@/store/uiStore'
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
async function mount(list: LayoutVersionMeta[], budget?: TimelineBudget) {
  mockList.mockResolvedValue({ versions: list, budget })
  useUiStore.setState({ versionsOpen: true })
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <TimelinePreview />
        <VersionDrawer />
      </TooltipProvider>,
    )
  })
  await flush()
  // 抽屉打开后下一帧把焦点送进名字框：等它落定，否则它会在用例中途抢走焦点
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
const node = (id: string) => document.querySelector(`[data-timeline-node="${id}"]`)!
const days = () =>
  [...document.querySelectorAll('[data-timeline-day] h3')].map((h) => h.textContent)
const $ = <T extends Element = HTMLElement>(sel: string) => document.querySelector(sel) as T | null

beforeEach(async () => {
  document.body.innerHTML = ''
  vi.clearAllMocks()
  useTimelineStore.setState({ preview: null, rev: 0 })
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

describe('预览：只读，不改当前排版', () => {
  it('点节点 = 画布上盖一层那一刻的排版；文档与历史一个字节都不动', async () => {
    await mount([meta()])
    const pastBefore = useDocumentStore.getState().past.length
    const docBefore = useDocumentStore.getState().doc
    await act(async () => rows()[0].click())
    await flush()
    const overlay = $('[data-timeline-preview]')
    expect(overlay?.getAttribute('data-timeline-preview')).toBe('v1')
    // 预览里画的是**那一刻**的内容
    expect(overlay?.textContent).toContain('版本里的那一段')
    // 当前排版原样：同一个对象引用、历史没长
    expect(useDocumentStore.getState().doc).toBe(docBefore)
    expect(useDocumentStore.getState().past.length).toBe(pastBefore)
    expect(mockCreate).not.toHaveBeenCalled()
  })

  it('退出预览 / 再点一次同一个节点：遮罩收起', async () => {
    await mount([meta()])
    await act(async () => rows()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-timeline-exit-preview]')!.click())
    expect($('[data-timeline-preview]')).toBeNull()
    await act(async () => rows()[0].click())
    await flush()
    await act(async () => rows()[0].click())
    expect($('[data-timeline-preview]')).toBeNull()
  })

  it('关掉抽屉，预览跟着退出（没有抽屉的只读大图没有出口）', async () => {
    await mount([meta()])
    await act(async () => rows()[0].click())
    await flush()
    await act(async () => useUiStore.setState({ versionsOpen: false }))
    expect(useTimelineStore.getState().preview).toBeNull()
  })
})

describe('恢复：先存「恢复前」，再写，⌘Z 能退回', () => {
  it('先存下当前内容（关键时刻 before_restore，不是命名节点），再写入节点内容', async () => {
    await mount([meta()])
    await act(async () => rows()[0].click())
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
    await act(async () => rows()[0].click())
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
    await act(async () => rows()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
    await flush()
    expect(useDocumentStore.getState().doc.layoutGroups?.map((g) => g.id)).toEqual(['g1'])
  })

  it('「恢复前」存不下来就不恢复：当前排版原样', async () => {
    mockCreate.mockRejectedValueOnce(new Error('disk full'))
    await mount([meta()])
    await act(async () => rows()[0].click())
    await flush()
    await act(async () => $<HTMLButtonElement>('[data-timeline-preview-restore]')!.click())
    await flush()
    expect(ids()).toEqual(['now'])
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

  it('只看命名：只留命名节点，空组整组不出现', async () => {
    await mount([
      meta({ id: 'y', ts: NOW - DAY }),
      meta({ id: 'a', ts: NOW - 2000 }),
      meta({ id: 'n', ts: NOW - 1000, kind: 'named', named: true, auto: false, name: '投稿前' }),
    ])
    await act(async () => $<HTMLButtonElement>('#timeline-named-only-toggle')!.click())
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
    await act(async () => rows()[0].click())
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
