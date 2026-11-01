/**
 * 期刊选择器与出处卡（ADR 0155 PR 2）：搜索、分组、空目录一行中性话、出处卡的 `<mark>` 与两个链接、
 * 「该期刊未规定」。夹具期刊（`fx-*`），无真实数据。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { literal, setLocale } from '@/i18n'
import { journalSources, loadCatalog } from '@/lib/journals'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { emptyProject } from '@/types/document'
import { JournalPicker } from './JournalPicker'
import { JournalRequirements } from './JournalRequirements'
import { SourceButton } from './SourceButton'
import { selectJournal } from '@/store/actions'
import { IssueJournalSource } from '../left/ProblemTreeRows'
import type { ValidationIssue } from '@/lib/validation'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

let host: HTMLElement
let root: Root

const mount = async (node: React.ReactNode) => {
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => {
    ;(el as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}
const q = (sel: string) => document.body.querySelector(sel)

beforeEach(async () => {
  await setLocale('zh-CN')
  localStorage.clear()
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_journal_ui')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useJournalCatalogStore.setState({ catalog: loadCatalog() })
})

describe('期刊选择器', () => {
  it('按出版社分组列出；输入名称 / 缩写 / ISSN 过滤；点一项就绑定', async () => {
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    expect(document.body.querySelectorAll('[data-journal-group]').length).toBeGreaterThan(1)
    expect(q('[data-journal-option="fx-alpha"]')).not.toBeNull()

    const input = q('[data-journal-search]') as HTMLInputElement
    const type = async (v: string) =>
      act(async () => {
        const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
        set.call(input, v)
        input.dispatchEvent(new Event('input', { bubbles: true }))
      })

    await type('fxa')
    expect([...document.body.querySelectorAll('[data-journal-option]')].map((e) => e.getAttribute('data-journal-option'))).toEqual(['fx-alpha'])
    await type('0000-0000')
    expect(document.body.querySelectorAll('[data-journal-option]')).toHaveLength(1)
    await type('zzzz')
    expect(document.body.querySelectorAll('[data-journal-option]')).toHaveLength(0)
    expect(q('[data-journal-empty]')).not.toBeNull()

    await type('')
    await click(q('[data-journal-option="fx-beta"]'))
    expect(useDocumentStore.getState().doc.profile?.journalRef?.id).toBe('fx-beta')
  })

  it('目录为空：只有一行中性的话，没有搜索框，也没有解释', async () => {
    useJournalCatalogStore.setState({ catalog: loadCatalog() })
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    const empty = document.body.querySelectorAll('[data-journal-empty]')
    expect(empty).toHaveLength(1)
    expect(empty[0].textContent).toBe('没有可选的期刊')
    expect(q('[data-journal-search]')).toBeNull()
  })
})

describe('出处卡', () => {
  const entries = (id: string, path: string) =>
    [[path, journalSources(id, loadCatalog(vectors.catalog))[path]]] as [string, ReturnType<typeof journalSources>[string]][]

  it('一句话 + 命中的数 <mark> + 域名与采集日期 + 打开原网页（带锚点 / 页码）+ 查看存档', async () => {
    await mount(<SourceButton journal="Fixture Press" entries={entries('fx-alpha', 'min_raster_dpi')} name="位图分辨率" />)
    await click(q('[data-journal-source]'))
    expect(q('[data-journal-sentence]')?.textContent).toBe('Fixture Press：位图分辨率 ≥ 600 ppi')
    const mark = q('[data-journal-quote] mark')
    expect(mark?.textContent).toBe('600')
    expect(q('[data-journal-meta]')?.textContent).toBe('example.invalid · 2000-01-01')
    const open = q('[data-journal-open]') as HTMLAnchorElement
    expect(open.getAttribute('href')).toBe('https://example.invalid/fx-press/guide.pdf#page=12')
    expect(open.getAttribute('target')).toBe('_blank')
    expect(open.getAttribute('rel')).toContain('noreferrer')
  })

  it('建议性字号：Elsevier 式一句话 + 两段原文', async () => {
    const src = journalSources('fx-adv', loadCatalog(vectors.catalog))
    await mount(
      <SourceButton
        journal="Elsevier"
        name="字号"
        entries={[
          ['min_effective_font_size_pt', src.min_effective_font_size_pt],
          ['min_script_font_size_pt', src.min_script_font_size_pt],
        ]}
      />,
    )
    await click(q('[data-journal-source]'))
    expect(q('[data-journal-sentence]')?.textContent).toBe('Elsevier 说明 7 pt 为正文最小字号，6 pt 为上下标最小字号')
    expect(document.body.querySelectorAll('[data-journal-entry]')).toHaveLength(2)
  })

  it('没有出处的项不渲染按钮（期刊没规定就没有卡可开）', async () => {
    await mount(<SourceButton journal="J" entries={[]} name="x" />)
    expect(q('[data-journal-source]')).toBeNull()
  })
})

describe('要求清单', () => {
  const row = (p: string) => q(`[data-journal-row="${p}"]`)!

  it('期刊没写的要紧项明说「该期刊未规定」且没有出处钮', async () => {
    selectJournal('fx-adv-own')
    await mount(<JournalRequirements />)
    expect(row('min_raster_dpi').textContent).toContain('该期刊未规定')
    expect(row('min_raster_dpi').querySelector('[data-journal-source]')).toBeNull()
    expect(row('page_columns').textContent).toContain('该期刊未规定')
  })

  it('typical 显示原话，不显示成「5 pt」；有出处钮', async () => {
    selectJournal('fx-zeta')
    await mount(<JournalRequirements />)
    expect(row('min_effective_font_size_pt').textContent).toContain('FIXTURE about 7 pt, not smaller than 5')
    expect(row('min_effective_font_size_pt').querySelector('[data-journal-source]')).not.toBeNull()
  })
})

describe('预检结果行的出处', () => {
  const issue = (ruleCode: string) =>
    ({ ruleCode, message: literal('x'), objectRef: { canvasId: 'c' } }) as unknown as ValidationIssue

  it('这条检查来自所选期刊的规则时行尾有出处钮，点开是同一张卡', async () => {
    selectJournal('fx-beta')
    await mount(<IssueJournalSource issue={issue('page-width')} />)
    await click(q('[data-journal-source]'))
    expect(q('[data-journal-sentence]')?.textContent).toContain('双栏 180 mm')
  })

  it('没按期刊选过，或这条检查在该期刊里没有出处：什么都不画', async () => {
    await mount(<IssueJournalSource issue={issue('page-width')} />)
    expect(q('[data-journal-source]')).toBeNull()
    selectJournal('fx-delta')
    await mount(<IssueJournalSource issue={issue('page-width')} />)
    expect(q('[data-journal-source]')).toBeNull()
  })
})
