/**
 * 图内副本的创建 / 选中 / 删除 / 恢复（ADR 0207，`store/actions` 的 `duplicateFigureElements` 一族 + `store/figureCopySelect`）。
 * 剪贴板分派与弹层在 `lib/figureTextClipboard.test.tsx`，判据与落点换算在 `lib/figureElementCopy.test.ts`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage } from '@/i18n'
import type { Manifest, ManifestElement, ManifestTextBlock } from '@/lib/api'
import { panelFullRect } from '@/lib/elementGeom'
import { COPY_GID_RE } from '@/lib/figureElementCopy'
import { overrideCounts } from '@/lib/overrideCounts'
import { contentToPageVec, panelContentTransform } from '@/lib/panelTransform'
import { DUPLICATE_OFFSET_MM } from '@/lib/units'
import {
  deleteFigureCopies,
  duplicateFigureElements,
  duplicateSelection,
  hideElement,
  hideElements,
  resetOverrides,
  restoreElementEdits,
} from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { copySelectPending } from '@/store/figureCopySelect'
import { cancelCopySelect } from '@/store/figureCopySelect'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type PanelObject } from '@/types/document'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const TB: ManifestTextBlock = {
  center: [0.5, 0.1], w: 0.2, h: 0.06, asc: 0.045, text: 'Reaction', math: false,
  size: 10, color: '#112233', weight: 'normal', style: 'normal', rotation: 0, ha: 'center',
}
const text = (gid: string, role = 'title'): ManifestElement =>
  ({
    gid, role, label: gid, bbox: [0.4, 0.05, 0.2, 0.06], anchor: [0.5, 0.1],
    editable: [{ prop: 'text', value: 'Reaction' }, { prop: 'visible', value: true }], draggable: true, text_block: TB,
  }) as unknown as ManifestElement
const base = (): ManifestElement[] => [
  { gid: 'axes_0', role: 'axes', label: 'axes_0', bbox: [0.1, 0.1, 0.8, 0.8], editable: [{ prop: 'position', value: [0.1, 0.1, 0.8, 0.8] }], draggable: false } as unknown as ManifestElement,
  text('axes_0.title'),
  text('axes_0.ylabel', 'axis_label'),
  { gid: 'axes_0.lines_0', role: 'line', label: '曲线', bbox: [0.2, 0.2, 0.5, 0.5], editable: [], draggable: false } as unknown as ManifestElement,
]
const manifest = (extra: ManifestElement[] = []): Manifest => ({ stem: 'f1', size_mm: [100, 75], elements: [...base(), ...extra] })
const mkPanel = (over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 10, y: 20, w: 100, h: 75, fileId: 'f1.pdf', fileKind: 'pdf',
    nativeW: 100, nativeH: 75, script: 'fig.py', overrides: [], ...over,
  }) as unknown as PanelObject

function seedExact(p: PanelObject, m: Manifest, rev = 1) {
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: p.fileId, rev, manifest: m, svg: '<svg/>', status: 'ready', stale: false,
    lastPatches: JSON.stringify(p.overrides), wantPatches: JSON.stringify(p.overrides),
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, [p.fileId]: renderKeyOf(p) } }))
}
const doc = () => useDocumentStore.getState().doc
const panelNow = () => doc().objects.find((o): o is PanelObject => o.id === 'p1')!
const copyGids = () => panelNow().overrides.filter((o) => o.prop === 'copy_of').map((o) => o.gid)
const past = () => useDocumentStore.getState().past.length

async function boot(over: Partial<PanelObject> = {}) {
  useRenderStore.getState().clear()
  useRenderStore.setState({ render: vi.fn() })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_dup')
  useDocumentStore.getState().commit({ key: 'test' } as never, (d) => {
    d.objects.push(mkPanel(over))
  })
  seedExact(panelNow(), manifest())
  useSelectionStore.getState().set(['p1'])
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.title'] })
  useUiStore.getState().setStatus(null)
}

beforeEach(async () => {
  await boot()
})
afterEach(() => {
  cancelCopySelect()
  useWorkspaceStore.getState().clear()
  useUiStore.getState().setStatus(null)
  vi.restoreAllMocks()
})

describe('duplicateFigureElements：写的 override', () => {
  it('gid 是 <宿主>.copy_<8 位>；写 copy_of 一条历史；返回新 gid', () => {
    const histBefore = past()
    const made = duplicateFigureElements('p1', ['axes_0.title', 'axes_0.ylabel'])
    expect(made).toHaveLength(2)
    for (const g of made) expect(COPY_GID_RE.test(g)).toBe(true)
    expect(made.every((g) => g.startsWith('axes_0.copy_'))).toBe(true)
    expect(copyGids()).toEqual(made)
    expect(past()).toBe(histBefore + 1)
  })

  it('整图文字的宿主是 fig', () => {
    const m = manifest([text('fig.texts_0', 'text')])
    seedExact(panelNow(), m)
    const [g] = duplicateFigureElements('p1', ['fig.texts_0'])
    expect(g.startsWith('fig.copy_')).toBe(true)
  })

  it('页面右下 4 mm：面板转 / 翻转后换回页面仍是 (4, 4)；at 落回宿主 axes 分数', async () => {
    for (const over of [{}, { rotation: 90 }, { rotation: 180 }, { rotation: 270 }, { flipH: true }, { rotation: 90, flipV: true }]) {
      await boot(over as Partial<PanelObject>)
      // 源放在图中间：四个方向错开 4 mm 都不会出图（出图的收回另有用例）
      const mid = { ...text('axes_0.title'), bbox: [0.4, 0.4, 0.2, 0.06], anchor: [0.5, 0.43] } as unknown as ManifestElement
      seedExact(panelNow(), { ...manifest(), elements: manifest().elements.map((e) => (e.gid === 'axes_0.title' ? mid : e)) })
      duplicateFigureElements('p1', ['axes_0.title'])
      const at = (panelNow().overrides.find((o) => o.prop === 'copy_of')!.value as { at: number[] }).at
      // 反推：axes 分数 → figure 分数（y 向下）→ 内容 mm → 页面 mm
      const fx = 0.1 + at[0] * 0.8
      const fy = 1 - (0.1 + at[1] * 0.8)
      const full = panelFullRect(panelNow())
      const page = contentToPageVec(panelContentTransform(panelNow()), (fx - 0.5) * full.w, (fy - 0.43) * full.h)
      expect(page[0], JSON.stringify(over)).toBeCloseTo(DUPLICATE_OFFSET_MM, 6)
      expect(page[1], JSON.stringify(over)).toBeCloseTo(DUPLICATE_OFFSET_MM, 6)
    }
  })

  it('撤销：副本与它的全部修改一起消失，overrides 回到粘贴之前', () => {
    useDocumentStore.getState().commit({ key: 'test' } as never, (d) => {
      ;(d.objects.find((o) => o.id === 'p1') as PanelObject).overrides.push({ gid: 'axes_0.title', prop: 'fontsize', value: 14 })
    })
    seedExact(panelNow(), manifest())
    const before = structuredClone(panelNow().overrides)
    duplicateFigureElements('p1', ['axes_0.title'])
    expect(panelNow().overrides.length).toBe(before.length + 2) // copy_of + 抄来的字号
    useDocumentStore.getState().undo()
    expect(panelNow().overrides).toEqual(before)
  })

  it('不能复制的元素（曲线）：什么都不写，胶囊说「曲线还不能复制」', () => {
    const histBefore = past()
    expect(duplicateFigureElements('p1', ['axes_0.lines_0'])).toEqual([])
    expect(past()).toBe(histBefore)
    expect(formatMessage(useUiStore.getState().status)).toBe('曲线还不能复制')
  })

  it('权威 manifest 不在：「还不能复制」，不拿旧清单算', () => {
    useRenderStore.getState().patch(renderKeyOf(panelNow()), { stale: true })
    const histBefore = past()
    expect(duplicateFigureElements('p1', ['axes_0.title'])).toEqual([])
    expect(past()).toBe(histBefore)
    expect(formatMessage(useUiStore.getState().status)).toContain('还不能复制')
    expect(copySelectPending()).toBe(false)
  })

  it('选中的是副本再创建副本：src 仍是原来的源，样式从这只副本抄', () => {
    const [first] = duplicateFigureElements('p1', ['axes_0.title'])
    useDocumentStore.getState().commit({ key: 'test' } as never, (d) => {
      ;(d.objects.find((o) => o.id === 'p1') as PanelObject).overrides.push({ gid: first, prop: 'color', value: '#00aa00' })
    })
    seedExact(panelNow(), manifest([text(first)]))
    const [second] = duplicateFigureElements('p1', [first])
    expect(second).not.toBe(first)
    const mine = panelNow().overrides.filter((o) => o.gid === second)
    expect((mine.find((o) => o.prop === 'copy_of')!.value as { src: string }).src).toBe('axes_0.title')
    expect(mine.find((o) => o.prop === 'color')!.value).toBe('#00aa00')
  })
})

describe('建完后选中副本：等下一份权威 manifest 里出现才选', () => {
  const withCopies = (gids: string[]) => manifest(gids.map((g) => text(g)))

  it('manifest 还没回来时选区不动；回来后选中新副本', () => {
    const made = duplicateFigureElements('p1', ['axes_0.title'])
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.title'])
    expect(copySelectPending()).toBe(true)
    // 渲染回来：这一版 manifest 对得上当前 overrides、里面有副本
    seedExact(panelNow(), withCopies(made), 2)
    expect(useUiStore.getState().selectedGids).toEqual(made)
    expect(copySelectPending()).toBe(false)
    expect(useUiStore.getState().elementPanelId).toBe('p1') // 仍在图内编辑
  })

  it('撤销：取消待选中，之后清单里即使再出现也不选（撤销回来的那一版还没画好时也一样）', () => {
    const made = duplicateFigureElements('p1', ['axes_0.title'])
    // 撤销后的那一版此刻还没有权威清单：只靠「copy_of 已不在」这一条才能取消，不能等到清单回来再发现
    useRenderStore.getState().patch(renderKeyOf({ ...panelNow(), overrides: [] }), { stale: true })
    useDocumentStore.getState().undo()
    expect(copySelectPending()).toBe(false)
    seedExact(panelNow(), withCopies(made), 2)
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.title'])
  })

  it('用户自己改了选区：取消', () => {
    const made = duplicateFigureElements('p1', ['axes_0.title'])
    useUiStore.getState().setSelectedGids(['axes_0.ylabel'])
    expect(copySelectPending()).toBe(false)
    seedExact(panelNow(), withCopies(made), 2)
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.ylabel'])
  })

  it('退出图内编辑 / 换文档：取消', async () => {
    const made = duplicateFigureElements('p1', ['axes_0.title'])
    useUiStore.getState().setElementPanel(null)
    expect(copySelectPending()).toBe(false)
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.title'] })
    seedExact(panelNow(), withCopies(made), 2)
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.title'])
  })

  it('渲染失败：取消，选区不动', () => {
    const made = duplicateFigureElements('p1', ['axes_0.title'])
    useRenderStore.getState().patch(renderKeyOf(panelNow()), { status: 'error', stale: true })
    expect(copySelectPending()).toBe(false)
    seedExact(panelNow(), withCopies(made), 2)
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.title'])
  })

  it('这一版画好了、里面没有副本（引擎没建出来）：取消，选区不动', () => {
    duplicateFigureElements('p1', ['axes_0.title'])
    seedExact(panelNow(), manifest(), 2) // 对得上当前 overrides，但没有副本元素
    expect(copySelectPending()).toBe(false)
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.title'])
  })

  it('连建两批：只跟最后一批', () => {
    duplicateFigureElements('p1', ['axes_0.title'])
    seedExact(panelNow(), manifest(), 2) // 第一批没出现 → 已取消
    const second = duplicateFigureElements('p1', ['axes_0.ylabel'])
    seedExact(panelNow(), withCopies(second), 3)
    expect(useUiStore.getState().selectedGids).toEqual(second)
  })
})

describe('⌘D / 系统菜单「创建副本」的唯一出口 duplicateSelection', () => {
  it('图内编辑里选着可复制的元素：原地建图内副本，整张图不被复制', () => {
    const objectsBefore = doc().objects.length
    duplicateSelection()
    expect(copyGids()).toHaveLength(1)
    expect(doc().objects.length).toBe(objectsBefore)
  })

  it('选着不能复制的元素（曲线）：也消费，只出胶囊，整张图不被复制', () => {
    useUiStore.setState({ selectedGids: ['axes_0.lines_0'] })
    const objectsBefore = doc().objects.length
    const histBefore = past()
    duplicateSelection()
    expect(doc().objects.length).toBe(objectsBefore)
    expect(past()).toBe(histBefore)
    expect(formatMessage(useUiStore.getState().status)).toBe('曲线还不能复制')
  })

  it('图内编辑里没选元素 / 不在图内编辑：照旧复制选区里的对象', () => {
    useUiStore.setState({ selectedGids: [] })
    duplicateSelection()
    expect(doc().objects.filter((o) => o.type === 'panel')).toHaveLength(2)
    useUiStore.setState({ elementPanelId: null, selectedGids: ['axes_0.title'] })
    useSelectionStore.getState().set(['p1'])
    duplicateSelection()
    expect(doc().objects.filter((o) => o.type === 'panel')).toHaveLength(3)
    expect(copyGids()).toHaveLength(0)
  })
})

describe('删除副本 / 恢复此元素修改', () => {
  const makeCopy = (): string => {
    const [g] = duplicateFigureElements('p1', ['axes_0.title'])
    useDocumentStore.getState().commit({ key: 'test' } as never, (d) => {
      ;(d.objects.find((o) => o.id === 'p1') as PanelObject).overrides.push(
        { gid: g, prop: 'fontsize', value: 20 },
        { gid: g, prop: 'color', value: '#ff0000' },
      )
    })
    seedExact(panelNow(), manifest([text(g)]), 2)
    cancelCopySelect()
    useUiStore.setState({ selectedGids: [g] })
    return g
  }

  it('「恢复此元素修改」只清 copy_of 以外的，副本留着；一条历史；计数不数 copy_of', () => {
    const g = makeCopy()
    expect(overrideCounts(panelNow().overrides, g).element).toBe(2)
    const histBefore = past()
    restoreElementEdits('p1', g)
    expect(panelNow().overrides.filter((o) => o.gid === g).map((o) => o.prop)).toEqual(['copy_of'])
    expect(past()).toBe(histBefore + 1)
    useDocumentStore.getState().undo()
    expect(panelNow().overrides.filter((o) => o.gid === g)).toHaveLength(3)
  })

  it('脚本里的元素「恢复」照旧清它全部修改（行为不变）', () => {
    useDocumentStore.getState().commit({ key: 'test' } as never, (d) => {
      ;(d.objects.find((o) => o.id === 'p1') as PanelObject).overrides.push({ gid: 'axes_0.title', prop: 'fontsize', value: 20 })
    })
    seedExact(panelNow(), manifest())
    restoreElementEdits('p1', 'axes_0.title')
    expect(panelNow().overrides).toEqual([])
  })

  it('「删除副本」：copy_of 与 gid 上全部修改一次摘掉，一条历史、选区去掉它；撤销整只请回', () => {
    const g = makeCopy()
    const histBefore = past()
    expect(deleteFigureCopies('p1', [g])).toBe(1)
    expect(panelNow().overrides.filter((o) => o.gid === g)).toEqual([])
    expect(past()).toBe(histBefore + 1)
    expect(useUiStore.getState().selectedGids).toEqual([])
    useDocumentStore.getState().undo()
    expect(panelNow().overrides.filter((o) => o.gid === g)).toHaveLength(3)
  })

  it('隐藏的入口（右键 / 属性页 / 元素树 / Delete 键都经 hideElement(s)）对副本就是删除，不写 visible:false', () => {
    const g = makeCopy()
    hideElement('p1', g, '副本')
    expect(panelNow().overrides.some((o) => o.gid === g)).toBe(false)
    expect(panelNow().overrides.some((o) => o.prop === 'visible')).toBe(false)
    // 混选：副本整只删、脚本里的元素照旧隐藏
    const g2 = makeCopy()
    hideElements('p1', [{ gid: g2, label: 'c' }, { gid: 'axes_0.ylabel', label: 'y' }])
    expect(panelNow().overrides.some((o) => o.gid === g2)).toBe(false)
    expect(panelNow().overrides.find((o) => o.gid === 'axes_0.ylabel' && o.prop === 'visible')?.value).toBe(false)
  })

  it('「恢复图内修改」（整张图）：副本一起消失', () => {
    makeCopy()
    resetOverrides('p1')
    expect(panelNow().overrides).toEqual([])
  })

  it('不是副本的 gid 不理', () => {
    expect(deleteFigureCopies('p1', ['axes_0.title'])).toBe(0)
  })
})
