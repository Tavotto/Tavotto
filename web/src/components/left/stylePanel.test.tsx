/**
 * 左栏「样式」面板（2026-09-24）。
 *
 * 逐条量用户实测暴露出来的那几件事：
 *
 * 1. 显示的是**页面上**的字号（manifest 值 × 面板缩放比），不是脚本里的原始值；
 * 2. 几个元素不一致时是「多个值」，不拿第一个冒充全部；
 * 3. 改字号按缩放比换算回脚本值再写，一次 commit、⌘Z 一次撤回；
 * 4. 应用样式 = 一次 commit，可撤销；
 * 5. 恢复原样只清样式管得到的那批 override，一次 commit，可撤销；
 * 6. 不合规的那一格能跳到问题面板里对应的那一条。
 *
 * 夹具：原生 80 mm 宽的图在页面上 48 mm 宽（缩放比 0.6）——正是用户那种「缩小放进
 * 拼版」的情形；标题脚本值 15 pt，读者量到 9 pt。
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createRoot, type Root } from 'react-dom/client'
import { literal, setLocale } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { runValidation, useValidationStore } from '@/store/validationStore'
import { useWorkspaceStore } from '@/store/workspace'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { bindCanvasStyle, resetStyleBindingSession } from '@/store/styleBinding'
import { SEVERITY_INK, severityLabel } from '@/lib/validationText'
import { StylePanel } from './StylePanel'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  script: 'fig1.py',
  name: 'Fig1',
  nativeW: 80,
  nativeH: 60,
  x: 0,
  y: 0,
  w: 48,
  h: 36,
  overrides: [],
}

const num = (prop: string, value: number) => ({ prop, type: 'number', value, min: 3, max: 36 })
const fam = (value: string) => ({
  prop: 'fontfamily',
  type: 'enum',
  value,
  options: ['serif', 'sans-serif', 'Times New Roman'],
})
const el = (gid: string, role: string, editable: unknown[]) => ({
  gid,
  role,
  label: gid,
  bbox: [0.1, 0.1, 0.2, 0.05],
  draggable: false,
  editable,
})

/** 两条刻度组字号不一致（8 与 10）：刻度那一格该是「多个值」 */
const manifest = (titleSize = 15) => ({
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: [
    el('axes_0', 'axes', [num('spine_linewidth', 1.25)]),
    el('axes_0.title', 'title', [num('fontsize', titleSize), fam('serif')]),
    el('axes_0.xlabel', 'axis_label', [num('fontsize', 15), fam('serif')]),
    el('axes_0.xticks', 'ticks', [num('fontsize', 8), fam('serif')]),
    el('axes_0.yticks', 'ticks', [num('fontsize', 10), fam('serif')]),
    el('axes_0.lines_0', 'line', [num('linewidth', 2)]),
  ],
})

const styleRecord = {
  id: 's1',
  kind: 'style',
  schema_version: 1,
  revision: 1,
  display_name: '投稿用',
  name_key: '',
  version: '',
  created_at: 0,
  updated_at: 0,
  built_in: false,
  read_only: false,
  is_default: false,
  derived_from: '',
  warnings: [],
  data: { element: { title: { fontsize: 12 }, axis_label: { fontsize: 12 } }, background: '#eeeeee', pt_basis: 'page' },
}

let container: HTMLDivElement
let root: Root
const s = () => useDocumentStore.getState()
const current = () => s().doc.objects.find((o) => o.id === 'p1') as PanelObject

async function seed(p: PanelObject = panel, m: unknown = manifest()) {
  await s().switchDocument(emptyProject(), 'd_style_panel')
  s().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 60 }
    d.objects = [{ ...p }]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  seedExactRender(p, m as never)
  // 「当前图」= 选中的面板（与问题面板同一个判据）
  useSelectionStore.getState().set(['p1'])
}

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <StylePanel />
      </TooltipProvider>,
    )
  })
}

const input = (label: string) =>
  container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!

/** 在数字框里敲一个数并回车（NumberField 回车才提交） */
async function type(el: HTMLInputElement, value: string) {
  await act(async () => {
    el.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  })
}

const click = async (el: Element) =>
  act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })

beforeEach(() => {
  useUiStore.setState({
    problemFilter: null,
    problemScope: null,
    problemCursor: null,
    elementPanelId: null,
    leftTab: 'style',
    leftOpen: true,
    layout: 'wide',
  })
  useWorkspaceStore.getState().clear()
  useSelectionStore.getState().clear()
  useValidationStore.setState({ results: [], issues: [], ready: false, failed: false, running: false })
  useProfileStore.setState({ styles: [styleRecord as never], loaded: true, error: null })
})

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
})

describe('显示：页面上的有效字号，四档不压扁', () => {
  it('字号 = 脚本值 × 面板缩放比（15 pt × 0.6 = 9 pt），线宽同理', async () => {
    await seed()
    await mount()
    expect(input('标题字号').value).toBe('9')
    expect(input('轴标题字号').value).toBe('9')
    expect(input('数据线宽').value).toBe('1.2')
    expect(input('边框线宽').value, '0.75 不显示成 0.8').toBe('0.75')
  })

  it('两条刻度组不一致时是「多个值」：框里留空、不拿第一个冒充全部', async () => {
    await seed()
    await mount()
    const ticks = input('刻度字号')
    expect(ticks.value).toBe('')
    expect(ticks.placeholder).toBeTruthy()
  })

  it('没有当前图时一句话 + 一个行动', async () => {
    await seed()
    useSelectionStore.getState().clear()
    await mount()
    expect(container.querySelector('[data-style-panel]')).toBeNull()
    // 空态只有一个行动；画布一级的「跟随样式」照样在底部（它不依赖选中哪张图）
    const empty = container.querySelector('[data-style-panel-empty]')!
    const outside = [...empty.querySelectorAll('button')].filter((b) => !b.closest('[data-style-apply]'))
    expect(outside).toHaveLength(1)
    expect(empty.querySelector('[data-style-apply]')).not.toBeNull()
  })
})

describe('改字号：按缩放比换算回脚本值，一次 commit', () => {
  it('在页面上写 10 pt → override 写 10 / 0.6 = 16.67；⌘Z 一次回到原样', async () => {
    await seed()
    await mount()
    const before = s().past.length
    await type(input('标题字号'), '10')
    expect(current().overrides).toEqual([{ gid: 'axes_0.title', prop: 'fontsize', value: 16.67 }])
    expect(s().past.length - before).toBe(1)
    expect(s().undo()).not.toBeNull()
    expect(current().overrides).toEqual([])
  })
})

describe('画布跟随样式（ADR 0081）：底部选择器 = 绑定，绑定后改值 = 改样式本身', () => {
  /** 同一张画布上第二张图：原生 80 mm、页面上 80 mm（缩放比 1），轴标题脚本值 9 pt */
  const other: PanelObject = { ...panel, id: 'p2', fileId: 'Fig2.pdf', name: 'Fig2', w: 80, h: 60, x: 60 }

  async function seedTwo() {
    await seed()
    s().commit(literal('第二张图'), (d) => {
      d.objects.push({ ...other })
    })
    seedExactRender(other, manifest(15) as never)
    resetStyleBindingSession()
  }

  it('没绑时选择器写「不跟随样式」；绑上之后写样式名', async () => {
    await seed()
    await mount()
    const combo = () => container.querySelector('[data-style-apply] [role="combobox"]')!
    expect(combo().textContent).toBe('不跟随样式')
    await act(async () => bindCanvasStyle('s1'))
    expect(combo().textContent).toBe('投稿用')
  })

  it('已脱离（撤销过样式修改）：选择器写「已脱离「X」」、提示换成脱离那一句；选回那套样式恢复跟随', async () => {
    await seed()
    await mount()
    await act(async () => bindCanvasStyle('s1'))
    await act(async () => {
      s().commit(literal('脱离'), (d) => void (d.style!.detached = true))
    })
    const combo = () => container.querySelector('[data-style-apply] [role="combobox"]')!
    expect(combo().textContent).toBe('已脱离「投稿用」')
    expect(container.querySelector('[data-style-bind-hint]')?.textContent).toBe('这张画布不再跟随样式；重新选中它即恢复。')
    await act(async () => bindCanvasStyle('s1'))
    expect(s().doc.style?.detached).toBeUndefined()
    expect(combo().textContent).toBe('投稿用')
  })

  it('绑定之后在面板里改轴标题字号：先存进样式库，画布上两张图一起变（按各自缩放比），一条历史', async () => {
    await seedTwo()
    const saves: unknown[] = []
    useProfileStore.setState({
      save: async (_k, id, data) => {
        saves.push({ id, data })
        const rec = { ...styleRecord, data } as never
        useProfileStore.setState({ styles: [rec] })
        return rec
      },
    })
    bindCanvasStyle('s1')
    // 绑定顺手发出的那次渲染请求先落完（测试里没有真引擎，它会以失败收场）
    await new Promise((r) => setTimeout(r, 50))
    // 绑定写了 override，渲染变体换了键：挂上与新 override 对得上的渲染（真引擎回来那一刻）
    for (const id of ['p1', 'p2']) {
      const p = s().doc.objects.find((o) => o.id === id) as PanelObject
      seedExactRender(p, manifest(15) as never)
    }
    await mount()
    const before = s().past.length
    await type(input('轴标题字号'), '10')
    await act(async () => {})
    expect(saves).toHaveLength(1)
    expect(s().past.length - before, '两张图的改动是同一条历史').toBe(1)
    const xlabel = (id: string) =>
      (s().doc.objects.find((o) => o.id === id) as PanelObject).overrides.find((o) => o.gid === 'axes_0.xlabel')?.value
    expect(xlabel('p1')).toBe(16.67)
    expect(xlabel('p2')).toBe(10)
    expect(s().undo()).not.toBeNull()
    expect(xlabel('p1')).toBe(20)
    expect(xlabel('p2')).toBe(12)
  })

  it('恢复原样：整张画布清掉样式管得到的 override 并解绑，一条历史，可撤销', async () => {
    const edited: PanelObject = {
      ...panel,
      overrides: [
        { gid: 'axes_0.title', prop: 'fontsize', value: 20 },
        { gid: 'axes_0.title', prop: 'text', value: '改过的标题' },
      ],
    }
    await seed(edited)
    s().commit(literal('绑定'), (d) => {
      d.style = { id: 's1', snapshot: styleRecord.data }
    })
    await mount()
    const restore = container.querySelector('[data-style-restore]')!
    expect(restore.textContent).toContain('1')
    const before = s().past.length
    await click(restore)
    expect(s().past.length - before).toBe(1)
    expect(current().overrides).toEqual([{ gid: 'axes_0.title', prop: 'text', value: '改过的标题' }])
    expect(s().doc.style).toBeUndefined()
    expect(s().undo()).not.toBeNull()
    expect(current().overrides).toHaveLength(2)
    expect(s().doc.style?.id).toBe('s1')
  })

  it('脚本重跑后与样式不一致（用户 2026-09-25 裁决：不自动对齐）：显示处数与「对齐」，点一次对齐、一条历史；不一致为 0 时整行不在', async () => {
    await seed()
    bindCanvasStyle('s1')
    seedExactRender(current(), manifest(15) as never)
    await mount()
    expect(container.querySelector('[data-style-mismatch]'), '已经合样式：不摆提示').toBeNull()
    // 同一素材重跑：多了一个 y 轴标签（脚本 15 pt，读者量到 9 pt），样式不自动写
    await act(async () => {
      useRenderStore.getState().markStale(['Fig1.pdf'])
      const m = manifest(15)
      seedExactRender(current(), {
        ...m,
        elements: [...m.elements, el('axes_0.ylabel', 'axis_label', [num('fontsize', 15), fam('serif')])],
      } as never)
    })
    const row = container.querySelector('[data-style-mismatch]')!
    expect(row.textContent).toContain('1 处与样式不一致')
    const before = s().past.length
    await click(row.querySelector('[data-style-align]')!)
    expect(s().past.length - before).toBe(1)
    expect(current().overrides.find((o) => o.gid === 'axes_0.ylabel')?.value).toBe(20)
  })

  it('没绑、也没有可恢复的：不摆恢复钮（没有禁用的恢复钮）', async () => {
    await seed()
    await mount()
    expect(container.querySelector('[data-style-restore]')).toBeNull()
  })
})

describe('不合规的那一格 → 问题面板里的那一条', () => {
  it('标题在页面上只有 4.2 pt：那一格带标记，点它切到问题面板、游标落在这一条上', async () => {
    // 7 pt × 0.6 = 4.2 pt，低于默认规范的绝对下限 8 pt
    await seed(panel, manifest(7))
    runValidation()
    await mount()
    const issue = useValidationStore
      .getState()
      .issues.find((i) => i.objectRef.gid === 'axes_0.title' && i.propertyPath === 'fontsize')
    expect(issue, '夹具该让预检报出这一条').toBeTruthy()
    expect(input('标题字号').value).toBe('4.2')
    // 记号落在出问题那一格所在的那一行（字号那一行），不在字体那一行；没问题的行没有记号
    const mark = container.querySelector('[data-style-line="title.size"] [data-style-issue="title"]')!
    expect(mark, '字号那一行带记号').toBeTruthy()
    expect(container.querySelector('[data-style-line="title.family"] [data-style-issue]')).toBeNull()
    expect(container.querySelector('[data-style-line="axis_label.size"] [data-style-issue]')).toBeNull()
    // 等级与问题面板同一套：阻断 = 红色（不再和警告共用一种颜色）
    expect(mark.getAttribute('data-severity')).toBe(issue!.severity)
    expect(mark.className).toContain(SEVERITY_INK[issue!.severity])
    expect(mark.getAttribute('aria-label')).toContain(severityLabel(issue!.severity))

    await click(mark)
    const ui = useUiStore.getState()
    expect(ui.leftTab).toBe('problems')
    expect(ui.problemScope).toBe('figure')
    expect(ui.problemCursor?.issueId).toBe(issue!.issueId)
  })
})

describe('Codex #547 评审', () => {
  it('P2 「画布标注」那一行不含子图序号标签 (a)(b)：改它的字号不会顺手改掉序号', async () => {
    await seed()
    const text = (id: string, t: string) => ({
      id, type: 'text', text: t, sizePt: 9, bold: false, color: '#000000', align: 'left', x: 0, y: 0, w: 10, h: 5,
    })
    s().commit(literal('标注'), (d) => {
      d.objects.push(text('t1', '注释') as never, text('t2', '(a)') as never)
    })
    await mount()
    await type(input('画布标注字号'), '12')
    const size = (id: string) => (s().doc.objects.find((o) => o.id === id) as { sizePt: number }).sizePt
    expect(size('t1')).toBe(12)
    expect(size('t2'), '序号标签不跟普通标注一起改').toBe(9)
  })

  it('P2 切语言时 memo 的行也跟着换语言（行名与选项名不停在旧语言）', async () => {
    await seed()
    await mount()
    const row = () => container.querySelector('[data-style-row="frame"]')!.textContent
    expect(row()).toContain('边框线宽')
    await act(async () => {
      await setLocale('en-US')
    })
    try {
      expect(row()).toContain('Frame')
    } finally {
      await act(async () => {
        await setLocale('zh-CN')
      })
    }
  })
})

describe('Codex #547 第七轮评审', () => {
  it('P1 没绑样式、这张图的这一版还没渲染回来（只有上一版的 manifest）：控件置灰，不拿过期的 gid 写 override', async () => {
    await seed()
    // override 变了、这一版还在渲染：显示用的 manifest 是上一版
    s().commit(literal('改了别的'), (d) => {
      ;(d.objects[0] as PanelObject).overrides = [{ gid: 'axes_0.title', prop: 'color', value: '#ff0000' }]
    })
    await mount()
    expect(input('标题字号').disabled).toBe(true)
    expect(input('标题字号').closest('[title]')?.getAttribute('title')).toBe('这张图的这一版还在渲染，渲染完才能改')
    // 这一版回来了：可以改
    await act(async () => {
      seedExactRender(current(), manifest() as never)
    })
    expect(input('标题字号').disabled).toBe(false)
  })
})

describe('粗体 / 斜体（2026-09-26 用户反馈：样式栏能改的太少）', () => {
  const enumOf = (prop: string, value: string, options: string[]) => ({ prop, type: 'enum', value, options })
  const w = (v: string) => enumOf('weight', v, ['normal', 'bold'])
  const st = (v: string) => enumOf('style', v, ['normal', 'italic'])
  /** 标题正体；两条轴标题一粗一细（「多个值」）；图例项有 weight / style；刻度组没有（与引擎一致） */
  const faceManifest = () => ({
    ...manifest(),
    elements: [
      ...manifest().elements.filter((e) => e.role !== 'axis_label' && e.role !== 'title'),
      el('axes_0.title', 'title', [num('fontsize', 15), fam('serif'), w('normal'), st('normal')]),
      el('axes_0.xlabel', 'axis_label', [num('fontsize', 15), fam('serif'), w('bold'), st('normal')]),
      el('axes_0.ylabel', 'axis_label', [num('fontsize', 15), fam('serif'), w('normal'), st('normal')]),
      el('axes_0.legend', 'legend', [num('fontsize', 10)]),
      el('axes_0.legend.texts_0', 'legend_text', [fam('serif'), w('normal'), st('normal')]),
    ],
  })
  const button = (label: string) =>
    container.querySelector<HTMLButtonElement>(`button[aria-label^="${label}"]`)

  it('没绑样式：点「标题加粗」写一条 weight override，一次 commit、⌘Z 一次撤回；再点回正常', async () => {
    await seed(panel, faceManifest())
    await mount()
    expect(button('标题加粗')!.getAttribute('aria-pressed')).toBe('false')
    const before = s().past.length
    await click(button('标题加粗')!)
    expect(current().overrides).toEqual([{ gid: 'axes_0.title', prop: 'weight', value: 'bold' }])
    expect(s().past.length - before).toBe(1)
    expect(s().undo()).not.toBeNull()
    expect(current().overrides).toEqual([])
  })

  it('两条轴标题一粗一细：开关是「多个值」三态（不拿第一个冒充全部）；点一下两条都加粗', async () => {
    await seed(panel, faceManifest())
    await mount()
    const bold = button('轴标题加粗')!
    expect(bold.getAttribute('aria-pressed')).toBe('mixed')
    const before = s().past.length
    await click(bold)
    const weights = Object.fromEntries(
      current()
        .overrides.filter((o) => o.prop === 'weight')
        .map((o) => [o.gid, o.value]),
    )
    expect(weights['axes_0.ylabel']).toBe('bold')
    expect(Object.values(weights).every((v) => v === 'bold')).toBe(true)
    expect(s().past.length - before).toBe(1)
  })

  it('图例的粗 / 斜体写在图例项（legend_text）上；刻度组的引擎字段里没有 weight / style，不摆开关', async () => {
    await seed(panel, faceManifest())
    await mount()
    await click(button('图例倾斜')!)
    expect(current().overrides).toEqual([{ gid: 'axes_0.legend.texts_0', prop: 'style', value: 'italic' }])
    expect(button('刻度加粗')).toBeNull()
    expect(button('刻度倾斜')).toBeNull()
  })

  it('绑了样式：点「标题加粗」= 改样式本身（element.title.weight 存进样式库），画布跟着对齐', async () => {
    await seed(panel, faceManifest())
    const saves: { data: { element: Record<string, Record<string, unknown>> } }[] = []
    useProfileStore.setState({
      save: async (_k, _id, data) => {
        saves.push({ data } as never)
        const rec = { ...styleRecord, data } as never
        useProfileStore.setState({ styles: [rec] })
        return rec
      },
    })
    bindCanvasStyle('s1')
    await new Promise((r) => setTimeout(r, 50))
    seedExactRender(current(), faceManifest() as never)
    await mount()
    await click(button('标题加粗')!)
    await act(async () => {})
    expect(saves).toHaveLength(1)
    expect(saves[0].data.element.title.weight).toBe('bold')
    expect(current().overrides.find((o) => o.gid === 'axes_0.title' && o.prop === 'weight')?.value).toBe('bold')
  })

  it('画布标注：粗体写 TextObject.bold（没绑）；绑了样式时存成样式里 annotation.bold = true', async () => {
    await seed()
    s().commit(literal('标注'), (d) => {
      d.objects.push({
        id: 't1', type: 'text', text: '注释', sizePt: 9, bold: false, color: '#000000', align: 'left', x: 0, y: 0, w: 10, h: 5,
      } as never)
    })
    await mount()
    await click(button('画布标注加粗')!)
    expect((s().doc.objects.find((o) => o.id === 't1') as { bold: boolean }).bold).toBe(true)

    const saves: { data: { annotation?: { italic?: unknown } } }[] = []
    useProfileStore.setState({
      save: async (_k, _id, data) => {
        saves.push({ data } as never)
        const rec = { ...styleRecord, data } as never
        useProfileStore.setState({ styles: [rec] })
        return rec
      },
    })
    await act(async () => bindCanvasStyle('s1'))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
      seedExactRender(current(), manifest() as never)
    })
    await click(button('画布标注倾斜')!)
    await act(async () => {})
    expect(saves.at(-1)?.data.annotation?.italic).toBe(true)
    expect((s().doc.objects.find((o) => o.id === 't1') as { italic?: boolean }).italic).toBe(true)
  })
})

describe('排版：三列网格、「多个值」放得下且有说明', () => {
  it('「多个值」的数字框：输入框铺满定宽的值格，悬停说明「输入一个值会把它们统一」', async () => {
    await seed()
    await mount()
    const ticks = input('刻度字号')
    expect(ticks.placeholder).toBe('多个值')
    expect(ticks.className, '不再按 4 个等宽字符定宽（那样「多个值」被裁成「多个僮」）').toContain('w-full')
    expect(ticks.closest('[title]')?.getAttribute('title')).toBe(
      '这张图里这几处的值不一样；输入一个值会把它们统一',
    )
  })

  it('每条控件行都带一个定宽的状态格（有没有问题都占着）；值格与刻度方向下拉同一个宽', async () => {
    await seed(panel, {
      ...manifest(),
      elements: [
        ...manifest().elements,
        el('axes_0.yticks2', 'ticks', [
          { prop: 'direction', type: 'enum', value: 'in', options: ['out', 'in', 'inout'] },
          num('length', 3.5),
        ]),
      ],
    })
    await mount()
    const lines = [...container.querySelectorAll('[data-style-line]')]
    expect(lines.length).toBeGreaterThan(4)
    for (const l of lines) expect(l.lastElementChild!.className).toContain('w-5')
    const valueW = (sel: Element) => sel.className.match(/w-\[[^\]]+\]/)?.[0]
    const size = input('标题字号').closest('.group')!
    const direction = container.querySelector('[data-style-cell="tickDirection"] [role="combobox"]')!
    expect(valueW(size)).toBeTruthy()
    expect(valueW(direction)).toBe(valueW(size))
    // 刻度长度是页面 pt：3.5 × 0.6 = 2.1
    expect(input('刻度长度').value).toBe('2.1')
  })
})
