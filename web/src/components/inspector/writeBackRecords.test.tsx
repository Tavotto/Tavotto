/**
 * 写回窗口里的「写回记录」页（2026-10-01，设计稿 C11：原属性栏的「历史」弹层）。
 *
 * 钉住的是「页什么时候出现」与「有记录时是不是真的看得见」：
 *   - 一条记录都没有：窗口就是原来那一页，没有页签（不摆一个空页）；
 *   - 有记录：两个页签；属性栏入口带着 `initialPage="records"` 直接落在记录页；
 *   - 读不到记录：如实报错，页签照样在——入口悄悄消失会让人以为从没写回过；
 *   - 恢复：仍然是原来那条路（`restoreHistory` + 把基线放回面板），不是只关个窗口；
 *   - `detached`（同步修改的目标图不在画布上）不查记录、没有这一页。
 * 恢复逻辑本身没有改，这里只守入口与外壳。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchHistory, restoreHistory, type HistoryVersion } from '@/lib/api'
import { literal, t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { WriteBackDialog, WriteBackRecordsButton } from './UpdateSourceButton'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchHistory: vi.fn(),
  restoreHistory: vi.fn(),
}))

globalThis.fetch = (async () =>
  new Response(JSON.stringify({ figures_dir: '/figs', panels: [] }), { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const vh = (k: string, v?: Record<string, unknown>) => t(`versionHistory.${k}`, { ns: 'inspector', ...v })
const wb = (k: string) => t(`writeBack.${k}`, { ns: 'inspector' })

const panel = (over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 80,
    fileId: 'A.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 80, script: 'a.py',
    overrides: [{ gid: 'axes_0.title', prop: 'fontsize', value: 11 }],
    ...over,
  }) as unknown as PanelObject

const version = (n: number): HistoryVersion =>
  ({ n, ts: '2026-09-06 14:59:00', count: 3, patches: [] }) as unknown as HistoryVersion

let root: Root
let host: HTMLDivElement

async function mount(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
  // 记录是挂载后异步取的
  await act(async () => {
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}

/** 对话框走 Portal，落在 document.body 上 */
const text = () => document.body.textContent ?? ''
const tab = (name: 'write' | 'records') =>
  document.body.querySelector<HTMLElement>(`[data-write-back-tab="${name}"]`)
const click = async (el: Element | null | undefined) => {
  expect(el, '找不到要点的元素').toBeTruthy()
  await act(async () => {
    ;(el as HTMLElement).click()
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}
/** 同名按钮取最后一个：确认框是后挂到 body 上的，行里的同名钮排在它前面（带 loading 文案的钮文字是「恢复正在重写…」，所以按开头认） */
const byText = (label: string) =>
  [...document.body.querySelectorAll('button')].filter((b) => b.textContent?.trim().startsWith(label)).pop()

beforeEach(async () => {
  document.body.innerHTML = ''
  vi.mocked(fetchHistory).mockReset()
  vi.mocked(restoreHistory).mockReset()
  useProjectStore.setState({ project: null } as never)
  useAssetStore.setState({ byId: { 'A.pdf': { mtime: 1 } } } as never)
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_wb_records')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel())
  })
})

afterEach(async () => {
  await act(async () => root?.unmount())
  document.body.innerHTML = ''
})

describe('写回记录页什么时候出现', () => {
  it('一条记录都没有：没有页签，窗口就是「写回」那一页', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [] } as never)
    await mount(<WriteBackDialog panels={[panel()]} open onOpenChange={() => {}} />)
    expect(document.body.querySelector('[role="tablist"]')).toBeNull()
    expect(text()).toContain(wb('targetsLabel'))
    expect(text()).not.toContain(vh('origin'))
  })

  it('有记录：两个页签，默认落在「写回」页；点「写回记录」才看到起点 + 各版本', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [version(0), version(1)] } as never)
    await mount(<WriteBackDialog panels={[panel()]} open onOpenChange={() => {}} />)
    expect(tab('write')).toBeTruthy()
    expect(tab('records')).toBeTruthy()
    expect(text()).toContain(wb('targetsLabel'))
    await click(tab('records'))
    expect(text()).toContain(vh('origin'))
    expect(text()).toContain('09-06 14:59')
    // 记录页上没有「写回原始文件」确认钮：这一页只读 / 恢复
    expect(document.body.querySelector('[data-write-back="confirm"]')).toBeNull()
  })

  it('属性栏入口带 initialPage="records"：直接落在记录页，且没有可写回的修改也能进', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [version(0)] } as never)
    await mount(
      <WriteBackDialog
        panels={[panel({ overrides: [] })]}
        open
        initialPage="records"
        onOpenChange={() => {}}
      />,
    )
    expect(text()).toContain(vh('origin'))
    expect(tab('records')?.getAttribute('aria-selected')).toBe('true')
    // 切回「写回」页：没有修改就说清，确认钮灰着
    await click(tab('write'))
    expect(text()).toContain(wb('noOverridesTitle'))
    expect(document.body.querySelector<HTMLButtonElement>('[data-write-back="confirm"]')?.disabled).toBe(true)
  })

  it('读不到记录：页签照样在，进去如实报错', async () => {
    vi.mocked(fetchHistory).mockRejectedValue(new Error('后端没起来'))
    await mount(<WriteBackDialog panels={[panel()]} open initialPage="records" onOpenChange={() => {}} />)
    expect(tab('records')).toBeTruthy()
    expect(text()).toContain('后端没起来')
  })

  it('detached（目标图不在画布上）：不查记录、没有这一页', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [version(0)] } as never)
    await mount(<WriteBackDialog panels={[panel()]} open detached onOpenChange={() => {}} />)
    expect(fetchHistory).not.toHaveBeenCalled()
    expect(document.body.querySelector('[role="tablist"]')).toBeNull()
  })

  it('窗口没打开就不发请求', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [version(0)] } as never)
    await mount(<WriteBackDialog panels={[panel()]} open={false} onOpenChange={() => {}} />)
    expect(fetchHistory).not.toHaveBeenCalled()
  })
})

describe('属性栏「写回记录」入口', () => {
  it('没写回过：不渲染（不再有「暂无写回记录」的空弹层）', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [] } as never)
    await mount(<WriteBackRecordsButton panel={panel()} />)
    expect(host.querySelector('[data-write-back="records"]')).toBeNull()
  })

  it('写回过：出现，点开就是写回窗口的记录页', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [version(0)] } as never)
    await mount(<WriteBackRecordsButton panel={panel()} />)
    const btn = host.querySelector<HTMLElement>('[data-write-back="records"]')
    expect(btn?.textContent).toContain(vh('trigger'))
    expect(vh('trigger')).toBe('写回记录')
    await click(btn)
    expect(document.body.querySelector('[data-write-back-page="records"]')).toBeTruthy()
    expect(text()).toContain(vh('origin'))
  })

  it('runtime 素材没有原件：不渲染也不查', async () => {
    await mount(<WriteBackRecordsButton panel={panel({ fileKind: 'runtime' } as never)} />)
    expect(fetchHistory).not.toHaveBeenCalled()
    expect(host.querySelector('[data-write-back="records"]')).toBeNull()
  })
})

describe('项目关了写回：恢复禁用并写明原因（2026-10-01 用户拍板）', () => {
  const setReadOnly = (off: boolean) =>
    useProjectStore.setState({ project: { settings: { allow_write_back: !off } } } as never)

  it('关着：每个「恢复」都是 disabled，项内写着原因，点不出确认框', async () => {
    setReadOnly(true)
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [version(0), version(1)] } as never)
    await mount(<WriteBackDialog panels={[panel()]} open initialPage="records" onOpenChange={() => {}} />)
    const btns = [...document.body.querySelectorAll<HTMLButtonElement>('[data-write-back="restore"]')]
    expect(btns).toHaveLength(2)
    expect(btns.every((b) => b.disabled)).toBe(true)
    const reasons = document.body.querySelectorAll('[data-write-back="restore-reason"]')
    expect(reasons).toHaveLength(2)
    expect(reasons[0].textContent).toBe(vh('readOnlyReason'))
    await click(btns[0])
    expect(text()).not.toContain(vh('restoreTitle'))
  })

  it('开着：可点、没有原因行', async () => {
    setReadOnly(false)
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [version(0), version(1)] } as never)
    await mount(<WriteBackDialog panels={[panel()]} open initialPage="records" onOpenChange={() => {}} />)
    const btns = [...document.body.querySelectorAll<HTMLButtonElement>('[data-write-back="restore"]')]
    expect(btns.every((b) => !b.disabled)).toBe(true)
    expect(document.body.querySelector('[data-write-back="restore-reason"]')).toBeNull()
    await click(btns[0])
    expect(text()).toContain(vh('restoreTitle'))
  })
})

describe('恢复仍是原来那条路', () => {
  it('恢复到某一版：重写原图（restoreHistory）并把那一版的修改放回面板', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({ versions: [version(0), version(1)] } as never)
    vi.mocked(restoreHistory).mockResolvedValue({
      updated: ['A.pdf'],
      backup_dir: '/b',
      patches: [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 7 }],
    } as never)
    const closed = vi.fn()
    await mount(<WriteBackDialog panels={[panel()]} open initialPage="records" onOpenChange={closed} />)
    // 起点、v0、v1(当前)：前两行有「恢复」
    const restoreBtns = [...document.body.querySelectorAll<HTMLElement>('[data-write-back="restore"]')]
    expect(restoreBtns).toHaveLength(2)
    await click(restoreBtns[1])
    await click(byText(vh('confirmRestore')))
    expect(restoreHistory).toHaveBeenCalledWith('A.pdf', 0, 1)
    const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject
    expect(p.overrides.map((o) => o.prop)).toEqual(['fontsize'])
    expect(p.overrides[0].gid).toBe('axes_0.xlabel')
    // 恢复完成 → 写回窗口随之关掉（原先是历史弹层关掉）
    expect(closed).toHaveBeenCalledWith(false)
  })
})
