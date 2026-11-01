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
import { clearJournal, selectJournal } from '@/store/actions'
import { pickJournal } from '@/lib/journalPick'
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

  it('列表最上面是「不按期刊」：选了就退回之前的规范，是一次提交', async () => {
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    selectJournal('fx-beta')
    const list = q('[data-journal-list]')!
    expect(list.firstElementChild?.hasAttribute('data-journal-none')).toBe(true)
    expect(list.firstElementChild?.textContent).toBe('不按期刊')
    await click(q('[data-journal-none]'))
    expect(useDocumentStore.getState().doc.profile?.journalRef).toBeUndefined()
  })

  it('项目已按某本期刊检查、但本机目录是空的：仍给「不按期刊」，且不依赖目录', async () => {
    selectJournal('fx-beta')
    useJournalCatalogStore.setState({ catalog: loadCatalog() }) // 目录不可用
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    expect(q('[data-journal-none]')).not.toBeNull()
    expect(q('[data-journal-search]')).toBeNull()
    expect(document.body.querySelectorAll('[data-journal-option]')).toHaveLength(0)
    await click(q('[data-journal-none]'))
    expect(useDocumentStore.getState().doc.profile?.journalRef).toBeUndefined()
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

  it('没有快照：摘录排在白纸上，命中的数标 mark；底栏是结论 + 「期刊 · 域名 · 日期 · PDF」+ 打开原网页 ↗', async () => {
    await mount(<SourceButton journal="Fixture Press" entries={entries('fx-alpha', 'min_raster_dpi')} name="位图分辨率" />)
    await click(q('[data-journal-source]'))
    expect(q('[data-journal-sentence]')?.textContent).toBe('位图分辨率 ≥ 600 dpi')
    expect(q('[data-journal-quote]')?.className).toContain('bg-paper')
    const mark = q('[data-journal-quote] mark')
    expect(mark?.textContent).toBe('600')
    expect(mark?.className).toContain('bg-paper-highlight')
    expect(q('[data-journal-meta]')?.textContent).toBe('Fixture Press · example.invalid · 2000-01-01 · PDF')
    const open = q('[data-journal-open]') as HTMLAnchorElement
    expect(open.getAttribute('href')).toBe('https://example.invalid/fx-press/guide.pdf#page=12')
    expect(open.getAttribute('target')).toBe('_blank')
    expect(open.getAttribute('rel')).toContain('noreferrer')
    expect(open.querySelector('svg')).not.toBeNull()
    // 不是实心按钮；没有存档就没有「存档」链接
    expect(open.getAttribute('data-variant')).toBeNull()
    expect(q('[data-journal-archive]')).toBeNull()
  })

  it('有存档：底栏右侧多一个灰色文字链接「存档」', async () => {
    await mount(<SourceButton journal="Fixture Press" entries={entries('fx-beta', 'min_effective_font_size_pt')} name="最小字号" />)
    await click(q('[data-journal-source]'))
    const archive = q('[data-journal-archive]') as HTMLAnchorElement
    expect(archive.textContent).toBe('存档')
    expect(archive.getAttribute('href')).toMatch(/^https:\/\//)
  })

  it('有快照：满宽快照图（按资产根解析、最高 210px、底边渐隐），没有摘录框与换算行', async () => {
    await mount(<SourceButton journal="Nature Energy" entries={entries('fx-beta', 'min_raster_dpi')} name="位图分辨率" />)
    await click(q('[data-journal-source]'))
    const box = q('[data-journal-snapshot]')
    expect(box).not.toBeNull()
    expect(box!.className).toContain('max-h-[210px]')
    expect(box!.className).toContain('bg-paper')
    const img = box!.querySelector('img') as HTMLImageElement
    expect(img.getAttribute('src')).toMatch(/^\/journal-catalog\/snapshots\/[0-9a-f]{64}\.webp$/)
    expect(img.getAttribute('width')).toBe('1080')
    expect(box!.querySelector('[aria-hidden]')).not.toBeNull() // 渐隐层
    expect(q('[data-journal-quote]')).toBeNull()
    expect(q('[data-journal-sentence]')?.textContent).toBe('位图分辨率 ≥ 300 dpi')
    expect(q('[data-journal-meta]')?.textContent).toBe('Nature Energy · www.nature.com · 2026-10-31')
    expect(q('[data-journal-open]')).not.toBeNull()
  })

  it('资产根跟着目录走（远程目录给自己的根）', async () => {
    useJournalCatalogStore.setState({ assetBase: 'https://assets.example.invalid/rev1/' })
    await mount(<SourceButton journal="Nature Energy" entries={entries('fx-beta', 'min_raster_dpi')} name="位图分辨率" />)
    await click(q('[data-journal-source]'))
    expect(q('[data-journal-snapshot] img')!.getAttribute('src')).toMatch(
      /^https:\/\/assets\.example\.invalid\/rev1\/snapshots\/[0-9a-f]{64}\.webp$/,
    )
    useJournalCatalogStore.setState({ assetBase: '/journal-catalog/' })
  })

  it('图加载失败：退回白纸摘录（命中的数仍标 mark），不出错误态', async () => {
    await mount(<SourceButton journal="Nature Energy" entries={entries('fx-beta', 'min_raster_dpi')} name="位图分辨率" />)
    await click(q('[data-journal-source]'))
    await act(async () => {
      q('[data-journal-snapshot] img')!.dispatchEvent(new Event('error'))
    })
    expect(q('[data-journal-snapshot]')).toBeNull()
    expect(q('[data-journal-quote] mark')?.textContent).toBe('300 dpi')
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
    expect(document.body.querySelectorAll('[data-journal-sentence]')).toHaveLength(1)
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
    ({ ruleCode, message: literal('x'), objectRef: { canvasId: useDocumentStore.getState().activeCanvasId } }) as unknown as ValidationIssue

  it('这条检查来自所选期刊的规则时行尾有出处钮，点开是同一张卡', async () => {
    selectJournal('fx-beta')
    await mount(<IssueJournalSource issue={issue('page-width')} />)
    await click(q('[data-journal-source]'))
    expect(q('[data-journal-sentence]')?.textContent).toContain('双栏 180 mm')
  })

  it('问题面板列着几张画布的问题：各读自己所在画布的期刊绑定；活动画布没绑期刊也不影响别的画布', async () => {
    const issueOn = (canvasId: string) =>
      ({ ruleCode: 'raster-dpi', message: literal('x'), objectRef: { canvasId } }) as unknown as ValidationIssue
    const cat = loadCatalog(vectors.catalog)
    const st = useDocumentStore.getState()
    const a = st.activeCanvasId
    selectJournal('fx-beta') // 活动画布 A：实时文档里按 fx-beta（300）
    const profileOf = (id: string) => pickJournal(id, cat, { pageW: 100 }).binding
    useDocumentStore.setState({
      canvases: [
        ...st.canvases,
        { ...st.canvases[0], id: 'cB', name: 'B', profile: profileOf('fx-zeta') } as never,
      ],
    })
    const dpiOf = async (canvasId: string) => {
      await mount(<IssueJournalSource issue={issueOn(canvasId)} />)
      const btn = q('[data-journal-source]')
      if (!btn) return null
      await click(btn)
      const text = q('[data-journal-sentence]')?.textContent ?? null
      await act(async () => {
        document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      })
      return text
    }
    expect(await dpiOf(a)).toBe('位图分辨率 ≥ 300 dpi')
    expect(await dpiOf('cB')).toBe('位图分辨率 450 dpi')
    // 活动画布 A 不按期刊：B 的出处钮仍在，A 自己没有
    act(() => clearJournal())
    expect(await dpiOf(a)).toBeNull()
    expect(await dpiOf('cB')).toBe('位图分辨率 450 dpi')
  })

  it('没按期刊选过，或这条检查在该期刊里没有出处：什么都不画', async () => {
    await mount(<IssueJournalSource issue={issue('page-width')} />)
    expect(q('[data-journal-source]')).toBeNull()
    selectJournal('fx-delta')
    await mount(<IssueJournalSource issue={issue('page-width')} />)
    expect(q('[data-journal-source]')).toBeNull()
  })
})
