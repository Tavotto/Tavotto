/**
 * 期刊入口随种子数据一起打开（`lib/journalUi` 的 `JOURNAL_CATALOG_UI = true`，ADR 0155 §8）：不设任何开发开关，
 * 设置 › 期刊规范里的「期刊」行、问题面板行尾的「出处」、设置搜索里的 `spec.journal` 都出现；
 * 带期刊绑定的旧文档照旧按绑定预检。入口后面的功能由 `journal.test.tsx` 与 e2e 覆盖。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal } from '@/i18n'
import { JOURNAL_CATALOG_UI, JOURNAL_UI_DEV_KEY, journalCatalogUi } from '@/lib/journalUi'
import { loadCatalog } from '@/lib/journals'
import { pickJournal } from '@/lib/journalPick'
import { builtinCatalog } from '@/lib/specBinding'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useProfileStore } from '@/store/profileStore'
import { emptyProject } from '@/types/document'
import { searchSettings, SETTINGS_REGISTRY } from '../settings/settingsRegistry'
import { ProfilesSettings } from '../settings/ProfilesSettings'
import { IssueJournalSource } from '../left/ProblemTreeRows'
import type { ValidationIssue } from '@/lib/validation'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false

let host: HTMLElement
let root: Root

beforeEach(async () => {
  localStorage.clear() // 不设开发开关 = 生产状态（入口由常量打开）
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  useProfileStore.setState({
    specs: builtinCatalog().map((e) => ({
      id: e.id, kind: 'spec', schema_version: 1, revision: 1, display_name: e.display_name, name_key: e.name_key ?? '',
      version: e.version, created_at: 0, updated_at: 0, built_in: true, read_only: true, is_default: false,
      derived_from: '', warnings: [], data: e.data,
    })) as never,
    loaded: true,
  })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_journal_off')
  // 旧文档里已经带着期刊绑定（入口隐藏后用户正常到不了这个状态，但读到时照旧按绑定）
  useDocumentStore.getState().commit(literal('旧绑定'), (d) => {
    d.profile = pickJournal('fx-beta', loadCatalog(vectors.catalog), { pageW: 150 }).binding
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useJournalCatalogStore.setState({ catalog: loadCatalog() })
})

describe('期刊入口已打开', () => {
  it('常量是 true；没有开发开关时入口也是开的，开发键不再起作用', () => {
    expect(JOURNAL_CATALOG_UI).toBe(true)
    expect(journalCatalogUi()).toBe(true)
    localStorage.setItem(JOURNAL_UI_DEV_KEY, 'true')
    expect(journalCatalogUi()).toBe(true)
  })

  it('设置 › 期刊规范：有「期刊」行；绑了期刊的文档有「期刊要求」，「正在使用」那一行让位给它', async () => {
    await act(async () => {
      root.render(
        <TooltipProvider>
          <ProfilesSettings kind="spec" />
        </TooltipProvider>,
      )
    })
    expect(document.body.querySelector('[data-journal-picker]')).not.toBeNull()
    expect(document.body.querySelector('[data-settings-anchor="spec.journal"]')).not.toBeNull()
    expect(document.body.querySelector('[data-journal-disclosure]')).not.toBeNull()
    expect(document.body.querySelector('[data-spec-key-rules]')).not.toBeNull()
  })

  it('期刊只给了建议字号（没有硬性最小字号）：规范页大数字写「未规定」，建议值在名字后小字，不露出底层的 0', async () => {
    useDocumentStore.getState().commit(literal('换期刊'), (d) => {
      d.profile = pickJournal('fx-zeta', loadCatalog(vectors.catalog), { pageW: 150 }).binding
    })
    await act(async () => {
      root.render(
        <TooltipProvider>
          <ProfilesSettings kind="spec" />
        </TooltipProvider>,
      )
    })
    const rules = document.body.querySelector('[data-spec-key-rules]')!
    const first = rules.querySelector('div')!
    expect(first.querySelector('dd')?.textContent).toBe('未规定')
    expect(first.querySelector('[data-key-rule-advice]')?.textContent).toMatch(/建议 [\d.]+ pt/)
    // 期刊没写栏宽：显示「未规定」，不是 null
    expect(rules.textContent).not.toContain('null')
  })

  it('期刊有自己的栏位表：栏宽一格列出它自己的栏位；分辨率由底兜底时写「未规定」+ 小字「默认 300 dpi」', async () => {
    useDocumentStore.getState().commit(literal('换期刊'), (d) => {
      d.profile = pickJournal('fx-cols-inherit', loadCatalog(vectors.catalog), { pageW: 150 }).binding
    })
    await act(async () => {
      root.render(
        <TooltipProvider>
          <ProfilesSettings kind="spec" />
        </TooltipProvider>,
      )
    })
    const rules = document.body.querySelector('[data-spec-key-rules]')!
    const col = rules.querySelector('[data-key-rule-columns]')!
    expect(col.querySelector('dd')?.textContent).toBe('57 / 121 / 184 mm')
    expect(col.querySelector('dt')?.textContent).toBe('栏宽')
    expect(rules.textContent).not.toContain('单栏宽')
    expect(rules.querySelectorAll('dd')).toHaveLength(3)
    const dpi = [...rules.querySelectorAll('div')].find((d) => d.textContent?.includes('最低分辨率'))!
    expect(dpi.querySelector('dd')?.textContent).toBe('未规定')
    expect(dpi.querySelector('[data-key-rule-default]')?.textContent).toMatch(/默认 300 dpi/)
  })

  it('期刊只有单栏 / 双栏：四个数照旧（栏宽两格）', async () => {
    await act(async () => {
      root.render(
        <TooltipProvider>
          <ProfilesSettings kind="spec" />
        </TooltipProvider>,
      )
    })
    const rules = document.body.querySelector('[data-spec-key-rules]')!
    expect(rules.querySelector('[data-key-rule-columns]')).toBeNull()
    expect(rules.querySelectorAll('dd')).toHaveLength(4)
  })

  it('问题面板行尾有「出处」图标（文档带着期刊绑定）', async () => {
    const issue = {
      ruleCode: 'raster-dpi',
      message: literal('x'),
      objectRef: { canvasId: useDocumentStore.getState().activeCanvasId },
    } as unknown as ValidationIssue
    await act(async () => {
      root.render(
        <TooltipProvider>
          <IssueJournalSource issue={issue} />
        </TooltipProvider>,
      )
    })
    expect(document.body.querySelector('[data-journal-source]')).not.toBeNull()
  })

  it('设置搜索找得到 spec.journal', () => {
    const order = ['spec'] as never
    const hit = (q: string) =>
      searchSettings(q, order, () => '期刊规范').flatMap((r) => r.entries.map((e) => e.id))
    expect(SETTINGS_REGISTRY.some((e) => e.id === 'spec.journal')).toBe(true)
    expect(hit('期刊')).toContain('spec.journal')
  })
})
