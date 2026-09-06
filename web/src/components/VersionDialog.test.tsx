/**
 * 文档版本抽屉（审计 T04）。
 *
 * 守两件事，都是验收里明写的：
 *
 * 1. **恢复之前先把当前状态存成一版。** 恢复是这个抽屉里唯一会覆盖用户当前
 *    工作的动作；那一版是用户后悔时唯一的回头路，而它必须在写入**之前**落
 *    （写完再存等于存下来的已经是恢复后的内容）。
 * 2. **每行说的是「什么时候 → 变了什么」**，不是把日期说两遍。自动版本的
 *    名字由后端按时间生成，与行内时间重复。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createVersion: vi.fn(),
  fetchVersions: vi.fn(),
  fetchVersionDoc: vi.fn(),
}))

import { createVersion, fetchVersionDoc, fetchVersions, type LayoutVersionMeta } from '@/lib/api'
import { VersionDrawer } from '@/components/VersionDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type FigureDocument, type TextObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockList = vi.mocked(fetchVersions)
const mockDoc = vi.mocked(fetchVersionDoc)
const mockCreate = vi.mocked(createVersion)

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const CANVAS_ID = 'c_v'

const meta = (over: Partial<LayoutVersionMeta> = {}): LayoutVersionMeta => ({
  id: 'v1',
  name: '09-06 21:30',
  ts: 1_757_000_000_000,
  auto: true,
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

/** `fetchVersions` 的返回按时间升序（抽屉自己 reverse 成最新在上） */
async function mount(list: LayoutVersionMeta[]) {
  mockList.mockResolvedValue(list)
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
  await act(async () => {
    await Promise.resolve()
  })
}

const rows = () =>
  [...document.querySelectorAll('ul[aria-label] > li > button')] as HTMLButtonElement[]

const buttonByText = (t: string) =>
  [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === t)

beforeEach(async () => {
  document.body.innerHTML = ''
  vi.clearAllMocks()
  mockCreate.mockResolvedValue({ ok: true, version: meta({ id: 'v_backup' }) })
  mockDoc.mockResolvedValue({ ...meta(), doc: snapshot() })
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

describe('恢复之前先留一版', () => {
  it('先存下当前内容，再写入版本内容', async () => {
    await mount([meta()])
    await act(async () => rows()[0].click())
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => buttonByText('恢复为新版本')!.click())
    await act(async () => {
      await Promise.resolve()
    })

    expect(mockCreate).toHaveBeenCalledTimes(1)
    const sent = mockCreate.mock.calls[0][1]
    expect(sent.auto).toBe(true)
    // 存下去的是**恢复前**的内容：里面是当前那一段，不是版本里的那一段
    expect(sent.doc?.objects.map((o) => o.id)).toEqual(['now'])
    // 写入确实发生了（否则「先存一版」是在给一件没发生的事做备份）
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['old'])
    // 可撤销：恢复是一条历史，用户还能退回去
    expect(useDocumentStore.getState().past.length).toBeGreaterThan(0)
  })
})

describe('列表每行说什么', () => {
  it('时间 + 变了什么；后端按时间生成的名字不再重复一遍', async () => {
    await mount([
      meta({ id: 'v1', objects: 3, ts: 1 }),
      meta({ id: 'v2', objects: 5, ts: 2 }),
    ])
    const [newest, oldest] = rows().map((r) => r.textContent ?? '')
    expect(newest).toContain('新增 2 个对象')
    // 最老的一条没有可比的上一版：说自己有多大
    expect(oldest).toContain('3')
    // 「09-06 21:30」这个生成名不再作为一行标题出现（每行本来就有时间）
    expect(newest.match(/09-06 21:30/g) ?? []).toHaveLength(0)
  })

  it('用户起的名字照常显示', async () => {
    await mount([meta({ name: '投稿前' })])
    expect(rows()[0].textContent).toContain('投稿前')
  })
})
