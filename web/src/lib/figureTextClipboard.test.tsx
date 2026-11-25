/**
 * 图内文字的剪贴板与右键弹层（ADR 0163 + 0207）：
 * ⌘C 在图内编辑里复制的是选中的**元素**：负载里是换算好的文字标注（`objects`，0163 原样），第一期能复制成图内副本的
 * 还带一段 `elements`（ADR 0207）；⌘V 在**同一份文档、同一张图**的图内编辑里建图内副本（不退出图内编辑），
 * 落在别处 / 「粘贴为标注」仍落成标注；曲线 / 色块按 ⌘C 只出顶栏胶囊「{元素名}还不能复制」、不动剪贴板；一次粘贴一条历史。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage } from '@/i18n'
import type { Manifest, ManifestElement, ManifestTextBlock } from '@/lib/api'
import { CLIPBOARD_FORMAT } from '@/lib/brand'
import { handleCopyEvent, handlePasteEvent, copyFigureElements, pasteObjects, parsePayload } from '@/lib/clipboard'
import { DUPLICATE_OFFSET_MM } from '@/lib/units'
import { QuickEdit } from '@/canvas/QuickEdit'
import { useQuickEdit } from '@/canvas/quickEditStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { cancelCopySelect } from '@/store/figureCopySelect'
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
  ({
    gid,
    role,
    label,
    bbox: [0.4, 0.05, 0.2, 0.06],
    editable: tb ? [{ prop: 'text', value: tb.text }] : [],
    draggable: !!tb,
    text_block: tb,
    ...(tb ? { anchor: tb.center, drag_prop: 'pos_frac' } : {}),
  }) as unknown as ManifestElement
const axes0 = (): ManifestElement =>
  ({
    gid: 'axes_0', role: 'axes', label: 'axes_0', bbox: [0.1, 0.1, 0.8, 0.8],
    editable: [{ prop: 'position', value: [0.1, 0.1, 0.8, 0.8] }], draggable: false,
  }) as unknown as ManifestElement

const manifest = (): Manifest => ({
  stem: 'f1',
  size_mm: [200, 150],
  elements: [
    axes0(),
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
  useRenderStore.setState({ render: vi.fn() })
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
  cancelCopySelect()
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
    await copyFigureElements('p1', ['x.unknown'])
    expect(formatMessage(useUiStore.getState().status)).toBe('这个元素还不能复制')
  })
})

const copied = (...gids: string[]) => {
  useUiStore.setState({ selectedGids: gids })
  const { e, setData } = copyEvent()
  handleCopyEvent(e)
  return setData.mock.calls[0][1] as string
}
const panelNow = () => doc().objects.find((o): o is PanelObject => o.id === 'p1')!
const copyOfs = () => panelNow().overrides.filter((o) => o.prop === 'copy_of')

describe('⌘C 的负载：标注（objects）+ 图内副本（elements，ADR 0207）', () => {
  it('第一期能复制成副本的元素：objects 照旧，多一段 elements（文档、面板、gid），只记 gid 不记落点', () => {
    const payload = JSON.parse(copied('axes_0.title', 'axes_0.ylabel'))
    expect(payload.objects).toHaveLength(2)
    expect(payload.elements).toEqual({
      docId: useDocumentStore.getState().documentId,
      panelId: 'p1',
      items: [{ gid: 'axes_0.title' }, { gid: 'axes_0.ylabel' }],
    })
  })

  it('选中的不能全部复制成副本（闭集外的文字）：整批只带标注，不让粘贴悄悄少一部分', () => {
    const m = manifest()
    m.elements.push(el('axes_0.legend.texts_0', 'legend_text', '图例文字', { ...TB, text: 'A' }))
    seedExact(panel(), m)
    const payload = JSON.parse(copied('axes_0.title', 'axes_0.legend.texts_0'))
    expect(payload.objects).toHaveLength(2)
    expect(payload.elements).toBeUndefined()
    const only = JSON.parse(copied('axes_0.legend.texts_0'))
    expect(only.objects).toHaveLength(1)
    expect(only.elements).toBeUndefined()
  })

  it('宿主是 3D 子图 / 色条轴：只有标注', () => {
    const m = manifest()
    m.elements[0] = { ...axes0(), role: 'axes3d' } as ManifestElement
    seedExact(panel(), m)
    const payload = JSON.parse(copied('axes_0.title'))
    expect(payload.objects).toHaveLength(1)
    expect(payload.elements).toBeUndefined()
  })

  it('读剪贴板：elements 形状不对当没有（别的版本 / 手改过），objects 照用', () => {
    const good = JSON.parse(copied('axes_0.title'))
    for (const bad of [{ docId: 1 }, { docId: 'd', panelId: 'p1', items: [] }, { docId: 'd', panelId: 'p1', items: [{ gid: 3 }] }, 'x', null]) {
      const got = parsePayload(JSON.stringify({ ...good, elements: bad }))!
      expect(got.objects).toHaveLength(1)
      expect(got.elements).toBeUndefined()
    }
    expect(parsePayload(JSON.stringify(good))!.elements).toBeDefined()
  })
})

describe('⌘V：同一张图的图内编辑里建图内副本', () => {
  it('事件派给属性页那个没有焦点的文字框（残留选区）时照样按页面粘贴，不写进框里', () => {
    const data = copied('axes_0.title')
    // 属性页的富文本框：没有焦点、残留选区。jsdom 不实现 isContentEditable，手动补上（真浏览器里它就是 true）
    const editor = document.createElement('div')
    editor.setAttribute('contenteditable', 'true')
    Object.defineProperty(editor, 'isContentEditable', { value: true })
    editor.textContent = 'Reaction kinetics'
    document.body.append(editor)
    const before = copyOfs().length
    const e = { ...pasteEvent(data), target: editor } as unknown as ClipboardEvent
    expect(handlePasteEvent(e)).toBe(true)
    expect(e.preventDefault).toHaveBeenCalled()
    // 同一张图的图内编辑里：落成图内副本（ADR 0207），不是写进框里
    expect(copyOfs().length).toBe(before + 1)
    expect(editor.textContent).toBe('Reaction kinetics')
    // 有焦点就是真在框里打字：让位给浏览器
    editor.tabIndex = 0
    editor.focus()
    expect(handlePasteEvent({ ...pasteEvent(data), target: editor } as unknown as ClipboardEvent)).toBe(false)
    editor.remove()
  })

  it('建副本：一条历史、仍在图内编辑、画布上没有新标注；写 copy_of（gid 形状 / src / 文字 / 落点）', () => {
    const data = copied('axes_0.title')
    const objectsBefore = doc().objects.length
    const histBefore = useDocumentStore.getState().past.length
    expect(handlePasteEvent(pasteEvent(data))).toBe(true)
    expect(useDocumentStore.getState().past.length).toBe(histBefore + 1)
    expect(useUiStore.getState().elementPanelId).toBe('p1') // 不退出图内编辑
    expect(doc().objects.length).toBe(objectsBefore) // 没有落成标注
    expect(useSelectionStore.getState().ids).toEqual(['p1'])
    const [c] = copyOfs()
    expect(c.gid).toMatch(/^axes_0\.copy_[a-z0-9]{8}$/)
    const v = c.value as { src: string; text: string; at: number[] }
    expect(v.src).toBe('axes_0.title')
    expect(v.text).toBe('Reaction $k^{2}$')
    // 锚点 [0.5, 0.1] 向右下错开 4 mm（100 × 75 mm 的图），换成 axes_0 分数（position [0.1, 0.1, 0.8, 0.8]）
    expect(v.at[0]).toBeCloseTo((0.5 + 4 / 100 - 0.1) / 0.8, 9)
    expect(v.at[1]).toBeCloseTo((1 - (0.1 + 4 / 75) - 0.1) / 0.8, 9)
  })

  it('多个元素：每个一只副本，一条历史；撤销整批消失、重做回来', () => {
    const data = copied('axes_0.title', 'axes_0.ylabel')
    const histBefore = useDocumentStore.getState().past.length
    handlePasteEvent(pasteEvent(data))
    expect(copyOfs()).toHaveLength(2)
    expect(useDocumentStore.getState().past.length).toBe(histBefore + 1)
    useDocumentStore.getState().undo()
    expect(copyOfs()).toHaveLength(0)
    expect(panelNow().overrides).toEqual([])
    useDocumentStore.getState().redo()
    expect(copyOfs()).toHaveLength(2)
  })

  it('源上已有的样式类修改抄给副本（不抄 text / pos_frac / visible），和 copy_of 在同一条历史里', () => {
    useDocumentStore.getState().commit({ key: 'test' } as never, (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides.push(
        { gid: 'axes_0.title', prop: 'fontsize', value: 14 },
        { gid: 'axes_0.title', prop: 'color', value: '#cc0000' },
        { gid: 'axes_0.title', prop: 'text', value: 'Edited' },
        { gid: 'axes_0.title', prop: 'pos_frac', value: [0.3, 0.3] },
      )
    })
    seedExact(panelNow(), manifest())
    const data = copied('axes_0.title')
    const histBefore = useDocumentStore.getState().past.length
    handlePasteEvent(pasteEvent(data))
    expect(useDocumentStore.getState().past.length).toBe(histBefore + 1)
    const cg = copyOfs()[0].gid
    const mine = panelNow().overrides.filter((o) => o.gid === cg)
    expect(mine.map((o) => [o.prop, o.value])).toEqual([
      ['copy_of', expect.objectContaining({ src: 'axes_0.title' })],
      ['fontsize', 14],
      ['color', '#cc0000'],
    ])
    // 源自己一条没少
    expect(panelNow().overrides.filter((o) => o.gid === 'axes_0.title')).toHaveLength(4)
  })

  it('权威 manifest 不在（正在重渲染）：说「还不能复制」、消费这次粘贴、什么都不写，也不退出图内编辑', () => {
    const data = copied('axes_0.title')
    useRenderStore.getState().patch(renderKeyOf(panel()), { stale: true })
    const histBefore = useDocumentStore.getState().past.length
    expect(handlePasteEvent(pasteEvent(data))).toBe(true)
    expect(useDocumentStore.getState().past.length).toBe(histBefore)
    expect(useUiStore.getState().elementPanelId).toBe('p1')
    expect(formatMessage(useUiStore.getState().status)).toContain('还不能复制')
  })

  it('粘贴这一刻重新过判据：源已经不在清单里 → 退回落成标注（0163 行为）', () => {
    const data = copied('axes_0.title')
    seedExact(panel(), { ...manifest(), elements: manifest().elements.filter((e) => e.gid !== 'axes_0.title') })
    const before = texts().length
    expect(handlePasteEvent(pasteEvent(data))).toBe(true)
    expect(copyOfs()).toHaveLength(0)
    expect(texts().length).toBe(before + 1)
  })

  it('两项负载、一项源已不在：整批落成标注、无副本被建（全有或全无，不悄悄只粘幸存的）', () => {
    const data = copied('axes_0.title', 'axes_0.ylabel')
    seedExact(panel(), { ...manifest(), elements: manifest().elements.filter((e) => e.gid !== 'axes_0.ylabel') })
    const before = texts().length
    const histBefore = useDocumentStore.getState().past.length
    expect(handlePasteEvent(pasteEvent(data))).toBe(true)
    expect(copyOfs()).toHaveLength(0)
    expect(panelNow().overrides).toEqual([])
    // 两项都落成标注（负载里的标注快照两份俱在），一条历史
    expect(texts().length).toBe(before + 2)
    expect(useDocumentStore.getState().past.length).toBe(histBefore + 1)
  })

  it('渲染还没回来时再粘一次：说「还不能复制」、不叠写（等权威清单）；回来后再粘得到第二只、gid 不同', () => {
    const data = copied('axes_0.title')
    handlePasteEvent(pasteEvent(data))
    handlePasteEvent(pasteEvent(data)) // 第一只的渲染还没回来：这一版清单已不是权威
    expect(copyOfs()).toHaveLength(1)
    expect(formatMessage(useUiStore.getState().status)).toContain('还不能复制')
    seedExact(panelNow(), manifest())
    handlePasteEvent(pasteEvent(data))
    const gids = copyOfs().map((o) => o.gid)
    expect(new Set(gids).size).toBe(2)
  })
})

describe('⌘V：落在别处照旧落成标注（0163 行为逐字不变）', () => {
  const asAnnotation = (data: string) => {
    const before = texts().length
    const histBefore = useDocumentStore.getState().past.length
    expect(handlePasteEvent(pasteEvent(data))).toBe(true)
    expect(copyOfs()).toHaveLength(0)
    expect(texts().length).toBe(before + JSON.parse(data).objects.length)
    expect(useDocumentStore.getState().past.length).toBe(histBefore + 1) // 一次粘贴一条历史
    return texts().slice(before)
  }

  it('不在图内编辑（画布上）：落成标注、选中新标注', () => {
    const data = copied('axes_0.title', 'axes_0.ylabel')
    useUiStore.setState({ elementPanelId: null, selectedGids: [] })
    const pasted = asAnnotation(data)
    expect(useSelectionStore.getState().ids).toEqual(pasted.map((o) => o.id))
    // y 轴标签带 -90；同画布粘贴向右下错开 DUPLICATE_OFFSET_MM（与 ⌘D 同一常量）
    const y = pasted.find((o) => o.text === 'Conversion')!
    expect(y.rotationDeg).toBe(-90)
    const raw = JSON.parse(data).objects.find((o: TextObject) => o.text === 'Conversion')
    expect(y.x).toBeCloseTo(raw.x + DUPLICATE_OFFSET_MM, 9)
    expect(y.y).toBeCloseTo(raw.y + DUPLICATE_OFFSET_MM, 9)
    useDocumentStore.getState().undo()
    expect(texts().length).toBe(0)
    useDocumentStore.getState().redo()
    expect(texts().length).toBe(2)
  })

  it('在别的图的图内编辑里：落成标注，并退出图内编辑回到画布层（标注的 0163 行为）', () => {
    const data = copied('axes_0.title')
    useDocumentStore.getState().commit({ key: 'test' } as never, (d) => {
      d.objects.push({ ...panel(), id: 'p2', x: 150 } as PanelObject)
    })
    useUiStore.setState({ elementPanelId: 'p2', selectedGids: [] })
    const pasted = asAnnotation(data)
    expect(useUiStore.getState().elementPanelId).toBeNull()
    expect(useSelectionStore.getState().ids).toEqual(pasted.map((o) => o.id))
  })

  it('别的文档（docId 不同）的负载：落成标注', () => {
    const payload = JSON.parse(copied('axes_0.title'))
    payload.elements.docId = 'someone-else'
    asAnnotation(JSON.stringify(payload))
    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  it('没有 elements 的旧负载（升级前的标签页）：落成标注', () => {
    const payload = JSON.parse(copied('axes_0.title'))
    delete payload.elements
    asAnnotation(JSON.stringify(payload))
  })


  it('标注留在原来的工作区：粘出来的只有文字、没有第二张图（单图编辑保持单图编辑）', () => {
    const data = copied('axes_0.title')
    useUiStore.setState({ elementPanelId: null })
    handlePasteEvent(pasteEvent(data))
    expect(doc().objects.filter((o) => o.type === 'panel')).toHaveLength(1)
  })
})

describe('右键弹层：复制 / 粘贴 / 创建副本 / 粘贴为标注', () => {
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
  // 元素弹层与对象右键菜单不会同时开着，所以这里沿用对象菜单同名的 `copy` / `paste` / `duplicate` 锚点（在 body 里取到的就是弹层里的）
  const item = (name: string) => document.body.querySelector<HTMLButtonElement>(`[data-quick-item="${name}"]`)
  const items = () => [...document.body.querySelectorAll('[data-quick-item]')].map((n) => n.getAttribute('data-quick-item'))

  it('文字元素：四项都在且可点，没有「复制为标注」；复制写剪贴板（标注 + 副本两种落法）并关掉弹层', async () => {
    restore = stubClipboardEngine(CHROMIUM_UA, true)
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText, readText: async () => '' }, configurable: true })
    open('axes_0.title')
    expect(items().filter((i) => ['copy', 'paste', 'duplicate', 'paste-as-annotation'].includes(i!))).toEqual([
      'copy',
      'paste',
      'duplicate',
      'paste-as-annotation',
    ])
    expect(item('copy-as-annotation')).toBeNull()
    for (const n of ['copy', 'paste', 'duplicate', 'paste-as-annotation']) expect(item(n)!.disabled, n).toBe(false)
    // 键位：复制 ⌘C、粘贴 ⌘V、创建副本 ⌘D，「粘贴为标注」没有快捷键
    expect(item('copy')!.textContent).toContain('C')
    expect(item('paste')!.textContent).toContain('V')
    expect(item('duplicate')!.textContent).toContain('D')
    expect(item('paste-as-annotation')!.querySelector('kbd')).toBeNull()
    await act(async () => {
      item('copy')!.click()
    })
    expect(writeText).toHaveBeenCalledTimes(1)
    const payload = JSON.parse((writeText.mock.calls[0] as unknown as [string])[0])
    expect(payload.objects[0]).toMatchObject({ type: 'text', text: 'Reaction $k^{2}$' })
    expect(payload.elements.items).toEqual([{ gid: 'axes_0.title' }])
    expect(useQuickEdit.getState().target).toBeNull()
  })

  it('创建副本：写 copy_of、关掉弹层、仍在图内编辑', () => {
    restore = stubClipboardEngine(CHROMIUM_UA, true)
    open('axes_0.title')
    act(() => item('duplicate')!.click())
    expect(copyOfs()).toHaveLength(1)
    expect(useQuickEdit.getState().target).toBeNull()
    expect(useUiStore.getState().elementPanelId).toBe('p1')
  })

  it('粘贴 → 建图内副本；粘贴为标注 → 落成标注并退出图内编辑（读剪贴板的两个入口）', async () => {
    restore = stubClipboardEngine(CHROMIUM_UA, true)
    const data = copied('axes_0.title')
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => {}, readText: async () => data }, configurable: true })
    open('axes_0.title')
    await act(async () => {
      item('paste')!.click()
    })
    expect(copyOfs()).toHaveLength(1)
    expect(texts()).toHaveLength(0)
    expect(useUiStore.getState().elementPanelId).toBe('p1')
    open('axes_0.title')
    await act(async () => {
      item('paste-as-annotation')!.click()
    })
    expect(copyOfs()).toHaveLength(1) // 没有再建副本
    expect(texts()).toHaveLength(1)
    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  it('曲线：「复制」「创建副本」置灰，其余现有项不变（全部属性仍在）', () => {
    restore = stubClipboardEngine(CHROMIUM_UA, true)
    open('axes_0.lines_0')
    expect(item('copy')!.disabled).toBe(true)
    expect(item('duplicate')!.disabled).toBe(true)
    expect(item('open-inspector')).not.toBeNull()
  })

  it('复制得了标注、复制不成副本的文字（闭集外）：「复制」可点、「创建副本」置灰', () => {
    restore = stubClipboardEngine(CHROMIUM_UA, true)
    const m = manifest()
    m.elements.push(el('axes_0.legend.texts_0', 'legend_text', '图例文字', { ...TB, text: 'A' }))
    seedExact(panel(), m)
    open('axes_0.legend.texts_0')
    expect(item('copy')!.disabled).toBe(false)
    expect(item('duplicate')!.disabled).toBe(true)
  })

  it('权威 manifest 不在：「创建副本」置灰', () => {
    restore = stubClipboardEngine(CHROMIUM_UA, true)
    useRenderStore.getState().patch(renderKeyOf(panel()), { stale: true })
    open('axes_0.title')
    expect(item('duplicate')!.disabled).toBe(true)
  })

  it('WebKit / 没有 readText：不摆「粘贴」「粘贴为标注」（⌘V 照常可用）', () => {
    restore = stubClipboardEngine(CHROMIUM_UA, false)
    open('axes_0.title')
    expect(item('copy')).not.toBeNull()
    expect(item('duplicate')).not.toBeNull()
    expect(item('paste')).toBeNull()
    expect(item('paste-as-annotation')).toBeNull()
  })

  it('入口函数对曲线只出胶囊、返回 false、不写剪贴板', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    expect(await copyFigureElements('p1', ['axes_0.lines_0'])).toBe(false)
    expect(writeText).not.toHaveBeenCalled()
    expect(formatMessage(useUiStore.getState().status)).toBe('曲线还不能复制')
  })
})

describe('强制落成标注的入口', () => {
  it('pasteObjects({ asAnnotation }) 在同图图内编辑里也不建副本', async () => {
    const data = copied('axes_0.title')
    Object.defineProperty(navigator, 'clipboard', { value: { readText: async () => data }, configurable: true })
    await pasteObjects({ asAnnotation: true })
    expect(copyOfs()).toHaveLength(0)
    expect(texts()).toHaveLength(1)
  })
})
