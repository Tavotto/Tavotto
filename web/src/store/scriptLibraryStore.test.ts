import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchRegistry } from '@/lib/api'
import { useScriptLibraryStore } from './scriptLibraryStore'

vi.mock('@/lib/api', () => ({
  fetchRegistry: vi.fn(),
}))

const mockFetch = vi.mocked(fetchRegistry)
const flush = () => new Promise((r) => setTimeout(r, 0))

const view = (scripts: string[]) =>
  ({
    source: '',
    scripts: {},
    candidates: [],
    conflicts: {},
    all_scripts: scripts.map((s) => ({
      script: s,
      registered: false,
      static_stems: [],
      entry_candidates: [],
      reason: 'no_static_output',
      can_probe: true,
    })),
  }) as never

beforeEach(() => {
  useScriptLibraryStore.getState().clear()
  mockFetch.mockReset()
})

describe('scriptLibraryStore', () => {
  it('切项目：pending 的 /api/registry 响应作废，绝不落进新项目（Codex 评审 P2）', async () => {
    let resolveA!: (v: unknown) => void
    mockFetch.mockImplementationOnce(
      () => new Promise((r) => (resolveA = r as never)),
    )
    const st = useScriptLibraryStore.getState()
    void st.load()

    st.clear()
    resolveA(view(['a_project_script.py']))
    await flush()
    // A 项目的清单没有落地（B 的脚本区绝不显示 A 的条目）
    expect(useScriptLibraryStore.getState().view).toBeNull()
    expect(useScriptLibraryStore.getState().loaded).toBe(false)

    // B 自己的请求照常新发、照常落地
    mockFetch.mockResolvedValueOnce(view(['b_script.py']))
    await useScriptLibraryStore.getState().load()
    expect(
      useScriptLibraryStore.getState().view?.all_scripts.map((s) => s.script),
    ).toEqual(['b_script.py'])
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })

  it('幂等去重仍然成立：同代内并发 load 只发一次请求', async () => {
    mockFetch.mockResolvedValue(view(['x.py']))
    const st = useScriptLibraryStore.getState()
    const p1 = st.load()
    const p2 = st.load()
    expect(p1).toBe(p2)
    await p1
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })
})
