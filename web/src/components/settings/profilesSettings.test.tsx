/**
 * 「样式与规范」设置分区（Session 10，ADR 0029）。
 *
 * 盯着四条**产品合同**，每一条错了都不会有任何技术信号：
 *
 * 1. 默认界面**不出现内部 id 与版本号**（`lab-publication-v1 · v1.0.0`）；
 * 2. 内置只读：改内置的出口是"复制一份"，不是一个点了没反应的保存按钮；
 * 3. Style 与 Spec **不在同一张表单里混改**（切换后字段整组换掉）；
 * 4. 「本项目用这套规范」写的是**带快照的绑定**，不是一个 id。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '@/i18n'
import { ProfilesSettings } from './ProfilesSettings'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { DEFAULT_PROFILE_ID } from '@/lib/profile'
import { builtinCatalog } from '@/lib/specBinding'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/** 后端返回的形状：**内置在前、用户自建在后**（`profilestore.list_profiles`）。 */
const envelope = (over: Record<string, unknown>) => ({
  kind: 'style',
  schema_version: 1,
  revision: 1,
  name_key: '',
  version: '',
  created_at: 0,
  updated_at: 0,
  built_in: false,
  read_only: false,
  is_default: false,
  derived_from: '',
  warnings: [],
  data: {},
  ...over,
})

const BUILTIN_STYLE = envelope({
  id: 'builtin-default-style',
  display_name: '默认样式',
  name_key: 'builtin.style.default',
  built_in: true,
  read_only: true,
  is_default: true,
  data: { element: { line: { linewidth: 0.5 } } },
})

const BUILTIN_SPECS = builtinCatalog().map((e) =>
  envelope({
    id: e.id,
    kind: 'spec',
    display_name: e.display_name,
    name_key: e.name_key ?? '',
    version: e.version,
    built_in: true,
    read_only: true,
    data: e.data,
  }),
)

const USER_STYLE = {
  id: 's1',
  kind: 'style' as const,
  schema_version: 1,
  revision: 3,
  display_name: '投稿用',
  name_key: '',
  version: '',
  created_at: 0,
  updated_at: 0,
  built_in: false,
  read_only: false,
  is_default: false,
  derived_from: 'builtin-default-style',
  warnings: ['unmapped_field:从未见过'],
  data: { element: { line: { linewidth: 1.25 } } },
}

let container: HTMLDivElement
let root: Root

const text = () => document.body.textContent ?? ''
const buttons = () => [...document.body.querySelectorAll('button')]
const byText = (label: string) => buttons().find((b) => b.textContent?.trim() === label)

async function mount(kind: 'style' | 'spec' = 'style') {
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ProfilesSettings kind={kind} />
      </TooltipProvider>,
    )
  })
}

/** 「样式」与「规范」自 Session 19 起是两个分区（同一个组件按 kind 渲染）：切页 = 换 kind 重渲染 */
const switchToSpec = () => mount('spec')

beforeEach(async () => {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) =>
    new Response(
      JSON.stringify({
        profiles: String(input).includes('/style') ? [BUILTIN_STYLE, USER_STYLE] : BUILTIN_SPECS,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ),
  ) as typeof fetch
  useProfileStore.setState({ styles: [], loaded: false, error: null, conflict: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_profiles')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  if (i18n.language !== 'zh-CN') await i18n.changeLanguage('zh-CN')
  vi.restoreAllMocks()
})

describe('默认界面不暴露内部身份', () => {
  it('列表显示自然名称，id 与版本只在 title 里', async () => {
    await mount()
    expect(text()).toContain('默认样式')
    expect(text()).not.toContain('builtin-default-style')
    // 库是一行分段选择器（2026-09-15 打磨批次 B）：格子上写自然名称，技术身份在 title 里
    const row = buttons().find((b) => b.getAttribute('role') === 'radio' && b.textContent?.includes('默认样式'))!
    expect(row.getAttribute('title')).toContain('builtin-default-style')
  })

  it('内置的名字跟界面语言走，用户起的名字不翻译', async () => {
    await mount()
    expect(text()).toContain('默认样式')
    expect(text()).toContain('投稿用')
    await act(async () => {
      await i18n.changeLanguage('en-US')
    })
    await mount()
    expect(text()).toContain('Default style')
    expect(text()).toContain('投稿用')
  })
})

describe('内置只读', () => {
  it('内置那份只出规则摘要，不摆一整套禁用输入（审计 T41 / T42）', async () => {
    await mount()
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('默认样式'))!.click()
    })
    // 「改不了」是**状态 + 动作**，不是一段散文（2026-09-07，#299：那句 37 字的
    // 解释和分区说明叠成两段，把 e2e 的「一个分区最多一段长解释」顶红了）。
    // 判据认锚点与徽标，不认某一句话——文案下一轮还会被审计改。
    const readOnly = document.body.querySelector('[data-profile-readonly]')
    expect(readOnly).not.toBeNull()
    expect(readOnly!.textContent).toContain('只读')
    // 那颗按钮就在徽标旁边，不用滚到所有字段下面才找得到
    expect(
      [...readOnly!.querySelectorAll('button')].some((b) =>
        b.textContent?.includes('复制一份再修改'),
      ),
    ).toBe(true)
    // 摘要模式下右栏一个输入框都没有——整页禁用的输入看起来像"我的表单坏了"
    expect(document.body.querySelectorAll('input:not([type="file"])')).toHaveLength(0)
    // 规则本身仍然读得到（线宽 0.5 来自 BUILTIN_STYLE.data）
    expect(text()).toContain('0.5 pt')
    // 改的动作从「复制一份」开始，而不是一排点了没反应的按钮
    expect(byText('保存')).toBeUndefined()
    expect(buttons().find((b) => b.textContent?.includes('删除'))).toBeUndefined()
    expect(buttons().some((b) => b.textContent?.includes('复制一份再修改'))).toBe(true)
  })

  it('点「复制一份再修改」：复制出来的那份被选中，并且是可编辑的（审计 T41 / T42 验收）', async () => {
    const copy = envelope({
      id: 's-copy',
      display_name: '默认样式 副本',
      derived_from: 'builtin-default-style',
      data: { element: { line: { linewidth: 0.5 } } },
    })
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const body =
        init?.method === 'POST' && String(input).includes('/duplicate')
          ? { profile: copy }
          : { profiles: String(input).includes('/style') ? [BUILTIN_STYLE, USER_STYLE] : BUILTIN_SPECS }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }) as typeof fetch

    await mount()
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('默认样式'))!.click()
    })
    expect(document.body.querySelectorAll('input:not([type="file"])')).toHaveLength(0)

    await act(async () => {
      buttons().find((b) => b.textContent?.includes('复制一份再修改'))!.click()
    })
    // 复制出来的那份被选中，摘要换回输入框，保存按钮出现
    expect(text()).toContain('默认样式 副本')
    expect(text()).not.toContain('内置配置只读')
    expect(
      document.body.querySelectorAll('input:not([type="file"])').length,
    ).toBeGreaterThan(1)
    expect(byText('保存')).toBeTruthy()
  })

  it('用户自建的那份也是可编辑状态（摘要换回输入框）', async () => {
    await mount()
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('投稿用'))!.click()
    })
    expect(text()).not.toContain('内置配置只读')
    expect(
      document.body.querySelectorAll('input:not([type="file"])').length,
    ).toBeGreaterThan(1)
    expect(byText('保存')).toBeTruthy()
  })

  it('用户自建的那条可以改名并保存', async () => {
    await mount()
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('投稿用'))!.click()
    })
    const input = document.body.querySelector<HTMLInputElement>('input[aria-label="名称"]')!
    expect(input.disabled).toBe(false)
    expect(byText('保存')!.disabled).toBe(true) // 没改过就不该是可点的
  })
})

describe('Style 与 Spec 不混改', () => {
  it('切到「规范」分区后字段整组换掉', async () => {
    await mount()
    expect(text()).toContain('线宽')
    expect(text()).not.toContain('最小字号')
    await switchToSpec()
    expect(text()).toContain('最小字号')
    expect(text()).toContain('单栏宽')
    expect(text()).not.toContain('刻度字号')
  })
})

/**
 * 「让本项目用选中的这一套」那颗钮。
 *
 * 文档里还没有显式绑定过任何规范时，检查走的就是内置默认那一份——**那也是「在用」**
 * （全面打磨 D03，判据改成 `resolveDocumentSpec` 算出的实际在用那份）。此时选中它，
 * 身份行给的是绿色的「本项目在用」加一颗说实话的 ghost「固定为本项目规范」：它做的事
 * 是把这一刻的回退固定成显式绑定 + 快照，而不是「现在换成用这套」。
 */
const useForProject = () => byText('固定为本项目规范') ?? byText('本项目用这套规范')

describe('警告与项目绑定', () => {
  it('迁移/导入没能识别的字段如实说出来（没有丢，只是没认出）', async () => {
    await mount()
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('投稿用'))!.click()
    })
    expect(text()).toContain('从未见过')
  })

  it('「跟随更新」默认关着，打开是一次可撤销的文档修改', async () => {
    await mount()
    await switchToSpec()
    // 还没绑定这套规范时根本不出现这个开关（没有可跟随的对象）
    expect(document.body.querySelector('[aria-label="跟随更新"]')).toBeNull()

    await act(async () => {
      useForProject()!.click()
    })
    expect(useDocumentStore.getState().doc.profile!.follow).toBeUndefined()

    // 这一行没有问号，也不再挂短说明（2026-09-11 设计包去掉了全部设置行说明）。
    // **这条判据只能写在这里**：那一行要「项目已绑定这套规范」才渲染，
    // settingsDisclosure 里数整页问号时它根本不在场
    expect(document.body.querySelectorAll('[data-help-tip]')).toHaveLength(0)

    const toggle = document.body.querySelector<HTMLElement>('[aria-label="跟随更新"]')!
    await act(async () => {
      toggle.click()
    })
    expect(useDocumentStore.getState().doc.profile!.follow).toBe(true)
    act(() => {
      useDocumentStore.getState().undo()
    })
    expect(useDocumentStore.getState().doc.profile!.follow).toBeUndefined()
  })

  it('换一套规范不会把「跟随更新」悄悄关掉', async () => {
    await mount()
    await switchToSpec()
    await act(async () => {
      useForProject()!.click()
    })
    await act(async () => {
      document.body.querySelector<HTMLElement>('[aria-label="跟随更新"]')!.click()
    })
    expect(useDocumentStore.getState().doc.profile!.follow).toBe(true)

    // 选另一套规范：跟随的表态是**项目的**，不是那一套规范的
    const other = buttons().find((b) => b.textContent?.includes('自由排版'))!
    await act(async () => other.click())
    await act(async () => {
      useForProject()!.click()
    })
    const bound = useDocumentStore.getState().doc.profile!
    expect(bound.id).toBe('free-form-v1')
    expect(bound.follow).toBe(true)
  })

  it('「本项目用这套规范」写的是带快照的绑定，不是一个 id', async () => {
    await mount()
    await switchToSpec()
    await act(async () => {
      useForProject()!.click()
    })
    const bound = useDocumentStore.getState().doc.profile!
    expect(bound.id).toBe(DEFAULT_PROFILE_ID)
    expect(bound.snapshot).toBeTruthy()
    expect((bound.snapshot as Record<string, unknown>).min_effective_font_size_pt).toBe(8)
  })
})

describe('无障碍', () => {
  it('库是一组带可达名的单选（四份以内分段选择器），当前那份 aria-checked（键盘走得到、读屏说得出）', async () => {
    await mount()
    const group = document.body.querySelector<HTMLElement>('[role="radiogroup"][aria-label="样式库"]')!
    expect(group).not.toBeNull()
    const radios = [...group.querySelectorAll<HTMLElement>('[role="radio"]')]
    expect(radios.length).toBeGreaterThan(1)
    expect(radios.filter((b) => b.getAttribute('aria-checked') === 'true')).toHaveLength(1)
    // 新建 / 复制 / 导入 / 导出收进「更多操作」菜单，库那一行只剩选择器与一颗图标钮
    expect(buttons().some((b) => b.getAttribute('aria-label') === '更多操作')).toBe(true)
    expect(byText('新建')).toBeUndefined()
  })

  it('超过四份时库换成 Select（分段放不下），触发器上是当前那份的名字', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) =>
      new Response(
        JSON.stringify({
          profiles: String(input).includes('/style')
            ? [BUILTIN_STYLE, ...[1, 2, 3, 4].map((n) => ({ ...USER_STYLE, id: `s${n}`, display_name: `方案 ${n}` }))]
            : BUILTIN_SPECS,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    ) as typeof fetch
    await mount()
    expect(document.body.querySelector('[role="radiogroup"][aria-label="样式库"]')).toBeNull()
    const combo = document.body.querySelector<HTMLElement>('[role="combobox"]')!
    expect(combo.getAttribute('aria-label')).toBe('样式库')
    expect(combo.textContent).toContain('默认样式')
  })

  it('每个数值输入都有可达名', async () => {
    await mount()
    // 内置那份是只读摘要，没有输入框——要选一条可编辑的才量得到这件事
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('投稿用'))!.click()
    })
    const inputs = [...document.body.querySelectorAll('input[type="text"], input:not([type])')]
    expect(inputs.length).toBeGreaterThan(0)
    for (const el of inputs) {
      expect(el.getAttribute('aria-label')?.trim()).toBeTruthy()
    }
  })
})

describe('「用于当前画布」：应用 = 绑定，与左栏样式面板同一个函数（ADR 0081）', () => {
  it('当前画布绑上此刻选中的那条样式，关掉设置、打开左栏「样式」面板；不再压一层样式对话框', async () => {
    const { useUiStore } = await import('@/store/uiStore')
    const { useDocumentStore } = await import('@/store/documentStore')
    useUiStore.setState({ settingsOpen: true, settingsSection: 'style', stylesOpen: false, dialogStack: ['settings'] })
    await mount()
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('投稿用'))!.click()
    })
    await act(async () => {
      byText('用于当前画布')!.click()
    })
    const s = useUiStore.getState()
    expect(useDocumentStore.getState().doc.style?.id, '绑的是刚才在设置里选中的那一条').toBe('s1')
    expect(s.leftOpen && s.leftTab).toBe('style')
    expect(s.settingsOpen, '设置关掉：结果在画布上').toBe(false)
    expect(s.stylesOpen, '不再开第二份应用流程').toBe(false)
    expect(s.dialogStack).toEqual([])
    useDocumentStore.getState().undo()
  })
})

describe('Codex #547：草稿没存时「用于当前画布」先不做', () => {
  it('改了名字（草稿脏了）→ 这颗钮置灰并说先保存', async () => {
    await mount()
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('投稿用'))!.click()
    })
    const use = () => byText('用于当前画布')!
    expect(use().disabled).toBe(false)
    const input = document.body.querySelector<HTMLInputElement>('input[aria-label="名称"]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, '投稿用 改')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(use().disabled).toBe(true)
    expect(use().title).toBe('先保存这份样式的改动，再用于画布')
  })
})

describe('旧样式在设置里第一次被编辑：与样式面板同一条升级规则（pt_basis）', () => {
  it('保存旧版存下的样式（没有 pt_basis）时写上 pt_basis:"page"，已有数字原样', async () => {
    await mount()
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('投稿用'))!.click()
    })
    const saves: Record<string, unknown>[] = []
    const real = useProfileStore.getState().save
    useProfileStore.setState({
      save: async (kind, id, data) => {
        saves.push(data)
        return real(kind, id, data)
      },
    })
    const input = document.body.querySelector<HTMLInputElement>('input[aria-label="名称"]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, '投稿用 改')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      byText('保存')!.click()
    })
    expect(saves[0]).toEqual({ element: { line: { linewidth: 1.25 } }, pt_basis: 'page' })
  })
})

describe('规范页把边界与快照摊开（审计 T41）', () => {
  it('数值来自规范自己，行内不再复述检查判据（2026-09-11 用户反馈：去掉「检查 ≥ 6pt，否则记为警告」这类行）', async () => {
    await mount('spec')
    const rows = [...document.body.querySelectorAll('[data-field-group="fonts"] > div')]
    const rowFor = (label: string) => rows.find((r) => r.textContent?.startsWith(label))!
    expect(rowFor('最小字号').textContent).toContain('8 pt')
    expect(rowFor('绝对下限').textContent).toContain('8 pt')
    expect(text()).not.toContain('检查 ')
    expect(text()).not.toContain('否则记为')
  })

  it('本项目实际用来检查的那份规则摊开可查，且来自绑定的解析结果', async () => {
    await mount('spec')
    await act(async () => {
      useForProject()!.click()
    })
    const head = buttons().find((b) => b.textContent?.includes('本项目实际检查的规则'))!
    expect(head.getAttribute('aria-expanded')).toBe('false') // 排障材料，默认折叠
    await act(async () => head.click())
    expect(text()).toContain('快照')
    expect(text()).toContain('80 mm')
    expect(text()).toContain('300 ppi') // 分辨率的单位统一写 ppi
  })
})

describe('样式页有示例图，字段按用途分组（审计 T42）', () => {
  const preview = () => document.body.querySelector('figure[data-style-preview]')

  it('选中一条样式就能预见大致效果，线宽跟着这条样式走', async () => {
    await mount()
    // 内置那份 linewidth 0.5
    expect(preview()).toBeTruthy()
    expect(preview()!.querySelector('polyline')!.getAttribute('stroke-width')).toBe('0.5')

    await act(async () => {
      buttons().find((b) => b.textContent?.includes('投稿用'))!.click()
    })
    expect(preview()!.querySelector('polyline')!.getAttribute('stroke-width')).toBe('1.25')
  })

  it('示例图有读屏读得出的说明——它不是纯装饰', async () => {
    await mount()
    const svg = preview()!.querySelector('svg')!
    expect(svg.getAttribute('role')).toBe('img')
    expect(svg.getAttribute('aria-label')).toContain('pt')
  })

  it('规范页没有示例图（规范不决定图长什么样）', async () => {
    await mount('spec')
    expect(preview()).toBeNull()
  })

  it('字段按文字 / 线条分组（与左栏样式面板同两组），不是一长列数字', async () => {
    await mount()
    for (const g of ['text', 'lines']) {
      expect(document.body.querySelector(`[data-field-group="${g}"]`), g).toBeTruthy()
    }
    // 刻度字号是「文字」里的「刻度」一行（与面板同一张行表），不再单独成组
    expect(document.body.querySelector('[data-field-group="ticks"]')).toBeNull()
    expect(document.body.querySelector('[data-field-group="text"]')!.textContent).toContain('文字')
    expect(document.body.querySelector('[data-field-group="lines"]')!.textContent).toContain('线宽')
    // 规范页是另一组，别把两套字段混在一张表单里
    await mount('spec')
    expect(document.body.querySelector('[data-field-group="ticks"]')).toBeNull()
    expect(document.body.querySelector('[data-field-group="page"]')).toBeTruthy()
  })
})

/* ------------------------------------------------------------------------- */
/*  2026-09-28：样式页补齐左栏面板能改的那几维（字体 / 粗体 / 斜体 / 刻度方向…）  */
/* ------------------------------------------------------------------------- */

describe('样式页与左栏样式面板同一张行表：字体 / 字号 / 粗斜体 / 刻度', () => {
  const rowIds = (group: string) =>
    [...document.body.querySelectorAll(`[data-field-group="${group}"] [data-style-row]`)].map(
      (r) => (r as HTMLElement).dataset.styleRow,
    )
  const input = (label: string) =>
    document.body.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)
  const toggle = (row: string, which: 'weight' | 'style') =>
    document.body.querySelector<HTMLButtonElement>(`[data-style-face="${row}.${which}"] button`)

  async function typeInto(el: HTMLInputElement, value: string) {
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

  /** 选中「投稿用」并截住保存时交给后端的内容 */
  async function editUserStyle() {
    await mount()
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('投稿用'))!.click()
    })
    const saves: Record<string, unknown>[] = []
    const real = useProfileStore.getState().save
    useProfileStore.setState({
      save: async (kind, id, data) => {
        saves.push(data)
        return real(kind, id, data)
      },
    })
    return saves
  }

  it('行与面板一致：文字五行 + 其余文字，线条五行（期望值写死，不从行表读）', async () => {
    await editUserStyle()
    expect(rowIds('text')).toEqual(['title', 'axis_label', 'ticks', 'legend', 'text', 'annotation'])
    expect(rowIds('lines')).toEqual(['dataLine', 'frame', 'tickDirection', 'tickLength', 'tickWidth'])
    // 每行文字都有字体下拉与字号框
    for (const row of ['title', 'axis_label', 'ticks', 'legend', 'text', 'annotation']) {
      expect(document.body.querySelector(`[data-style-cell="${row}.family"] [role="combobox"]`), row).toBeTruthy()
      expect(document.body.querySelector(`[data-style-cell="${row}.size"] input`), row).toBeTruthy()
    }
    // 刻度文字的引擎字段没有 weight / style：不摆点了不生效的开关（与面板同一条）
    expect(toggle('ticks', 'weight')).toBeNull()
    expect(toggle('ticks', 'style')).toBeNull()
    expect(toggle('title', 'weight')).toBeTruthy()
    expect(document.body.querySelector('[data-style-cell="tickDirection"] [role="combobox"]')).toBeTruthy()
  })

  it('没管的格显示「未设置」，不写「多个值」', async () => {
    await editUserStyle()
    expect(input('标题字号')!.value).toBe('')
    expect(input('标题字号')!.placeholder).toBe('未设置')
    expect(input('刻度长度')!.placeholder).toBe('未设置')
    expect(document.body.querySelector('[data-style-cell="title.family"]')!.textContent).toContain('未设置')
  })

  it('加粗 / 倾斜写进样式：图内是 weight / style 的取值，图例的在 legend_text 上，画布标注是 boolean', async () => {
    const saves = await editUserStyle()
    await act(async () => toggle('title', 'weight')!.click())
    await act(async () => toggle('legend', 'style')!.click())
    await act(async () => toggle('annotation', 'weight')!.click())
    expect(toggle('title', 'weight')!.getAttribute('aria-pressed')).toBe('true')
    // 再按一次 = 明确「不加粗」，不是回到「不管」
    await act(async () => toggle('title', 'weight')!.click())
    await typeInto(input('刻度字号')!, '7')
    await typeInto(input('刻度长度')!, '0')
    await act(async () => {
      byText('保存')!.click()
    })
    expect(saves[0]).toEqual({
      element: {
        line: { linewidth: 1.25 },
        title: { weight: 'normal' },
        legend_text: { style: 'italic' },
        ticks: { fontsize: 7, length: 0 },
      },
      annotation: { bold: true },
      pt_basis: 'page',
    })
  })

  it('先改字号再选字体：两样都留着（字体下拉按数据 memo，回调里不能捏着旧草稿）', async () => {
    const saves = await editUserStyle()
    await typeInto(input('标题字号')!, '10')
    // jsdom 没有 scrollIntoView，Radix 打开下拉时要滚到当前项
    if (!Element.prototype.scrollIntoView) {
      Object.defineProperty(Element.prototype, 'scrollIntoView', { value: () => {}, configurable: true })
    }
    const trigger = document.body.querySelector<HTMLElement>('[data-style-cell="title.family"] [role="combobox"]')!
    await act(async () => trigger.click())
    const options = [...document.body.querySelectorAll<HTMLElement>('[role="option"]')]
    // 通用三族在前（没有渲染过的图时就只有它们），第一项是 serif
    expect(options).toHaveLength(3)
    const serif = options[0]
    await act(async () => serif.click())
    await act(async () => {
      byText('保存')!.click()
    })
    expect((saves[0].element as Record<string, unknown>).title).toEqual({ fontsize: 10, fontfamily: 'serif' })
  })

  it('行尾的 × 把整行清回「未设置」（字体、字号、粗斜体一起），空了的角色不留空壳', async () => {
    const saves = await editUserStyle()
    await typeInto(input('标题字号')!, '10')
    await act(async () => toggle('title', 'style')!.click())
    const clear = () =>
      document.body.querySelector<HTMLButtonElement>('[data-style-row="title"] button[aria-label="清除标题"]')
    expect(clear()).toBeTruthy()
    await act(async () => clear()!.click())
    expect(clear()).toBeNull()
    expect(input('标题字号')!.value).toBe('')
    // 草稿回到原样，保存钮随之不可点（名字也没改）
    expect(byText('保存')!.disabled).toBe(true)
    expect(saves).toEqual([])
  })

  it('内置只读那份：每行一句摘要（字体 · 字号 · 粗斜体），没管的写「未设置」', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) =>
      new Response(
        JSON.stringify({
          profiles: String(input).includes('/style')
            ? [
                {
                  ...BUILTIN_STYLE,
                  data: {
                    element: {
                      title: { fontfamily: 'Arial', fontsize: 9, weight: 'bold' },
                      ticks: { direction: 'in' },
                    },
                    annotation: { sizePt: 8, italic: true },
                  },
                },
              ]
            : BUILTIN_SPECS,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    ) as typeof fetch
    await mount()
    const summary = (row: string) =>
      document.body.querySelector(`[data-summary-row][data-style-row="${row}"]`)!.textContent
    expect(summary('title')).toContain('Arial · 9 pt · 粗体')
    expect(summary('annotation')).toContain('8 pt · 斜体')
    expect(summary('axis_label')).toContain('未设置')
    expect(summary('tickDirection')).not.toContain('未设置')
    expect(document.body.querySelectorAll('input:not([type="file"])')).toHaveLength(0)
  })
})
