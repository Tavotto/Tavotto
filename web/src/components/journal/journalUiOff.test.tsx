/**
 * 期刊入口默认关闭（`lib/journalUi`，用户 2026-11-01 决定）：关着时界面与期刊 PR 之前一样，下面这些入口一个都不渲染。
 * 开关打开（`JOURNAL_UI_DEV_KEY`）时它们才出现——那一半由 `journal.test.tsx` 与 e2e 覆盖。
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
  localStorage.clear() // 不设开发开关 = 生产状态
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

describe('期刊入口默认关闭', () => {
  it('常量是 false；没有开发开关时入口就是关的，设了开关才开', () => {
    expect(JOURNAL_CATALOG_UI).toBe(false)
    expect(journalCatalogUi()).toBe(false)
    localStorage.setItem(JOURNAL_UI_DEV_KEY, '1')
    expect(journalCatalogUi()).toBe(true)
    localStorage.setItem(JOURNAL_UI_DEV_KEY, 'true') // 只认 '1'
    expect(journalCatalogUi()).toBe(false)
  })

  it('设置 › 期刊规范：没有「期刊」行与「期刊要求」，原来的「正在使用」那一行照旧在', async () => {
    await act(async () => {
      root.render(
        <TooltipProvider>
          <ProfilesSettings kind="spec" />
        </TooltipProvider>,
      )
    })
    expect(document.body.querySelector('[data-journal-picker]')).toBeNull()
    expect(document.body.querySelector('[data-journal-requirements]')).toBeNull()
    expect(document.body.querySelector('[data-settings-anchor="spec.journal"]')).toBeNull()
    expect(document.body.textContent).not.toContain('期刊要求')
    // 与此 PR 之前一样：「当前项目使用」组的第一行（规范名 + 来源）还在
    expect(document.body.querySelector('[data-spec-binding]')?.textContent).toContain('选择时的快照')
    expect(document.body.querySelector('[data-spec-key-rules]')).not.toBeNull()
  })

  it('问题面板行尾没有「出处」图标（即使文档带着期刊绑定）', async () => {
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
    expect(document.body.querySelector('[data-journal-source]')).toBeNull()
    // 对照：开关一开就出现
    localStorage.setItem(JOURNAL_UI_DEV_KEY, '1')
    await act(async () => {
      root.render(
        <TooltipProvider>
          <IssueJournalSource issue={{ ...issue }} />
        </TooltipProvider>,
      )
    })
    expect(document.body.querySelector('[data-journal-source]')).not.toBeNull()
  })

  it('设置搜索找不到 spec.journal', () => {
    const order = ['spec'] as never
    const hit = (q: string) =>
      searchSettings(q, order, () => '期刊规范').flatMap((r) => r.entries.map((e) => e.id))
    expect(hit('期刊')).not.toContain('spec.journal')
    expect(SETTINGS_REGISTRY.some((e) => e.id === 'spec.journal')).toBe(true) // 条目在，只是关着不参与搜索
    localStorage.setItem(JOURNAL_UI_DEV_KEY, '1')
    expect(hit('期刊')).toContain('spec.journal')
  })
})
