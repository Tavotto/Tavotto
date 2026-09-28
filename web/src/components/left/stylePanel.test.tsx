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
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
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
import {
  bindCanvasStyle,
  editBoundStyle,
  followLibrary,
  resetStyleBindingSession,
  styleEditScope,
} from '@/store/styleBinding'
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

describe('问题只在「问题」面板里看（用户 2026-09-26）：样式页不渲染问题记号', () => {
  it('标题在页面上只有 4.2 pt、预检报了阻断：样式页每条控件行里只有控件格，没有等级图标、没有跳转钮', async () => {
    // 7 pt × 0.6 = 4.2 pt，低于默认规范的绝对下限 8 pt
    await seed(panel, manifest(7))
    runValidation()
    await mount()
    const issue = useValidationStore
      .getState()
      .issues.find((i) => i.objectRef.gid === 'axes_0.title' && i.propertyPath === 'fontsize')
    expect(issue, '夹具该让预检报出这一条').toBeTruthy()
    expect(input('标题字号').value).toBe('4.2')
    const lines = [...container.querySelectorAll('[data-style-line]')]
    expect(lines.length, '判据要量到控件行').toBeGreaterThan(4)
    // 正面形式：每条控件行的子节点都是一格控件（`data-style-cell`）或粗 / 斜体开关（`data-style-face`）
    for (const line of lines) {
      for (const child of line.children) {
        expect(
          child.hasAttribute('data-style-cell') || child.hasAttribute('data-style-face'),
          `${line.getAttribute('data-style-line')} 里多了一个不是控件的子节点：${child.outerHTML.slice(0, 120)}`,
        ).toBe(true)
      }
    }
    // 行只有两列：标签 + 控件列
    const row = container.querySelector('[data-style-row="title"]')!
    expect(row.children).toHaveLength(2)
    expect(row.className).toContain('grid-cols-[4rem_minmax(0,1fr)]')
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

describe('Codex #662 P2：绑定样式时连点，按最后一次排进去的值算，不按存库前的旧读数', () => {
  /** 存库每一笔都卡住，由用例放行：模拟慢的样式库请求 */
  function slowLibrary() {
    const saves: { data: StyleData; release: () => void }[] = []
    useProfileStore.setState({
      save: (_k, _id, data) =>
        new Promise((resolve) => {
          const rec = { ...styleRecord, data } as never
          saves.push({
            data: data as StyleData,
            release: () => {
              useProfileStore.setState({ styles: [rec] })
              resolve(rec)
            },
          })
        }),
    })
    return saves
  }
  type StyleData = { element: Record<string, Record<string, unknown>>; annotation?: Record<string, unknown> }
  const faceManifest = () => ({
    ...manifest(),
    elements: [
      ...manifest().elements.filter((e) => e.role !== 'title'),
      el('axes_0.title', 'title', [
        num('fontsize', 15),
        fam('serif'),
        { prop: 'weight', type: 'enum', value: 'normal', options: ['normal', 'bold'] },
        { prop: 'style', type: 'enum', value: 'normal', options: ['normal', 'italic'] },
      ]),
    ],
  })
  const button = (label: string) =>
    container.querySelector<HTMLButtonElement>(`button[aria-label^="${label}"]`)!

  /** 让队列往前走一步：库写入是串行队列，前一笔落定之后下一笔才发出去 */
  const drain = () => act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve()
  })

  it('正体上连点两下「标题加粗」（第一笔还没存完）：排进去的是 bold 再 normal，最后回到正体；按钮当场显示按下', async () => {
    await seed(panel, faceManifest())
    const saves = slowLibrary()
    bindCanvasStyle('s1')
    await new Promise((r) => setTimeout(r, 50))
    seedExactRender(current(), faceManifest() as never)
    await mount()
    await click(button('标题加粗'))
    expect(button('标题加粗').getAttribute('aria-pressed'), '排进去了就显示按下，不等存库').toBe('true')
    await click(button('标题加粗'))
    expect(button('标题加粗').getAttribute('aria-pressed')).toBe('false')
    await drain()
    saves[0].release()
    await drain()
    // 第一笔落到画布上（weight = bold），引擎按它画回来——样式只认精确 manifest（ADR 0081）
    const bolded = faceManifest()
    bolded.elements = bolded.elements.map((e) =>
      e.gid === 'axes_0.title'
        ? {
            ...e,
            editable: (e.editable as { prop: string }[]).map((f) =>
              f.prop === 'weight' ? { ...f, value: 'bold' } : f,
            ),
          }
        : e,
    )
    await act(async () => seedExactRender(current(), bolded as never))
    saves[1].release()
    await drain()
    expect(saves.map((x) => x.data.element.title.weight)).toEqual(['bold', 'normal'])
    const w = current().overrides.filter((o) => o.gid === 'axes_0.title' && o.prop === 'weight').at(-1)?.value
    expect(w ?? 'normal').toBe('normal')
  })

  it('数字框按两下 ↑（第一笔还没存完）：第二下在第一下的值上再加一格，不是两次都从旧值算', async () => {
    await seed(panel, faceManifest())
    const saves = slowLibrary()
    bindCanvasStyle('s1')
    await new Promise((r) => setTimeout(r, 50))
    seedExactRender(current(), faceManifest() as never)
    await mount()
    const box = input('标题字号')
    const start = Number(box.value)
    for (let i = 0; i < 2; i++) {
      await act(async () => {
        box.focus()
        box.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }))
      })
    }
    await drain()
    saves[0].release()
    await drain()
    saves[1]?.release()
    await drain()
    expect(saves.map((x) => x.data.element.title.fontsize)).toEqual([start + 0.5, start + 1])
  })

  it('#688 挂着一笔没落定时切画布：新画布的图从真实读数起算，不显示上一张的挂起值；那一笔照旧存进样式库', async () => {
    await seed(panel, faceManifest())
    const saves = slowLibrary()
    bindCanvasStyle('s1')
    await new Promise((r) => setTimeout(r, 50))
    seedExactRender(current(), faceManifest() as never)
    await mount()
    await click(button('标题加粗'))
    expect(button('标题加粗').getAttribute('aria-pressed')).toBe('true')
    // 另一张画布上一张正体标题的图（id 在项目内唯一，引擎读数与 A 上那张一样）
    const other: PanelObject = { ...panel, id: 'p2' }
    await act(async () => {
      const b = s().addCanvas('B')
      s().switchCanvas(b)
      s().commit(literal('B 上放图'), (d) => {
        d.page = { w: 80, h: 60 }
        d.objects = [{ ...other }]
      })
      seedExactRender(other, faceManifest() as never)
      useSelectionStore.getState().set(['p2'])
    })
    const onB = button('标题加粗')?.getAttribute('aria-pressed')
    // 先放行再断言：断言先红的话那一笔卡在队列里，会把后面的用例一起堵住
    await drain()
    saves[0].release()
    await drain()
    expect(onB, 'A 上那一笔的挂起值串到了 B').toBe('false')
    expect(saves.map((x) => x.data.element.title.weight)).toEqual(['bold'])
    // 回到 A：库里那条变过，A 按库跟上（同步器在切画布时调的就是 followLibrary）——那一笔没被重挂丢掉
    await act(async () => {
      s().switchCanvas(s().canvases[0].id)
      useSelectionStore.getState().set(['p1'])
      followLibrary()
    })
    const w = current().overrides.filter((o) => o.gid === 'axes_0.title' && o.prop === 'weight').at(-1)?.value
    expect(w).toBe('bold')
  })

  it('#688 挂着一笔没落定时整份重载同一份文档（文档 / 画布 / 图的 id 全同、载入代次前进）：重载后的图从真实读数起算', async () => {
    await seed(panel, faceManifest())
    const saves = slowLibrary()
    bindCanvasStyle('s1')
    await new Promise((r) => setTimeout(r, 50))
    seedExactRender(current(), faceManifest() as never)
    await mount()
    await click(button('标题加粗'))
    expect(button('标题加粗').getAttribute('aria-pressed')).toBe('true')
    const ids = [s().documentId, s().activeCanvasId]
    const seqBefore = s().loadSeq
    await act(async () => {
      await s().switchDocument(s().buildProject(), s().documentId)
      seedExactRender(current(), faceManifest() as never)
      useSelectionStore.getState().set(['p1'])
    })
    expect([s().documentId, s().activeCanvasId], '夹具要的就是 id 全同的重载').toEqual(ids)
    expect(s().loadSeq).toBeGreaterThan(seqBefore)
    const afterReload = button('标题加粗')?.getAttribute('aria-pressed')
    // 先放行再断言：断言先红的话那一笔卡在队列里，会把后面的用例一起堵住
    await drain()
    saves[0].release()
    await drain()
    expect(afterReload, '重载前那一笔的挂起值盖到了新载入的内容上').toBe('false')
  })

  /**
   * `styleEditScope` 的每一维各一行：**只变这一维**，挂起值不许串到变化之后的那张图上。
   * 真实读数一律是正体（夹具的 manifest），所以按钮该是 `aria-pressed=false`。
   */
  const other = { ...styleRecord, id: 's2', display_name: '另一套' }
  const DIMENSIONS: [string, () => void][] = [
    ['项目', () => {
      setCurrentProjectId('proj_other')
      // 项目 id 不在 store 里：真实换项目总伴着一次文档替换，这里只推一下 store 让选择器重算
      useDocumentStore.setState({ dirty: !s().dirty })
    }],
    ['文档 id', () => useDocumentStore.setState({ documentId: 'd_other' })],
    ['载入代次', () => useDocumentStore.setState({ loadSeq: s().loadSeq + 1 })],
    ['画布', () => useDocumentStore.setState({ activeCanvasId: 'c_other' })],
    ['图（面板 id）', () => {
      s().commit(literal('换一张图'), (d) => {
        d.objects = d.objects.map((o) => (o.id === 'p1' ? { ...o, id: 'p9' } : o))
      })
      seedExactRender(current9(), faceManifest() as never)
      useSelectionStore.getState().set(['p9'])
    }],
    ['素材（同 id 换文件）', () => {
      useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 }, 'Fig2.pdf': { id: 'Fig2.pdf', mtime: 1 } } } as never)
      s().commit(literal('替换素材'), (d) => {
        d.objects = d.objects.map((o) => (o.id === 'p1' ? { ...o, fileId: 'Fig2.pdf', overrides: [] } : o))
      })
      seedExactRender(current(), faceManifest() as never)
    }],
    ['改绑到另一套样式', () => {
      s().commit(literal('改绑'), (d) => void (d.style = { ...d.style!, id: 's2' }))
    }],
    ['解绑 / 脱离', () => {
      s().commit(literal('脱离'), (d) => void (d.style!.detached = true))
    }],
  ]
  const current9 = () => s().doc.objects.find((o) => o.id === 'p9') as PanelObject

  it.each(DIMENSIONS)('#688 挂着一笔没落定时只变「%s」：变化之后的图从真实读数起算', async (_name, change) => {
    const project = currentProjectId()
    try {
      await seed(panel, faceManifest())
      useProfileStore.setState({ styles: [styleRecord as never, other as never] })
      const saves = slowLibrary()
      bindCanvasStyle('s1')
      await new Promise((r) => setTimeout(r, 50))
      seedExactRender(current(), faceManifest() as never)
      await mount()
      await click(button('标题加粗'))
      expect(button('标题加粗').getAttribute('aria-pressed')).toBe('true')
      await act(async () => change())
      const after = button('标题加粗')?.getAttribute('aria-pressed')
      // 先放行再断言：断言先红的话那一笔卡在队列里，会把后面的用例一起堵住
      await drain()
      saves[0]?.release()
      await drain()
      expect(after, '挂起值串到了变化之后的图上').toBe('false')
    } finally {
      setCurrentProjectId(project)
    }
  })

  it('#688 反向：改内置样式时我们自己复制成副本并改绑（同一串编辑），归属不变——排在后面那一笔的挂起值不因此被清掉', async () => {
    // 量的是归属函数本身：组件层面这一刻夹具里的显示 manifest 会短暂缺席（override 变了、新一版没画回来），
    // 整个面板先换成「需要渲染」的空态，量不到行是否因 key 重挂
    await seed(panel, faceManifest())
    const builtIn = { ...styleRecord, built_in: true, read_only: true }
    const saves: { release: () => void }[] = []
    useProfileStore.setState({
      styles: [builtIn as never],
      duplicate: async () => {
        const copy = { ...styleRecord, id: 's1copy', display_name: '投稿用 副本' }
        useProfileStore.setState({ styles: [builtIn as never, copy as never] })
        return copy as never
      },
      save: (_k, id, data) =>
        new Promise((resolve) => {
          const rec = { ...styleRecord, id, data } as never
          saves.push({
            release: () => {
              useProfileStore.setState((st) => ({ styles: [...st.styles.filter((x) => x.id !== id), rec] }))
              resolve(rec)
            },
          })
        }),
    })
    bindCanvasStyle('s1')
    await new Promise((r) => setTimeout(r, 50))
    seedExactRender(current(), faceManifest() as never)
    const before = styleEditScope(s(), current())
    // 连点两下：第一笔复制内置并改绑，第二笔还排着（挂起值就挂在它身上）
    const first = editBoundStyle({ kind: 'element', role: 'title', prop: 'weight', value: 'bold' })
    const second = editBoundStyle({ kind: 'element', role: 'title', prop: 'weight', value: 'normal' })
    await drain()
    saves[0].release()
    expect(await first).toBe(true)
    expect(s().doc.style?.id, '夹具要的就是「复制成副本并改绑」那条路').toBe('s1copy')
    expect(styleEditScope(s(), current()), '第二笔还排着时归属变了：它的挂起值会被清掉').toBe(before)
    await drain()
    saves[1].release()
    expect(await second, '第二笔顺着转发落到副本上').toBe(true)
    // 对照：用户自己改绑到副本（不是这一串编辑的转发）——归属变了
    s().commit(literal('改绑'), (d) => void (d.style = { ...d.style!, id: 's2' }))
    expect(styleEditScope(s(), current())).not.toBe(before)
  })

  it('那一笔存失败（库写不进去）：放掉挂着的值，按钮回到真实读数', async () => {
    await seed(panel, faceManifest())
    bindCanvasStyle('s1')
    await new Promise((r) => setTimeout(r, 50))
    seedExactRender(current(), faceManifest() as never)
    let fail: () => void = () => {}
    useProfileStore.setState({ save: () => new Promise((resolve) => (fail = () => resolve(null))) })
    await mount()
    await click(button('标题加粗'))
    expect(button('标题加粗').getAttribute('aria-pressed')).toBe('true')
    await drain()
    await act(async () => fail())
    await drain()
    expect(button('标题加粗').getAttribute('aria-pressed')).toBe('false')
  })
})

describe('排版：两列网格、「多个值」放得下且有说明', () => {
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

  it('值格与刻度方向下拉同一个宽、都从控件列左缘起排', async () => {
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
    const valueW = (sel: Element) => sel.className.match(/w-\[[^\]]+\]/)?.[0]
    const size = input('标题字号').closest('.group')!
    const direction = container.querySelector('[data-style-cell="tickDirection"] [role="combobox"]')!
    expect(valueW(size)).toBeTruthy()
    expect(valueW(direction)).toBe(valueW(size))
    // 刻度长度是页面 pt：3.5 × 0.6 = 2.1
    expect(input('刻度长度').value).toBe('2.1')
  })
})
