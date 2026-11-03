/**
 * 图内文字「复制为标注」的剪贴板与右键菜单（ADR 0163）：
 * ⌘C 在图内编辑里复制的是选中的**元素**（文字 → 标注），⌘V 走既有的对象粘贴路径；
 * 曲线 / 色块按 ⌘C 只出顶栏胶囊「{元素名}还不能复制」、不动剪贴板；右键弹层加两项；一次粘贴一条历史。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage } from '@/i18n'
import type { Manifest, ManifestElement, ManifestTextBlock } from '@/lib/api'
import { CLIPBOARD_FORMAT } from '@/lib/brand'
import { handleCopyEvent, handlePasteEvent, copyElementsAsAnnotations } from '@/lib/clipboard'
import { DUPLICATE_OFFSET_MM } from '@/lib/units'
import { QuickEdit } from '@/canvas/QuickEdit'
import { useQuickEdit } from '@/canvas/quickEditStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useMountedPngStore } from '@/store/mountedPngStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { CHROMIUM_UA, stubClipboardEngine } from '@/test/asyncClipboard'
import { emptyProject, type PanelObject, type TextObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const TB: ManifestTextBlock = {
  center: [0.5, 0.1],
  w: 0.2,
  h: 0.06,
  asc: 0.045,
  text: 'Reaction $k^{2}$',
  math: true,
  size: 10,
  color: '#112233',
  weight: 'bold',
  style: 'normal',
  rotation: 0,
  ha: 'center',
}
const el = (gid: string, role: string, label: string, tb?: ManifestTextBlock): ManifestElement =>
  ({ gid, role, label, bbox: [0.4, 0.05, 0.2, 0.06], editable: [], draggable: false, text_block: tb }) as unknown as ManifestElement

const manifest = (): Manifest => ({
  stem: 'f1',
  size_mm: [200, 150],
  elements: [
    el('axes_0.title', 'title', 'Reaction', TB),
    el('axes_0.ylabel', 'axis_label', 'Conversion', { ...TB, text: 'Conversion', math: false, rotation: 90, center: [0.03, 0.5], w: 0.3, h: 0.05, asc: 0.04 }),
    el('axes_0.lines_0', 'line', '曲线 A'),
    el('x.unknown', 'weird_role', 'x'),
  ],
})

const panel = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 10, y: 20, w: 100, h: 75, fileId: 'f1.pdf', fileKind: 'pdf',
    nativeW: 100, nativeH: 75, script: 'fig.py', overrides: [],
  }) as unknown as PanelObject

function seedExact(p: PanelObject, m: Manifest) {
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: p.fileId, rev: 1, manifest: m, svg: '<svg/>', status: 'ready', stale: false,
    lastPatches: JSON.stringify(p.overrides), wantPatches: JSON.stringify(p.overrides),
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, [p.fileId]: renderKeyOf(p) } }))
}

const doc = () => useDocumentStore.getState().doc
const texts = () => doc().objects.filter((o): o is TextObject => o.type === 'text')

function copyEvent(target: EventTarget = document.body) {
  const setData = vi.fn()
  const preventDefault = vi.fn()
  const e = { target, clipboardData: { getData: () => '', setData, clearData: vi.fn() }, preventDefault } as unknown as ClipboardEvent
  return { e, setData, preventDefault }
}
const pasteEvent = (data: string) =>
  ({ target: document.body, clipboardData: { getData: () => data, setData: vi.fn() }, preventDefault: vi.fn() }) as unknown as ClipboardEvent

beforeEach(async () => {
  useMountedPngStore.setState({ byPanel: {} })
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_copy')
  useDocumentStore.getState().commit({ key: 'test' } as never, (d) => {
    d.objects.push(panel())
  })
  seedExact(panel(), manifest())
  useSelectionStore.getState().set(['p1'])
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: [] })
  useUiStore.getState().setStatus(null)
})

afterEach(() => {
  useWorkspaceStore.getState().clear()
  useQuickEdit.getState().close()
  useUiStore.getState().setStatus(null)
  vi.restoreAllMocks()
})

describe('⌘C：图内编辑里选着元素', () => {
  it('文字元素：复制的是换算好的标注（不是整张图），原样带过公式、字号、颜色、粗体', () => {
    useUiStore.setState({ selectedGids: ['axes_0.title'] })
    const { e, setData, preventDefault } = copyEvent()
    expect(handleCopyEvent(e)).toBe(true)
    expect(preventDefault).toHaveBeenCalled()
    const payload = JSON.parse(setData.mock.calls[0][1])
    expect(payload.magic).toBe(CLIPBOARD_FORMAT)
    expect(payload.objects).toHaveLength(1)
    const o = payload.objects[0]
    expect(o.type).toBe('text')
    expect(o).toMatchObject({ text: 'Reaction $k^{2}$', textFormat: 'latex', sizePt: 10, color: '#112233', bold: true, align: 'center' })
    expect(o.rotationDeg).toBeUndefined()
    expect(formatMessage(useUiStore.getState().status)).toContain('已复制')
  })

  it('曲线 / 色块：顶栏胶囊说「{元素名}还不能复制」，剪贴板不动，整张图也不被复制走', () => {
    useUiStore.setState({ selectedGids: ['axes_0.lines_0'] })
    const { e, setData, preventDefault } = copyEvent()
    expect(handleCopyEvent(e)).toBe(true) // 消费了：不放给「复制选区里的整张图」
    expect(preventDefault).toHaveBeenCalled()
    expect(setData).not.toHaveBeenCalled()
    expect(formatMessage(useUiStore.getState().status)).toBe('曲线还不能复制')
    expect(useUiStore.getState().statusTone).toBe('info')
  })

  it('属性页文字框留下的、没有焦点的残留选区不挡 ⌘C（选中标题立刻按 ⌘C 就是这个形状）；别处的真实文字选区仍让位', () => {
    const editor = document.createElement('div')
    editor.setAttribute('contenteditable', 'true')
    editor.textContent = 'Reaction'
    const plain = document.createElement('p')
    plain.textContent = 'toast text'
    document.body.append(editor, plain)
    useUiStore.setState({ selectedGids: ['axes_0.title'] })
    const sel = document.getSelection()!
    sel.selectAllChildren(editor)
    // 浏览器把 copy 事件派给选区所在的那个没有焦点的文字框（不是焦点元素）
    const idle = copyEvent(editor)
    expect(handleCopyEvent(idle.e)).toBe(true)
    expect(JSON.parse(idle.setData.mock.calls[0][1]).objects[0].type).toBe('text')
    // 源码模式的文字框是 textarea：document 选区只露出外层容器，事件目标是那个没有焦点的 textarea
    const wrap = document.createElement('div')
    const ta = document.createElement('textarea')
    ta.value = 'Reaction'
    wrap.append(ta)
    document.body.append(wrap)
    sel.selectAllChildren(wrap)
    const src = copyEvent(ta)
    expect(handleCopyEvent(src.e)).toBe(true)
    expect(JSON.parse(src.setData.mock.calls[0][1]).objects[0].type).toBe('text')
    wrap.remove()
    sel.selectAllChildren(plain)
    const real = copyEvent(plain)
    expect(handleCopyEvent(real.e)).toBe(false) // toast 里选的文字：让位给原生文本复制
    expect(real.setData).not.toHaveBeenCalled()
    sel.removeAllRanges()
    editor.remove()
    plain.remove()
  })

  it('没选元素时 ⌘C 照旧复制选中的对象（整张图）', () => {
    const { e, setData } = copyEvent()
    expect(handleCopyEvent(e)).toBe(true)
    expect(JSON.parse(setData.mock.calls[0][1]).objects[0].type).toBe('panel')
  })

  it('权威 manifest 不在（正在重渲染）：不拿旧清单算位置，按「还不能复制」处理', () => {
    useRenderStore.getState().patch(renderKeyOf(panel()), { stale: true })
    useUiStore.setState({ selectedGids: ['axes_0.title'] })
    const { e, setData } = copyEvent()
    expect(handleCopyEvent(e)).toBe(true)
    expect(setData).not.toHaveBeenCalled()
    expect(formatMessage(useUiStore.getState().status)).toContain('还不能复制')
  })
})

describe('胶囊只写元素类别', () => {
  it('认识的类别用类别词；不认识的说「这个元素」', async () => {
    await copyElementsAsAnnotations('p1', ['x.unknown'])
    expect(formatMessage(useUiStore.getState().status)).toBe('这个元素还不能复制')
  })
})

describe('⌘V：粘贴为标注', () => {
  const copied = (...gids: string[]) => {
    useUiStore.setState({ selectedGids: gids })
    const { e, setData } = copyEvent()
    handleCopyEvent(e)
    return setData.mock.calls[0][1] as string
  }

  it('图内编辑里也能粘：一条历史、选中新标注、退出图内编辑回到画布层；撤销 / 重做对称', () => {
    const data = copied('axes_0.title', 'axes_0.ylabel')
    const before = texts().length
    const histBefore = useDocumentStore.getState().past.length
    expect(handlePasteEvent(pasteEvent(data))).toBe(true)
    expect(texts().length).toBe(before + 2)
    expect(useDocumentStore.getState().past.length).toBe(histBefore + 1) // 一次粘贴一条历史
    const pasted = texts()
    expect(useSelectionStore.getState().ids).toEqual(pasted.map((o) => o.id))
    expect(useUiStore.getState().elementPanelId).toBeNull()
    // y 轴标签带 -90；同画布粘贴向右下错开 DUPLICATE_OFFSET_MM（与 ⌘D 同一常量）
    const y = pasted.find((o) => o.text === 'Conversion')!
    expect(y.rotationDeg).toBe(-90)
    const raw = JSON.parse(data).objects.find((o: TextObject) => o.text === 'Conversion')
    expect(y.x).toBeCloseTo(raw.x + DUPLICATE_OFFSET_MM, 9)
    expect(y.y).toBeCloseTo(raw.y + DUPLICATE_OFFSET_MM, 9)

    useDocumentStore.getState().undo()
    expect(texts().length).toBe(before)
    useDocumentStore.getState().redo()
    expect(texts().length).toBe(before + 2)
  })

  it('标注留在原来的工作区：粘出来的只有文字、没有第二张图（单图编辑保持单图编辑）', () => {
    handlePasteEvent(pasteEvent(copied('axes_0.title')))
    expect(doc().objects.filter((o) => o.type === 'panel')).toHaveLength(1)
  })
})

describe('右键弹层：复制为标注 / 粘贴为标注', () => {
  let root: Root
  let host: HTMLDivElement
  let restore: (() => void) | null = null
  beforeEach(() => {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
  })
  afterEach(() => {
    act(() => root.unmount())
    host.remove()
    restore?.()
    restore = null
  })
  const open = (gid: string) => {
    useUiStore.setState({ selectedGids: [gid] })
    act(() => {
      root.render(
        <TooltipProvider>
          <QuickEdit />
        </TooltipProvider>,
      )
      useQuickEdit.getState().open({ kind: 'element', panelId: 'p1', gid }, 100, 100)
    })
  }
  const item = (name: string) => document.body.querySelector<HTMLButtonElement>(`[data-quick-item="${name}"]`)

  it('文字元素：复制可点，点了写系统剪贴板并关掉弹层；不能读剪贴板的引擎上不摆「粘贴为标注」', async () => {
    restore = stubClipboardEngine(CHROMIUM_UA, true)
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText, readText: async () => '' }, configurable: true })
    open('axes_0.title')
    expect(item('copy-as-annotation')!.disabled).toBe(false)
    expect(item('paste-as-annotation')).not.toBeNull() // 能读的引擎上摆出来（不知道剪贴板里有什么 = 可点）
    expect(item('paste-as-annotation')!.disabled).toBe(false)
    await act(async () => {
      item('copy-as-annotation')!.click()
    })
    expect(writeText).toHaveBeenCalledTimes(1)
    const payload = JSON.parse((writeText.mock.calls[0] as unknown as [string])[0])
    expect(payload.objects[0]).toMatchObject({ type: 'text', text: 'Reaction $k^{2}$' })
    expect(useQuickEdit.getState().target).toBeNull()
  })

  it('曲线：「复制为标注」置灰，其余现有项不变（全部属性仍在）', () => {
    restore = stubClipboardEngine(CHROMIUM_UA, true)
    open('axes_0.lines_0')
    expect(item('copy-as-annotation')!.disabled).toBe(true)
    expect(item('open-inspector')).not.toBeNull()
  })

  it('WebKit / 没有 readText：不摆「粘贴为标注」（⌘V 照常可用）', () => {
    restore = stubClipboardEngine(CHROMIUM_UA, false)
    open('axes_0.title')
    expect(item('copy-as-annotation')).not.toBeNull()
    expect(item('paste-as-annotation')).toBeNull()
  })

  it('入口函数对曲线只出胶囊、返回 false、不写剪贴板', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    expect(await copyElementsAsAnnotations('p1', ['axes_0.lines_0'])).toBe(false)
    expect(writeText).not.toHaveBeenCalled()
    expect(formatMessage(useUiStore.getState().status)).toBe('曲线还不能复制')
  })
})
