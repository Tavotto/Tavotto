/**
 * 项目接入就绪度的前端持有者（Prompt 08 §四）。
 *
 * 守四件事：
 *
 * 1. **旧响应不覆盖新的、不落进另一个项目**——与 assetStore 同一条纪律，
 *    判据是请求序号与「发请求那一刻认领的是哪个项目」，不是"谁最后返回"；
 * 2. **fingerprint 相同 = 同一份事实**，报告对象一个字节都不换（换了引用，
 *    每个订阅它的组件都会重渲染，而屏幕上不会有任何变化）；
 * 3. **后台失败保留上一次成功那份**——磁盘上的事实没变，清空只会让界面当场
 *    空掉；
 * 4. **横幅的关闭按「项目 + 报告版本」记**，事实一变就该再说一次。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchReadiness: vi.fn(),
}))

import { fetchReadiness, type ReadinessPanel, type ReadinessReport } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { pendingCount } from '@/lib/readinessText'
import {
  bannerReport,
  resetReadinessBookkeeping,
  useProjectReadinessStore,
} from './projectReadinessStore'
import { useUiStore } from './uiStore'

const mockFetch = vi.mocked(fetchReadiness)

const panel = (over: Partial<ReadinessPanel> = {}): ReadinessPanel => ({
  id: 'Fig1.pdf',
  stem: 'Fig1',
  status: 'layout_only',
  reason_code: 'no_source_candidate',
  script: null,
  candidates: [],
  can_probe: false,
  can_manual_link: true,
  details: {},
  ...over,
})

const report = (over: Partial<ReadinessReport> = {}): ReadinessReport => {
  const panels = over.panels ?? [panel()]
  const summary = {
    total: panels.length,
    editable: 0,
    auto_linkable: 0,
    needs_probe: 0,
    conflict: 0,
    source_missing: 0,
    layout_only: 0,
    ...(over.summary ?? {}),
  }
  if (!over.summary) for (const p of panels) summary[p.status] += 1
  const base = {
    project_id: 'pj-a',
    fingerprint: 'fp-1',
    generated_at: 1,
    summary,
    panels,
    conflicts: [],
    project: { writable: true, registry_valid: true, scan_ok: true, can_rescan: true },
    issues: [],
    ...over,
  }
  // `panels` / `summary` 由上面算好，别被 `over` 里的半份盖掉
  return { ...base, panels, summary }
}

beforeEach(() => {
  localStorage.clear()
  mockFetch.mockReset()
  resetReadinessBookkeeping()
  setCurrentProjectId(null)
  useProjectReadinessStore.getState().clear()
  useUiStore.setState({ registryOpen: false })
})

describe('取回报告', () => {
  it('成功一次之后 report 就位，loading 收掉', async () => {
    mockFetch.mockResolvedValue(report())
    await useProjectReadinessStore.getState().load()
    const s = useProjectReadinessStore.getState()
    expect(s.report?.fingerprint).toBe('fp-1')
    expect(s.loading).toBe(false)
    expect(s.error).toBeNull()
  })

  it('同一批事件里的多次 refresh 合并成一个请求', async () => {
    mockFetch.mockResolvedValue(report())
    const store = useProjectReadinessStore.getState()
    await Promise.all([store.refresh(), store.refresh(), store.refresh()])
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })

  it('force 永远另起一次：用户刚写过盘，不许复用写之前发出的那个', async () => {
    mockFetch.mockResolvedValue(report())
    const store = useProjectReadinessStore.getState()
    const merged = store.refresh()
    const forced = store.load({ force: true })
    await Promise.all([merged, forced])
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })
})

describe('并发：旧响应不覆盖新的', () => {
  it('先发的那次晚回来，落地的仍是后发的那一份', async () => {
    let releaseFirst!: (r: ReadinessReport) => void
    mockFetch.mockReturnValueOnce(
      new Promise<ReadinessReport>((res) => {
        releaseFirst = res
      }),
    )
    mockFetch.mockResolvedValueOnce(report({ fingerprint: 'fp-new' }))

    const first = useProjectReadinessStore.getState().load({ force: true })
    const second = useProjectReadinessStore.getState().load({ force: true })
    await second
    expect(useProjectReadinessStore.getState().report?.fingerprint).toBe('fp-new')

    releaseFirst(report({ fingerprint: 'fp-old' }))
    await first
    expect(useProjectReadinessStore.getState().report?.fingerprint).toBe('fp-new')
  })

  it('切了项目：在途那一份一个字节都不许落地', async () => {
    setCurrentProjectId('pj-a')
    let release!: (r: ReadinessReport) => void
    mockFetch.mockReturnValueOnce(
      new Promise<ReadinessReport>((res) => {
        release = res
      }),
    )
    const inflight = useProjectReadinessStore.getState().load()
    // 请求还在路上时用户换了项目
    setCurrentProjectId('pj-b')
    release(report({ project_id: 'pj-a' }))
    await inflight
    expect(useProjectReadinessStore.getState().report).toBeNull()
  })
})

describe('fingerprint', () => {
  it('相同 = 同一份事实，报告对象不换引用（否则白重渲染一整轮）', async () => {
    // **每次都造一个新对象**：`mockResolvedValue(report())` 只求值一次，两次
    // 响应是同一个引用，那时 `toBe` 恒真——判据会变成"我摆好的东西还在"，
    // 而不是"store 复用了旧的那一份"。
    mockFetch.mockImplementation(async () => report())
    await useProjectReadinessStore.getState().load()
    const first = useProjectReadinessStore.getState().report
    const second = await useProjectReadinessStore.getState().load({ force: true })
    // 先证明这一轮真的换过一个新对象进来（否则下一条断言什么也没证明）
    expect(second).toBe(first)
    expect(useProjectReadinessStore.getState().report).toBe(first)
  })

  it('变了就换成新的那一份', async () => {
    mockFetch.mockResolvedValueOnce(report())
    await useProjectReadinessStore.getState().load()
    mockFetch.mockResolvedValueOnce(report({ fingerprint: 'fp-2' }))
    await useProjectReadinessStore.getState().load({ force: true })
    expect(useProjectReadinessStore.getState().report?.fingerprint).toBe('fp-2')
  })
})

describe('失败', () => {
  it('后台刷新失败：上一次成功那份原样留着，只多一条 error', async () => {
    mockFetch.mockResolvedValueOnce(report())
    await useProjectReadinessStore.getState().load()
    mockFetch.mockRejectedValueOnce(new Error('读不回来'))
    await useProjectReadinessStore.getState().load({ force: true })
    const s = useProjectReadinessStore.getState()
    expect(s.report?.fingerprint).toBe('fp-1')
    expect(s.error).toBe('读不回来')
  })

  it('再成功一次就把 error 清掉——哪怕事实没变（那一位说的是"取回来了没有"）', async () => {
    mockFetch.mockResolvedValueOnce(report())
    await useProjectReadinessStore.getState().load()
    mockFetch.mockRejectedValueOnce(new Error('读不回来'))
    await useProjectReadinessStore.getState().load({ force: true })
    mockFetch.mockResolvedValueOnce(report())
    await useProjectReadinessStore.getState().load({ force: true })
    expect(useProjectReadinessStore.getState().error).toBeNull()
  })
})

describe('横幅的关闭状态', () => {
  const load = async (r: ReadinessReport) => {
    mockFetch.mockResolvedValueOnce(r)
    await useProjectReadinessStore.getState().load({ force: true })
  }

  it('关掉之后这一版不再显示', async () => {
    await load(report())
    expect(bannerReport(useProjectReadinessStore.getState())).not.toBeNull()
    useProjectReadinessStore.getState().dismissBanner()
    expect(bannerReport(useProjectReadinessStore.getState())).toBeNull()
  })

  it('落在本机：重开一次会话仍然记得', async () => {
    await load(report())
    useProjectReadinessStore.getState().dismissBanner()
    // 模拟重启：内存清空，磁盘（localStorage）留着
    useProjectReadinessStore.getState().clear()
    resetReadinessBookkeeping()
    await load(report())
    expect(bannerReport(useProjectReadinessStore.getState())).toBeNull()
  })

  it('事实变了（fingerprint 换代）就再说一次', async () => {
    await load(report())
    useProjectReadinessStore.getState().dismissBanner()
    await load(report({ fingerprint: 'fp-2' }))
    expect(bannerReport(useProjectReadinessStore.getState())).not.toBeNull()
  })

  it('项目之间互不影响：A 关过不代表 B 也关过，切回 A 时仍然算数', async () => {
    // 走的是真实那条路：换项目先 `clear()`（`projectStore.resetForNewProject`
    // 就是这么做的），报告的 fingerprint 也不同——`project_id` 在被哈希的
    // 那份 body 里，两个项目**不可能**给出同一个 fingerprint。
    await load(report({ project_id: 'pj-a', fingerprint: 'fp-a' }))
    useProjectReadinessStore.getState().dismissBanner()

    useProjectReadinessStore.getState().clear()
    await load(report({ project_id: 'pj-b', fingerprint: 'fp-b' }))
    expect(bannerReport(useProjectReadinessStore.getState())).not.toBeNull()

    useProjectReadinessStore.getState().clear()
    await load(report({ project_id: 'pj-a', fingerprint: 'fp-a' }))
    expect(bannerReport(useProjectReadinessStore.getState())).toBeNull()
  })

  it('localStorage 里是坏 blob：当成"谁都没关过"，绝不因此再也不提示', async () => {
    localStorage.setItem('tavotto.readinessDismissed', '{ 这不是 JSON')
    await load(report())
    expect(bannerReport(useProjectReadinessStore.getState())).not.toBeNull()
    // 而且照样存得进去（坏 blob 被整份换掉，不是永久卡住）
    useProjectReadinessStore.getState().dismissBanner()
    expect(bannerReport(useProjectReadinessStore.getState())).toBeNull()
  })

  it('blob 里混着非字符串：坏的那几条丢掉，好的那条仍然作数', async () => {
    localStorage.setItem(
      'tavotto.readinessDismissed',
      JSON.stringify({ 'pj-a': 'fp-1', 'pj-junk': { nested: true } }),
    )
    await load(report({ project_id: 'pj-a' }))
    expect(bannerReport(useProjectReadinessStore.getState())).toBeNull()
  })
})

describe('横幅的显示条件', () => {
  it('全部可编辑 = 没有横幅（那时它只是噪音）', () => {
    const r = report({
      panels: [panel({ status: 'editable', reason_code: 'registered_source', script: 'a.py' })],
    })
    expect(bannerReport({ report: r, dismissed: null })).toBeNull()
  })

  it('空项目 = 没有横幅', () => {
    const r = report({ panels: [] })
    expect(bannerReport({ report: r, dismissed: null })).toBeNull()
  })

  it('报告还没到 = 没有横幅（首次加载中不闪一条空提示）', () => {
    expect(bannerReport({ report: null, dismissed: null })).toBeNull()
  })

  it('「待连接」是四个非终态的合计，不含 layout_only', () => {
    const r = report({
      panels: [
        panel({ id: 'a.pdf', stem: 'a', status: 'auto_linkable' }),
        panel({ id: 'b.pdf', stem: 'b', status: 'needs_probe' }),
        panel({ id: 'c.pdf', stem: 'c', status: 'conflict' }),
        panel({ id: 'd.pdf', stem: 'd', status: 'source_missing' }),
        panel({ id: 'e.pdf', stem: 'e', status: 'layout_only' }),
        panel({ id: 'f.pdf', stem: 'f', status: 'editable' }),
      ],
    })
    expect(pendingCount(r.summary)).toBe(4)
  })
})

describe('聚焦与开关', () => {
  it('focusPanel 打开那个既有的开关，并记下要滚到哪一张', () => {
    mockFetch.mockResolvedValue(report())
    useProjectReadinessStore.getState().focusPanel('Fig1.pdf')
    expect(useUiStore.getState().registryOpen).toBe(true)
    expect(useProjectReadinessStore.getState().focusId).toBe('Fig1.pdf')
  })

  it('关闭时清掉聚焦：下次打开不该再高亮同一行', () => {
    mockFetch.mockResolvedValue(report())
    useProjectReadinessStore.getState().focusPanel('Fig1.pdf')
    useProjectReadinessStore.getState().closeCenter()
    expect(useUiStore.getState().registryOpen).toBe(false)
    expect(useProjectReadinessStore.getState().focusId).toBeNull()
  })
})

describe('换项目', () => {
  it('clear() 之后报告、错误、聚焦全没了', async () => {
    mockFetch.mockResolvedValue(report())
    await useProjectReadinessStore.getState().load()
    useProjectReadinessStore.getState().focusPanel('Fig1.pdf')
    useProjectReadinessStore.getState().clear()
    const s = useProjectReadinessStore.getState()
    expect(s.report).toBeNull()
    expect(s.focusId).toBeNull()
    expect(s.error).toBeNull()
  })
})
