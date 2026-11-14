/**
 * 期刊选择器与出处卡（ADR 0155 PR 2）：搜索、分组、空目录一行中性话、出处卡的 `<mark>` 与两个链接、
 * 「当前快照未记录」。夹具期刊（`fx-*`），无真实数据。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { literal, msg, setLocale } from '@/i18n'
import { journalProfile, journalSources, loadCatalog } from '@/lib/journals'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { BUNDLED_ASSET_BASE, useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import { JournalPicker } from './JournalPicker'
import { JournalRequirements } from './JournalRequirements'
import { SourceButton } from './SourceButton'
import { clearJournal, selectJournal } from '@/store/actions'
import { fieldLabel, pickJournal } from '@/lib/journalPick'
import { JOURNAL_UI_DEV_KEY } from '@/lib/journalUi'
import { IssueJournalSource, IssueRow } from '../left/ProblemTreeRows'
import { exportContextIssues } from '@/lib/validation'
import type { ValidationIssue } from '@/lib/validation'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

let host: HTMLElement
let root: Root

/** 空目录（种子已经不是空库，「没有期刊」的界面用它造） */
const EMPTY_CATALOG = { schema: 1, catalog_rev: 1, publishers: {}, journals: {} }

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
  localStorage.setItem(JOURNAL_UI_DEV_KEY, '1') // 期刊入口默认关；这里测的是入口后面的功能
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
    useJournalCatalogStore.setState({ catalog: loadCatalog(EMPTY_CATALOG) }) // 目录不可用
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    expect(q('[data-journal-none]')).not.toBeNull()
    expect(q('[data-journal-search]')).toBeNull()
    expect(document.body.querySelectorAll('[data-journal-option]')).toHaveLength(0)
    await click(q('[data-journal-none]'))
    expect(useDocumentStore.getState().doc.profile?.journalRef).toBeUndefined()
  })

  it('目录为空：只有一行中性的话，没有搜索框，也没有解释', async () => {
    useJournalCatalogStore.setState({ catalog: loadCatalog(EMPTY_CATALOG) })
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

  it('没有快照：摘录排在白纸上，命中的数标 mark；底栏是结论 + 「域名 · 日期 · PDF」+ 打开原网页 ↗', async () => {
    await mount(<SourceButton journal="Fixture Press" entries={entries('fx-alpha', 'min_raster_dpi')} name="位图分辨率" />)
    await click(q('[data-journal-source]'))
    expect(q('[data-journal-sentence]')?.textContent).toBe('位图分辨率 ≥ 600 dpi')
    expect(q('[data-journal-quote]')?.className).toContain('bg-paper')
    const mark = q('[data-journal-quote] mark')
    expect(mark?.textContent).toBe('600')
    expect(mark?.className).toContain('bg-paper-highlight')
    expect(q('[data-journal-meta]')?.textContent).toBe('example.invalid · 2000-01-01 · PDF')
    const open = q('[data-journal-open]') as HTMLAnchorElement
    expect(open.getAttribute('href')).toBe('https://example.invalid/fx-press/guide.pdf#page=12')
    expect(open.getAttribute('target')).toBe('_blank')
    expect(open.getAttribute('rel')).toContain('noreferrer')
    expect(open.querySelector('svg')).not.toBeNull()
    // 不是实心按钮；没有存档就没有「存档」链接
    expect(open.getAttribute('data-variant')).toBeNull()
    expect(q('[data-journal-archive]')).toBeNull()
  })

  it('通用来源：只显示实际来源域名，不把选刊名当出处', async () => {
    const generic = entries('fx-alpha', 'min_raster_dpi')
    expect(generic[0][1].coverage).toBe('publisher_general')
    await mount(<SourceButton journal="Selected Journal" entries={generic} name="分辨率" />)
    await click(q('[data-journal-source]'))
    expect(q('[data-journal-meta]')?.textContent).toBe('example.invalid · 2000-01-01 · PDF')
    expect(q('[data-journal-meta]')?.textContent).not.toContain('Selected Journal')
    expect(q('[data-journal-coverage]')?.textContent).toBe('此来源的范围标记：出版社通用。标记可能是默认值或已过时，是否适用于本刊未核实。')
    expect((q('[data-journal-open]') as HTMLAnchorElement).href).toBe(generic[0][1].link)
  })

  it('旧项目的来源范围未知：不猜归属，显示读取的链接且不改保存快照', async () => {
    selectJournal('fx-beta')
    const profile = structuredClone(useDocumentStore.getState().doc.profile!)
    const source = profile.journalSources!.min_raster_dpi
    delete (source as Partial<typeof source>).coverage
    delete (source as Partial<typeof source>).source_scope
    delete (source as Partial<typeof source>).rule_scope
    act(() => useDocumentStore.setState((st) => ({ doc: { ...st.doc, profile } })))
    const before = structuredClone(useDocumentStore.getState().doc.profile)
    useJournalCatalogStore.setState({ catalog: loadCatalog(EMPTY_CATALOG) })
    await mount(<JournalRequirements />)
    await click(q('[data-journal-row="min_raster_dpi"] [data-journal-source]'))
    expect(q('[data-journal-meta]')?.textContent).toBe('www.nature.com · 2026-10-31')
    expect(q('[data-journal-coverage]')?.textContent).toBe('来源范围和适用性未核实。')
    expect((q('[data-journal-open]') as HTMLAnchorElement).href).toBe(source.link)
    expect(useDocumentStore.getState().doc.profile).toEqual(before)
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
    expect(img.getAttribute('src')).toMatch(/^\/assets\/journal-catalog\/snapshots\/[0-9a-f]{64}\.webp$/)
    expect(img.getAttribute('width')).toBe('1080')
    expect(box!.querySelector('[aria-hidden]')).not.toBeNull() // 渐隐层
    expect(q('[data-journal-quote]')).toBeNull()
    expect(q('[data-journal-sentence]')?.textContent).toBe('位图分辨率 ≥ 300 dpi')
    expect(q('[data-journal-meta]')?.textContent).toBe('www.nature.com · 2026-10-31')
    expect(q('[data-journal-open]')).not.toBeNull()
  })

  it('资产根跟着目录走（远程目录给自己的根）', async () => {
    useJournalCatalogStore.setState({ assetBase: 'https://assets.example.invalid/rev1/' })
    await mount(<SourceButton journal="Nature Energy" entries={entries('fx-beta', 'min_raster_dpi')} name="位图分辨率" />)
    await click(q('[data-journal-source]'))
    expect(q('[data-journal-snapshot] img')!.getAttribute('src')).toMatch(
      /^https:\/\/assets\.example\.invalid\/rev1\/snapshots\/[0-9a-f]{64}\.webp$/,
    )
    useJournalCatalogStore.setState({ assetBase: '/assets/journal-catalog/' })
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
    expect(q('[data-journal-sentence]')?.textContent).toBe('字号记录，请参阅来源原文')
    expect(document.body.querySelectorAll('[data-journal-entry]')).toHaveLength(2)
    expect(document.body.querySelectorAll('[data-journal-sentence]')).toHaveLength(1)
  })

  it('多条证据分别显示来源、日期、链接与存档，不把首条归到其他条', async () => {
    const src = journalSources('fx-adv', loadCatalog(vectors.catalog))
    const body = { ...src.min_effective_font_size_pt, coverage: 'publisher_general' as const, url: 'https://first.invalid/guide', link: 'https://first.invalid/guide#:~:text=body', retrieved: '2025-01-01', kind: 'html' as const, archive_url: 'https://archive.invalid/first' }
    const script = { ...src.min_script_font_size_pt, coverage: 'single_journal_sample' as const, url: 'https://second.invalid/guide.pdf', link: 'https://second.invalid/guide.pdf#page=4', retrieved: '2026-02-03', kind: 'pdf' as const, archive_url: 'https://archive.invalid/second' }
    await mount(<SourceButton journal="Selected Journal" name="字号" entries={[
      ['min_effective_font_size_pt', body], ['min_script_font_size_pt', script],
    ]} />)
    await click(q('[data-journal-source]'))
    const first = q('[data-journal-entry="min_effective_font_size_pt"]')!
    const second = q('[data-journal-entry="min_script_font_size_pt"]')!
    expect(first.querySelector('[data-journal-meta]')?.textContent).toBe('first.invalid · 2025-01-01')
    expect(second.querySelector('[data-journal-meta]')?.textContent).toBe('second.invalid · 2026-02-03 · PDF')
    expect(first.querySelector('[data-journal-coverage]')?.textContent).toContain('出版社通用')
    expect(second.querySelector('[data-journal-coverage]')?.textContent).toContain('单刊样本')
    expect(second.querySelector('[data-journal-coverage]')?.textContent).toContain('是否适用于本刊未核实')
    expect(first.querySelector('[data-journal-open]')?.getAttribute('href')).toBe(body.link)
    expect(second.querySelector('[data-journal-open]')?.getAttribute('href')).toBe(script.link)
    expect(first.querySelector('[data-journal-archive]')?.getAttribute('href')).toBe(body.archive_url)
    expect(second.querySelector('[data-journal-archive]')?.getAttribute('href')).toBe(script.archive_url)
    const firstName = first.querySelector('[data-journal-open]')?.getAttribute('aria-label')
    const secondName = second.querySelector('[data-journal-open]')?.getAttribute('aria-label')
    expect(firstName).toContain('打开原网页')
    expect(secondName).toContain('打开原网页')
    expect(firstName).not.toBe(secondName)
    expect(q('[data-journal-sentence]')?.textContent).toBe('字号记录，请参阅来源原文')
  })

  it('岩土工程刊字号反例：标题不合成数值和正文类别，原文与两条出处保留', async () => {
    const src = journalSources('fx-adv', loadCatalog(vectors.catalog))
    const general = { ...src.min_effective_font_size_pt, value: 8, snapshot: null, quote: 'Make sure you use uniform lettering and sizing of your original artwork(usually 8 pt).', link: 'https://example.invalid/journal' }
    const subscript = { ...src.min_script_font_size_pt, value: 6, snapshot: null, quote: 'As a general rule, the lettering on the artwork should have a finished, printed size of 7 pt for normal text and no smaller than 6 pt for subscript and superscript characters.', link: 'https://example.invalid/publisher' }
    await mount(<SourceButton journal="Selected Journal" name="字号" entries={[
      ['min_effective_font_size_pt', general], ['min_script_font_size_pt', subscript],
    ]} />)
    await click(q('[data-journal-source]'))
    expect(q('[data-journal-sentence]')?.textContent).toBe('字号记录，请参阅来源原文')
    expect([...document.body.querySelectorAll('[data-journal-quote]')].map((node) => node.textContent)).toEqual([general.quote, subscript.quote])
    expect([...document.body.querySelectorAll('[data-journal-open]')].map((node) => node.getAttribute('href'))).toEqual([general.link, subscript.link])
    expect(q('[data-journal-entry="min_effective_font_size_pt"]')?.textContent).not.toContain('最小字号')
  })

  it('字段中文名称相同时，多条来源链接仍有不同且可见的序号', async () => {
    expect(fieldLabel('axis_policy.label_format')).toBe(fieldLabel('axis_policy.label_format_regex'))
    const source = entries('fx-alpha', 'min_raster_dpi')[0][1]
    await mount(<SourceButton journal="Selected Journal" name="坐标轴" entries={[
      ['axis_policy.label_format', { ...source, link: 'https://example.invalid/a' }],
      ['axis_policy.label_format_regex', { ...source, link: 'https://example.invalid/b' }],
    ]} />)
    await click(q('[data-journal-source]'))
    const links = [...document.body.querySelectorAll('[data-journal-open]')]
    expect(links).toHaveLength(2)
    expect(links[0].getAttribute('aria-label')).toContain('1/2')
    expect(links[1].getAttribute('aria-label')).toContain('2/2')
    expect(links[0].getAttribute('aria-label')).not.toBe(links[1].getAttribute('aria-label'))
    const rows = [...document.body.querySelectorAll('[data-journal-entry]')]
    expect(rows[0].textContent).toContain('1/2')
    expect(rows[1].textContent).toContain('2/2')
  })

  it('同一个 PDF 的不同页各有独立链接；旧快照没有范围元数据仍可用', async () => {
    const src = journalSources('fx-adv', loadCatalog(vectors.catalog))
    const body = { ...src.min_effective_font_size_pt, url: 'https://example.invalid/guide.pdf', link: 'https://example.invalid/guide.pdf#page=2', kind: 'pdf' as const }
    const script = { ...src.min_script_font_size_pt, url: body.url, link: 'https://example.invalid/guide.pdf#page=9', kind: 'pdf' as const }
    for (const source of [body, script]) {
      delete (source as Partial<typeof source>).coverage
      delete (source as Partial<typeof source>).source_scope
      delete (source as Partial<typeof source>).rule_scope
    }
    await mount(<SourceButton journal="Selected Journal" name="字号" entries={[
      ['min_effective_font_size_pt', body], ['min_script_font_size_pt', script],
    ]} />)
    await click(q('[data-journal-source]'))
    const links = [...document.body.querySelectorAll('[data-journal-open]')]
    expect(links.map((link) => link.getAttribute('href'))).toEqual([body.link, script.link])
    expect(body.coverage).toBeUndefined()
    expect(script.coverage).toBeUndefined()
  })

  it('没有出处的项不渲染按钮（期刊没规定就没有卡可开）', async () => {
    await mount(<SourceButton journal="J" entries={[]} name="x" />)
    expect(q('[data-journal-source]')).toBeNull()
  })
})

describe('要求清单', () => {
  const row = (p: string) => q(`[data-journal-row="${p}"]`)!

  it('快照没有来源的要紧项明说「当前快照未记录」且没有出处钮', async () => {
    selectJournal('fx-adv-own')
    await mount(<JournalRequirements />)
    expect(row('min_raster_dpi').textContent).toContain('当前快照未记录')
    expect(row('min_raster_dpi').querySelector('[data-journal-source]')).toBeNull()
    expect(row('page_columns').textContent).toContain('当前快照未记录')
  })

  it('typical 且没有可标的数：只写「有要求」，不显示成「5 pt」，原话在出处卡；有出处钮', async () => {
    selectJournal('fx-zeta')
    await mount(<JournalRequirements />)
    expect(row('min_effective_font_size_pt').textContent).toContain('有要求')
    expect(row('min_effective_font_size_pt').textContent).not.toContain('FIXTURE')
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
    const issueOn = (canvasId: string, min: number) =>
      ({ ruleCode: 'raster-dpi', message: msg('preflight.rasterDpi', { dpi: '1', min: String(min) }, 'errors'), objectRef: { canvasId } }) as unknown as ValidationIssue
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
    const dpiOf = async (canvasId: string, min: number) => {
      await mount(<IssueJournalSource issue={issueOn(canvasId, min)} />)
      const btn = q('[data-journal-source]')
      if (!btn) return null
      await click(btn)
      const text = q('[data-journal-sentence]')?.textContent ?? null
      await act(async () => {
        document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      })
      return text
    }
    expect(await dpiOf(a, 300)).toBe('位图分辨率 ≥ 300 dpi')
    expect(await dpiOf('cB', 450)).toBe('位图分辨率 450 dpi')
    // 活动画布 A 不按期刊：B 的出处钮仍在，A 自己没有
    act(() => clearJournal())
    expect(await dpiOf(a, 300)).toBeNull()
    expect(await dpiOf('cB', 450)).toBe('位图分辨率 450 dpi')
  })

  it('DPI 只有快照中有效且数值匹配的预检来源才能归因；旧快照明确未核实', async () => {
    const cat = loadCatalog(vectors.catalog)
    selectJournal('fx-delta')
    const canvasId = useDocumentStore.getState().activeCanvasId
    const issue = exportContextIssues({ formats: ['png'], dpi: 299 }, journalProfile('fx-delta', cat), { documentId: 'd', canvasId })[0]
    await mount(<ul><IssueRow issue={issue} depth={1} current={false} onLocate={() => {}} activeCanvasId={canvasId} /></ul>)
    expect(q('[data-journal-dpi-source-unverified]')?.textContent).toContain('期刊出处未在当前快照中核实')
    const aria = q('[data-journal-dpi-source-unverified]')?.closest('button')?.getAttribute('aria-label')
    expect(aria).toContain('检查阈值')
    expect(aria).not.toContain('要求')
    expect(q('[data-journal-source]')).toBeNull()
    const template = journalSources('fx-beta', cat).min_raster_dpi
    const exercise = async (source: typeof template | undefined, confirmed: boolean) => {
      await act(async () => useDocumentStore.setState((st) => ({
        doc: { ...st.doc, profile: { ...st.doc.profile!, journalSources: source ? { min_raster_dpi: source } : undefined } },
      })))
      expect(!!q('[data-journal-source]')).toBe(confirmed)
      expect(!!q('[data-journal-dpi-source-unverified]')).toBe(!confirmed)
    }
    await exercise({ ...template, value: 300, affects: 'preflight', unset: false }, true)
    await exercise({ ...template, value: 450, affects: 'preflight', unset: false }, false)
    await exercise({ ...template, value: 300, affects: 'advice', unset: false }, false)
    await exercise({ ...template, value: 300, affects: 'preflight', unset: true }, false)
    await exercise(undefined, false)
    await act(async () => clearJournal())
    expect(q('[data-journal-dpi-source-unverified]')).toBeNull()
  })

  it('没按期刊选过，或这条检查在该期刊里没有出处：什么都不画', async () => {
    await mount(<IssueJournalSource issue={issue('page-width')} />)
    expect(q('[data-journal-source]')).toBeNull()
    selectJournal('fx-delta')
    await mount(<IssueJournalSource issue={issue('page-width')} />)
    expect(q('[data-journal-source]')).toBeNull()
  })
})

describe('远程目录里还没下载的刊：选中时的胶囊提示', () => {
  const remote = () => {
    useJournalCatalogStore.getState().setCatalog(vectors.catalog, '/api/journal-catalog/assets/')
    useJournalCatalogStore.getState().setLoaded([])
  }
  const reply = (body: unknown) => {
    globalThis.fetch = (async () => new Response(JSON.stringify(body), { status: 200 })) as typeof fetch
  }
  const statusKey = () => (useUiStore.getState().status as { key?: string } | null)?.key
  afterEach(() => {
    globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
    useJournalCatalogStore.setState({ catalog: loadCatalog(), assetBase: BUNDLED_ASSET_BASE, loaded: null })
  })

  it('额度用完：不绑定，一条「稍后再试」胶囊', async () => {
    remote()
    reply({ state: 'quota', code: 'quota_day', retry_after_s: 60, bundle: null })
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    await click(q('[data-journal-option="fx-beta"]'))
    expect(useDocumentStore.getState().doc.profile?.journalRef).toBeUndefined()
    expect(statusKey()).toBe('journal.quotaLater')
  })

  it('没网又没缓存：不绑定，一条「需要联网下载」胶囊', async () => {
    remote()
    reply({ state: 'offline', code: 'network', bundle: null })
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    await click(q('[data-journal-option="fx-beta"]'))
    expect(useDocumentStore.getState().doc.profile?.journalRef).toBeUndefined()
    expect(statusKey()).toBe('journal.needsNetwork')
  })

  /** 可手动放行的下载：返回 release(body)，在它被调用之前 fetch 一直挂着 */
  const deferred = () => {
    const releases: ((body: unknown) => void)[] = []
    globalThis.fetch = (() =>
      new Promise<Response>((resolve) => {
        releases.push((body) => resolve(new Response(JSON.stringify(body), { status: 200 })))
      })) as typeof fetch
    return releases
  }
  const bundleOf = (id: string) => {
    const j = (vectors.catalog.journals as Record<string, { publisher: string }>)[id]
    return {
      journal_id: id,
      journal: j,
      publisher_id: j.publisher,
      publisher: (vectors.catalog.publishers as Record<string, unknown>)[j.publisher],
    }
  }
  const skeletonCatalog = () => {
    const full = structuredClone(vectors.catalog) as unknown as {
      publishers: Record<string, { label: string }>
      journals: Record<string, { label: string; publisher: string }>
    }
    return {
      ...full,
      publishers: Object.fromEntries(Object.entries(full.publishers).map(([k, v]) => [k, { label: v.label }])),
      journals: Object.fromEntries(Object.entries(full.journals).map(([k, v]) => [k, { label: v.label, publisher: v.publisher }])),
    }
  }

  it('下载等待期间改选别的刊：先发起的那次回来后被丢弃，不覆盖新选择', async () => {
    useJournalCatalogStore.getState().setCatalog(skeletonCatalog(), '/api/journal-catalog/assets/')
    useJournalCatalogStore.getState().setLoaded(['fx-alpha'])
    const releases = deferred()
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    await click(q('[data-journal-option="fx-beta"]')) // 要下载，挂起
    expect(releases).toHaveLength(1)
    await click(q('[data-journal-option="fx-alpha"]')) // 已在本地：立刻绑定
    expect(useDocumentStore.getState().doc.profile?.journalRef?.id).toBe('fx-alpha')
    await act(async () => releases[0]({ state: 'ready', bundle: bundleOf('fx-beta') }))
    expect(useDocumentStore.getState().doc.profile?.journalRef?.id).toBe('fx-alpha')
  })

  it('下载等待期间点「不按期刊」：回来后不再把期刊绑回去', async () => {
    useJournalCatalogStore.getState().setCatalog(skeletonCatalog(), '/api/journal-catalog/assets/')
    useJournalCatalogStore.getState().setLoaded([])
    const releases = deferred()
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    await click(q('[data-journal-option="fx-beta"]'))
    await click(q('[data-journal-none]'))
    await act(async () => releases[0]({ state: 'ready', bundle: bundleOf('fx-beta') }))
    expect(useDocumentStore.getState().doc.profile?.journalRef).toBeUndefined()
  })

  it('下载等待期间换了文档：回来后不改新文档的绑定', async () => {
    useJournalCatalogStore.getState().setCatalog(skeletonCatalog(), '/api/journal-catalog/assets/')
    useJournalCatalogStore.getState().setLoaded([])
    const releases = deferred()
    await mount(<JournalPicker />)
    await click(q('[data-journal-picker]'))
    await click(q('[data-journal-option="fx-beta"]'))
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_journal_ui_other')
    await act(async () => releases[0]({ state: 'ready', bundle: bundleOf('fx-beta') }))
    expect(useDocumentStore.getState().doc.profile?.journalRef).toBeUndefined()
  })
})

describe('historical panel width display', () => {
  it.each(['nature-chemistry', 'nature-materials', 'nature-physics'])('shows historical panel advice from the saved binding for %s', async (id) => {
    useJournalCatalogStore.setState({ catalog: loadCatalog() })
    act(() => selectJournal(id))
    const before = structuredClone(useDocumentStore.getState().doc.profile)
    await mount(<JournalRequirements />)
    const row = q('[data-journal-row="info.width_double_mm"]')!
    expect(row.textContent).toContain('图版最大宽（历史 AIP 建议）')
    expect(row.textContent).toContain('≤ 180 mm')
    expect(row.textContent).not.toContain('双栏')
    expect(row.querySelector('[data-journal-panel-history]')?.textContent).toContain('现行要求未知')
    await click(row.querySelector('[data-journal-source]'))
    expect(q('[data-journal-sentence]')?.textContent).toBe('图版最大宽（历史 AIP 建议） ≤ 180 mm')
    expect(q('[data-journal-open]')?.getAttribute('href')).toContain('nature.com/')
    expect(q('[data-journal-coverage]')).not.toBeNull()
    expect(useDocumentStore.getState().doc.profile).toEqual(before)
  })

  it('does not reclassify evidence with a submission stage', async () => {
    useJournalCatalogStore.setState({ catalog: loadCatalog() })
    act(() => selectJournal('nature-chemistry'))
    const profile = structuredClone(useDocumentStore.getState().doc.profile!)
    profile.journalSources!['info.width_double_mm'].stage = 'submission'
    act(() => useDocumentStore.setState((s) => ({ doc: { ...s.doc, profile } })))
    await mount(<JournalRequirements />)
    const row = q('[data-journal-row="info.width_double_mm"]')!
    expect(row.textContent).toContain('双栏宽')
    expect(row.querySelector('[data-journal-panel-history]')).toBeNull()
  })

  it('explains the historical-source limitation in English', async () => {
    useJournalCatalogStore.setState({ catalog: loadCatalog() })
    act(() => selectJournal('nature-chemistry'))
    await setLocale('en-US')
    await mount(<JournalRequirements />)
    const row = q('[data-journal-row="info.width_double_mm"]')!
    expect(row.textContent).toContain('Panel max (historical AIP advice)')
    expect(row.querySelector('[data-journal-panel-history]')?.textContent).toContain('Current requirements are unverified')
  })
})
