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
    root.render(<ProfilesSettings kind={kind} />)
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
    const row = buttons().find((b) => b.textContent?.includes('默认样式'))!
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
    expect(text()).toContain('内置配置只读')
    // 摘要模式下右栏一个输入框都没有——整页禁用的输入看起来像"我的表单坏了"
    expect(document.body.querySelectorAll('input:not([type="file"])')).toHaveLength(0)
    // 规则本身仍然读得到（线宽 0.5 来自 BUILTIN_STYLE.data）
    expect(text()).toContain('0.5 pt')
    // 改的动作从「复制一份」开始，而不是一排点了没反应的按钮
    expect(byText('保存')).toBeUndefined()
    expect(buttons().find((b) => b.textContent?.includes('删除'))).toBeUndefined()
    expect(buttons().some((b) => b.textContent?.includes('复制一份再修改'))).toBe(true)
  })

  it('复制出来的那份进入可编辑状态（摘要换回输入框）', async () => {
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
      byText('本项目用这套规范')!.click()
    })
    expect(useDocumentStore.getState().doc.profile!.follow).toBeUndefined()

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
      byText('本项目用这套规范')!.click()
    })
    await act(async () => {
      document.body.querySelector<HTMLElement>('[aria-label="跟随更新"]')!.click()
    })
    expect(useDocumentStore.getState().doc.profile!.follow).toBe(true)

    // 选另一套规范：跟随的表态是**项目的**，不是那一套规范的
    const other = buttons().find((b) => b.textContent?.includes('自由排版'))!
    await act(async () => other.click())
    await act(async () => {
      byText('本项目用这套规范')!.click()
    })
    const bound = useDocumentStore.getState().doc.profile!
    expect(bound.id).toBe('free-form-v1')
    expect(bound.follow).toBe(true)
  })

  it('「本项目用这套规范」写的是带快照的绑定，不是一个 id', async () => {
    await mount()
    await switchToSpec()
    await act(async () => {
      byText('本项目用这套规范')!.click()
    })
    const bound = useDocumentStore.getState().doc.profile!
    expect(bound.id).toBe(DEFAULT_PROFILE_ID)
    expect(bound.snapshot).toBeTruthy()
    expect((bound.snapshot as Record<string, unknown>).min_effective_font_size_pt).toBe(8)
  })
})

describe('无障碍', () => {
  it('清单是按钮列表，选中项带 aria-current（键盘走得到、读屏说得出）', async () => {
    await mount()
    const rows = buttons().filter((b) => b.getAttribute('title'))
    expect(rows.length).toBeGreaterThan(1)
    expect(rows.filter((b) => b.getAttribute('aria-current') === 'true')).toHaveLength(1)
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

describe('「应用到当前图…」交给样式对话框（审计 T35）', () => {
  it('带着此刻选中的那条样式打开，设置本身不关（样式对话框压在它上面）', async () => {
    const { useUiStore } = await import('@/store/uiStore')
    useUiStore.setState({ settingsOpen: true, settingsSection: 'style', stylesOpen: false, dialogStack: ['settings'] })
    await mount()
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('投稿用'))!.click()
    })
    await act(async () => {
      byText('应用到当前图…')!.click()
    })
    const s = useUiStore.getState()
    expect(s.stylesOpen).toBe(true)
    expect(s.stylesPresetId, '预选的是刚才在设置里选中的那一条').toBe('s1')
    expect(s.settingsOpen, '设置不关：样式对话框关掉就回到这里').toBe(true)
    expect(s.dialogStack).toEqual(['settings', 'styles'])
    useUiStore.setState({ settingsOpen: false, stylesOpen: false, stylesPresetId: null, dialogStack: [] })
  })
})

describe('规范页把边界与快照摊开（审计 T41）', () => {
  it('两条字号下限的要求不是同一句话：一条含等号、一条不含', async () => {
    await mount('spec')
    const rows = [...document.body.querySelectorAll('[data-field-group="fonts"] > div')]
    const rowFor = (label: string) => rows.find((r) => r.textContent?.startsWith(label))!
    // 值来自规范自己（两个都是 8），措辞来自措辞层那张规则表
    expect(rowFor('最小字号').textContent).toContain('8 pt')
    expect(rowFor('最小字号').textContent).toContain('检查 ≥ 8pt')
    expect(rowFor('绝对下限').textContent).toContain('检查 大于 8pt')
    // 「大于」那条不许写成 ≥ ——两个数一样、判据不一样，正是审计指出的那处误导
    expect(rowFor('绝对下限').textContent).not.toContain('≥')
    // 等级来自 profile 自己的 severity 表
    expect(rowFor('绝对下限').textContent).toContain('阻断')
  })

  it('规范页才有这行；样式页没有可判的规则，不硬造一句', async () => {
    await mount('style')
    expect(text()).not.toContain('检查 ')
  })

  it('本项目实际用来检查的那份规则摊开可查，且来自绑定的解析结果', async () => {
    await mount('spec')
    await act(async () => {
      byText('本项目用这套规范')!.click()
    })
    const head = buttons().find((b) => b.textContent?.includes('本项目实际用来检查的规则'))!
    expect(head.getAttribute('aria-expanded')).toBe('false') // 排障材料，默认折叠
    await act(async () => head.click())
    expect(text()).toContain('快照')
    expect(text()).toContain('80 mm')
    expect(text()).toContain('300 dpi')
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

  it('字段按文字 / 刻度 / 线条分组，不是一长列数字', async () => {
    await mount()
    for (const g of ['text', 'ticks', 'lines']) {
      expect(document.body.querySelector(`[data-field-group="${g}"]`), g).toBeTruthy()
    }
    expect(document.body.querySelector('[data-field-group="text"]')!.textContent).toContain('文字')
    expect(document.body.querySelector('[data-field-group="lines"]')!.textContent).toContain('线宽')
    // 规范页是另一组，别把两套字段混在一张表单里
    await mount('spec')
    expect(document.body.querySelector('[data-field-group="ticks"]')).toBeNull()
    expect(document.body.querySelector('[data-field-group="page"]')).toBeTruthy()
  })
})
