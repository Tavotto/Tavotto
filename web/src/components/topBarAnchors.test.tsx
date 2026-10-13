/**
 * 顶栏的两个锚点（2026-10 设计审计第二轮）：提示条撤出工作面板，状态回到它说的那个对象旁边。
 *
 *   - **项目锚点** `ProjectStatusPill`：接入状态贴在项目名后面。判据与此前的提示条同一份（`bannerReport`）：
 *     该说的时候说清楚（几张能编辑、几张待连接、几张仅版面），不该说的时候一个字不说
 *     （全部可编辑、空项目、报告还没到、这一版已经关过、接入中心开着）。
 *   - **文档锚点** `SaveStateLabel` 的说明块：会丢编辑的两件（冲突 / 保存失败）进入时自动展开一次、不搬焦点；
 *     不打断编辑的几件不自动展开。
 *
 * 判据的主语：胶囊在不在 = `[data-project-status-pill]` / `[data-doc-status-pill]` 在不在 DOM 里；说明块开没开 =
 * 它在 portal 里的根（`[data-*-popover]`）在不在 `document`；焦点 = `document.activeElement`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReadinessPanel, ReadinessReport, ReadinessStatus } from '@/lib/api'
import { emptyProject, type TextObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { resetReadinessBookkeeping, useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'
import { ProjectStatusPill, SaveStateLabel } from './TopBar'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panelOf = (id: string, status: ReadinessStatus): ReadinessPanel => ({
  id,
  stem: id.replace(/\.[^.]+$/, ''),
  status,
  reason_code: status === 'editable' ? 'registered_source' : 'no_source_candidate',
  script: status === 'editable' ? 'a.py' : null,
  candidates: [],
  can_probe: false,
  can_manual_link: true,
  details: {},
})

function reportOf(statuses: ReadinessStatus[], fingerprint = 'fp-1'): ReadinessReport {
  const panels = statuses.map((s, i) => panelOf(`Fig${i}.pdf`, s))
  const summary = {
    total: panels.length,
    editable: 0,
    auto_linkable: 0,
    needs_probe: 0,
    conflict: 0,
    source_missing: 0,
    layout_only: 0,
  }
  for (const p of panels) summary[p.status] += 1
  return {
    project_id: 'pj-a',
    fingerprint,
    generated_at: 1,
    summary,
    panels,
    conflicts: [],
    project: { writable: true, registry_valid: true, scan_ok: true, can_rescan: true },
    issues: [],
  }
}

let host: HTMLElement
let root: Root

async function mount(node: React.ReactElement) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(node))
}

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('项目锚点：显示条件', () => {
  const pill = () => host.querySelector<HTMLButtonElement>('[data-project-status-pill]')
  const popover = () => document.querySelector('[data-project-status-popover]')
  const openPill = () => act(async () => pill()!.click())

  beforeEach(() => {
    localStorage.clear()
    resetReadinessBookkeeping()
    useProjectReadinessStore.getState().clear()
    useUiStore.setState({ registryOpen: false })
  })

  it('有待连接的图：warn 胶囊说待连接的数；点开是带四个数的全句', async () => {
    useProjectReadinessStore.setState({ report: reportOf(['editable', 'editable', 'needs_probe', 'layout_only']) })
    await mount(<ProjectStatusPill />)
    expect(pill()!.dataset.projectStatusPill).toBe('warn')
    expect(pill()!.textContent).toContain('1 张待连接')
    await openPill()
    const text = popover()!.textContent ?? ''
    // 四个数字都在，而且分得开：待连接与仅版面不是同一件事
    expect(text).toContain('4')
    expect(text).toContain('2 张可编辑')
    expect(text).toContain('1 张待连接')
    expect(text).toContain('1 张仅版面')
  })

  it('只剩仅版面的图：中性胶囊——没有源脚本不是错误，不画成警告', async () => {
    useProjectReadinessStore.setState({ report: reportOf(['editable', 'layout_only', 'layout_only']) })
    await mount(<ProjectStatusPill />)
    expect(pill()!.dataset.projectStatusPill).toBe('neutral')
    expect(pill()!.textContent).toContain('2 张仅版面')
  })

  it('文案里不出现实现术语', async () => {
    useProjectReadinessStore.setState({ report: reportOf(['conflict', 'editable']) })
    await mount(<ProjectStatusPill />)
    await openPill()
    expect(`${pill()!.textContent} ${popover()!.textContent}`).not.toMatch(/registry|注册表|stem|probe|AST|manifest/i)
  })

  it('全部可编辑 / 空项目 / 报告还没到 / 接入中心开着：一个字都不说', async () => {
    useProjectReadinessStore.setState({ report: reportOf(['editable', 'editable']) })
    await mount(<ProjectStatusPill />)
    expect(pill()).toBeNull()
    await act(async () => useProjectReadinessStore.setState({ report: reportOf([]) }))
    expect(pill()).toBeNull()
    await act(async () => useProjectReadinessStore.getState().clear())
    expect(pill()).toBeNull()
    await act(async () => {
      useProjectReadinessStore.setState({ report: reportOf(['layout_only']) })
      useUiStore.setState({ registryOpen: true })
    })
    expect(pill()).toBeNull()
  })
})

describe('项目锚点：两个动作', () => {
  const pill = () => host.querySelector<HTMLButtonElement>('[data-project-status-pill]')
  const action = (name: 'open-center' | 'dismiss') =>
    document.querySelector<HTMLButtonElement>(`[data-project-status-action="${name}"]`)!

  beforeEach(async () => {
    localStorage.clear()
    resetReadinessBookkeeping()
    useProjectReadinessStore.getState().clear()
    useUiStore.setState({ registryOpen: false })
    useProjectReadinessStore.setState({ report: reportOf(['layout_only'], 'fp-1') })
    await mount(<ProjectStatusPill />)
    await act(async () => pill()!.click())
  })

  it('「查看接入状态」打开接入中心', async () => {
    await act(async () => action('open-center').click())
    expect(useUiStore.getState().registryOpen).toBe(true)
  })

  it('「关闭」之后这一版不再出现，报告本身原样留着', async () => {
    // 只关这一版（事实变了会再出现，见下一条），所以按钮文案不许诺永久（Codex #947）
    expect(action('dismiss').textContent).not.toMatch(/不再|again|never/i)
    await act(async () => action('dismiss').click())
    expect(pill()).toBeNull()
    expect(useProjectReadinessStore.getState().report).not.toBeNull()
  })

  it('关掉之后事实变了（fingerprint 换代）：重新出现', async () => {
    await act(async () => action('dismiss').click())
    expect(pill()).toBeNull()
    await act(async () => useProjectReadinessStore.setState({ report: reportOf(['layout_only'], 'fp-2') }))
    expect(pill()).not.toBeNull()
  })
})

describe('文档锚点：会丢编辑的两件自动展开一次', () => {
  const text: TextObject = {
    id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
    color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
  }
  const popover = (issue: string) => document.querySelector(`[data-doc-status-popover="${issue}"]`)

  beforeEach(async () => {
    localStorage.clear()
    // 写盘一律成功：这里量的是芯片的展开规则，不让一轮真的自动保存把状态改成「保存失败」
    globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_anchor')
    useDocumentStore.getState().silent((d) => {
      d.objects.push(text)
    })
    useDocumentStore.setState({ saveState: 'saved', saveIssue: null, lastPersisted: Date.now() })
    useProjectStore.setState({ lastDocumentIssue: null })
    // 冻住计时器：真的自动保存一轮会撞上 404 的 fetch 桩、把状态改成保存失败（与 topBarSaveLabel.test 同一做法）
    vi.useFakeTimers()
    await mount(<SaveStateLabel />)
  })

  afterEach(() => {
    vi.useRealTimers()
    useDocumentStore.setState({ saveState: 'saved', saveIssue: null })
    useProjectStore.setState({ lastDocumentIssue: null })
  })

  it('外部冲突：说明块自动摆出来，焦点不被搬走', async () => {
    const before = document.activeElement
    await act(async () =>
      useDocumentStore.setState({
        saveState: 'conflict',
        saveIssue: { kind: 'stale', docId: useDocumentStore.getState().documentId } as never,
      }),
    )
    const pop = popover('conflict')
    expect(pop, '冲突进入时说明块自动展开').not.toBeNull()
    expect(pop!.querySelector('[data-doc-conflict-action="reload"]')).not.toBeNull()
    expect(pop!.contains(document.activeElement), '自动展开不抢焦点').toBe(false)
    expect(document.activeElement).toBe(before)
  })

  it('播报区是进入出事前就挂着的同一个节点：从已保存到保存失败，节点不换、只是填字', async () => {
    await act(async () => useDocumentStore.setState({ saveState: 'saved', saveIssue: null }))
    const before = host.querySelector('[data-save-live]')
    expect(before, '已保存时播报区已经挂着').not.toBeNull()
    expect(before!.textContent).toBe('')
    await act(async () => useDocumentStore.setState({ saveState: 'save_error', saveIssue: null }))
    const after = host.querySelector('[data-save-live]')
    expect(after, '同一个节点（新挂的 live region 读屏多半不播）').toBe(before)
    expect(after!.textContent).not.toBe('')
  })

  it('空排版（不显示标签）也先挂着空的播报区：保存失败时填进的是同一个节点', async () => {
    await act(async () => {
      useDocumentStore.getState().silent((d) => {
        d.objects.length = 0
      })
      useDocumentStore.setState({ saveState: 'saved', saveIssue: null })
    })
    expect(host.querySelector('[data-save-state]'), '空排版不显示保存标签').toBeNull()
    const before = host.querySelector('[data-save-live]')
    expect(before, '播报区已经挂着').not.toBeNull()
    expect(before!.textContent).toBe('')
    await act(async () => useDocumentStore.setState({ saveState: 'save_error', saveIssue: null }))
    expect(host.querySelector('[data-save-live]')).toBe(before)
    expect(before!.textContent).not.toBe('')
  })

  it('保存失败：同样自动展开', async () => {
    await act(async () => useDocumentStore.setState({ saveState: 'save_error', saveIssue: null }))
    expect(popover('save_error')).not.toBeNull()
  })

  it('自动展开后经说明块里的按钮关掉，再手动点开：照常把焦点搬进说明块', async () => {
    await act(async () =>
      useDocumentStore.setState({
        saveState: 'conflict',
        saveIssue: { kind: 'stale', docId: useDocumentStore.getState().documentId } as never,
      }),
    )
    // 「另存为…」：关掉说明块、打开另存对话框；用户随后取消对话框，冲突还在
    await act(async () => popover('conflict')!.querySelector<HTMLButtonElement>('[data-doc-conflict-action="save-as"]')!.click())
    await act(async () => useUiStore.getState().setLayoutOpen(false))
    expect(useDocumentStore.getState().saveState).toBe('conflict')
    expect(popover('conflict'), '按钮已把说明块关掉').toBeNull()
    await act(async () => host.querySelector<HTMLButtonElement>('[data-doc-status-pill="conflict"]')!.click())
    const pop = popover('conflict')
    expect(pop).not.toBeNull()
    // Codex #947：按钮关闭不走 onOpenChange 时，「自动展开」标记残留，手动打开也被当成不搬焦点
    expect(pop!.contains(document.activeElement), '手动打开时焦点进说明块').toBe(true)
  })

  it('不打断编辑的事（上次的排版没打开）：只有胶囊，不自动展开', async () => {
    await act(async () => {
      useDocumentStore.setState({ saveState: 'saved', saveIssue: null })
      useProjectStore.setState({ lastDocumentIssue: { name: '旧排版' } as never })
    })
    expect(host.querySelector('[data-doc-status-pill="last_doc"]')).not.toBeNull()
    expect(popover('last_doc')).toBeNull()
  })
})
