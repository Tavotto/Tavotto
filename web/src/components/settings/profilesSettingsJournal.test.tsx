/**
 * 规范页「按期刊」条目：项目按期刊检查时，规范库默认看的是本项目实际在用的那份（期刊底 + 期刊覆盖），
 * 不再退回库里第一条（默认规范）——此前下面列着默认规范的 80 / 150 mm，与上面期刊的栏宽同屏打架。
 * 夹具期刊（`fx-*`），无真实数据。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { setLocale } from '@/i18n'
import { loadCatalog } from '@/lib/journals'
import { JOURNAL_UI_DEV_KEY } from '@/lib/journalUi'
import { builtinCatalog } from '@/lib/specBinding'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { selectJournal } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useProfileStore } from '@/store/profileStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import { ProfilesSettings } from './ProfilesSettings'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const BUILTIN_SPECS = builtinCatalog().map((e) => ({
  id: e.id,
  kind: 'spec',
  schema_version: 1,
  revision: 1,
  display_name: e.display_name,
  name_key: e.name_key ?? '',
  version: e.version,
  created_at: 0,
  updated_at: 0,
  built_in: true,
  read_only: true,
  is_default: false,
  derived_from: '',
  warnings: [],
  data: e.data,
}))

let host: HTMLDivElement
let root: Root
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const field = (path: string) => q(`[data-journal-field="${path}"]`)?.textContent ?? ''
const libraryTrigger = () => q('[data-profile-library] [role="combobox"]')

async function mount() {
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ProfilesSettings kind="spec" />
      </TooltipProvider>,
    )
  })
}
async function pickOption(prefix: string) {
  if (!Element.prototype.scrollIntoView) {
    Object.defineProperty(Element.prototype, 'scrollIntoView', { value: () => {}, configurable: true })
  }
  await act(async () => libraryTrigger()!.click())
  const opt = [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find((o) =>
    (o.textContent ?? '').startsWith(prefix),
  )
  expect(opt, `库里没有「${prefix}」`).toBeTruthy()
  await act(async () => opt!.click())
}

beforeEach(async () => {
  await setLocale('zh-CN')
  localStorage.clear()
  localStorage.setItem(JOURNAL_UI_DEV_KEY, '1')
  globalThis.fetch = vi.fn(async () =>
    new Response(JSON.stringify({ profiles: BUILTIN_SPECS }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as typeof fetch
  useProfileStore.setState({ specs: [], loaded: false, error: null, conflict: null })
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_profiles_journal')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useJournalCatalogStore.setState({ catalog: loadCatalog() })
  useUiStore.setState({ confirm: null })
  vi.restoreAllMocks()
})

describe('规范库里的「按期刊」条目', () => {
  it('按期刊检查时默认看它：值是本项目实际在用的，期刊没写的说「未规定」并给出实际按多少', async () => {
    selectJournal('fx-beta')
    await mount()
    expect(libraryTrigger()!.textContent).toContain('Fixture Beta · 期刊 · 本项目在用')
    const identity = q('[data-profile-journal]')!
    expect(identity.textContent).toContain('Fixture Beta')
    expect(identity.textContent).toContain('期刊要求 · 只读')
    // 期刊写了双栏 180 mm：显示期刊的数，不是默认规范的 150
    expect(field('widths_mm.double')).toContain('180 mm')
    // 期刊没写正文字号：说「未规定」，再说实际按多少
    expect(field('default_font_size_pt')).toMatch(/未规定 · 默认 [\d.]+ pt/)
    // 宽度容差、推荐导出分辨率：期刊没写也只写数（后者按 600 推荐）
    expect(field('widths_mm.tolerance_mm')).toMatch(/[\d.]+ mm$/)
    expect(field('widths_mm.tolerance_mm')).not.toContain('未规定')
    expect(field('preferred_formats.export_dpi_default')).toMatch(/600 dpi$/)
    // 「本项目实际检查的规则」不再摆第二份
    expect(document.body.textContent).not.toContain('本项目实际检查的规则')
  })

  it('期刊有自己的栏位表：单 / 双栏两行并成一行「栏宽」，列出全部栏位', async () => {
    selectJournal('fx-cols')
    await mount()
    expect(q('[data-journal-field="page_columns"]')!.textContent).toMatch(/栏宽.*mm/)
    expect(q('[data-journal-field="widths_mm.single"]')).toBeNull()
    expect(q('[data-journal-field="widths_mm.double"]')).toBeNull()
  })

  it('没按期刊检查：库里没有这条，默认规范照旧', async () => {
    await mount()
    expect(q('[data-profile-journal]')).toBeNull()
    expect(libraryTrigger()!.textContent).not.toContain('期刊')
    expect(document.body.textContent).toContain('本项目实际检查的规则')
  })

  it('「复制一份再修改」复制的是期刊折好的全文，去掉期刊底的 listed:false', async () => {
    selectJournal('fx-beta')
    await mount()
    const create = vi.spyOn(useProfileStore.getState(), 'create').mockResolvedValue(null)
    await act(async () => q('[data-profile-journal] [data-profile-duplicate]')!.click())
    expect(create).toHaveBeenCalledTimes(1)
    const [kind, name, data] = create.mock.calls[0]
    expect(kind).toBe('spec')
    expect(name).toContain('Fixture Beta')
    expect(data).not.toHaveProperty('listed')
    expect((data as { widths_mm: { double: number } }).widths_mm.double).toBe(180)
  })
})

describe('从期刊换到库里的另一份', () => {
  it('先问一句；取消则期刊绑定原样，确认才换', async () => {
    selectJournal('fx-beta')
    await mount()
    await pickOption('默认规范')
    const useBtn = [...document.body.querySelectorAll('button')].find((b) => b.textContent?.includes('本项目用这套规范'))!
    await act(async () => useBtn.click())
    const req = useUiStore.getState().confirm
    expect(req).not.toBeNull()
    await act(async () => req!.resolve(false))
    expect(useDocumentStore.getState().doc.profile?.journalRef?.id).toBe('fx-beta')

    await act(async () => useBtn.click())
    await act(async () => useUiStore.getState().confirm!.resolve(true))
    expect(useDocumentStore.getState().doc.profile?.journalRef).toBeUndefined()
  })
})
