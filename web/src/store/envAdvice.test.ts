/**
 * 环境建议 / 检查 / 采用在前端的投影（ADR 0114）。判据的主语：前端**保存**后端的建议、把用户的明确动作转成
 * 请求，不自写「能不能跑」；采用带着看到建议那一刻的环境代；换项目之后晚到的响应不落到新项目上。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchEngineEnvironment: vi.fn(),
  checkProjectEnvironment: vi.fn(),
  adoptEnvironmentCandidate: vi.fn(),
}))

import {
  ApiError,
  adoptEnvironmentCandidate,
  checkProjectEnvironment,
  fetchEngineEnvironment,
  type EnvCandidate,
  type EnvRecommendation,
} from '@/lib/api'
import { useEnvStore } from './envStore'

const candidate = (over: Partial<EnvCandidate> = {}): EnvCandidate => ({
  id: 'abc123',
  label: 'project_hint',
  name: '.venv',
  sources: ['project_venv'],
  scope: 'project',
  python_relative: '.venv/bin/python',
  generation: 'gen-1',
  status: 'unchecked',
  checked: false,
  health: null,
  current: false,
  ...over,
})

const recommendation = (cands: EnvCandidate[], needs = true): EnvRecommendation => ({
  version: 1,
  decision: { consent: 'none', locked_by: null, needs_decision: needs, current_id: null },
  recommended_id: cands[0]?.id ?? null,
  candidates: cands,
  python_requirement: {
    supported: { min: '3.10', max_exclusive: '3.15' },
    declared: null,
    status: 'unknown',
  },
  check: {
    executes_candidates: true,
    max_candidates: 6,
    deadline_s: 180,
    per_candidate_timeout_s: 120,
    scopes: ['project', 'machine', 'all'],
  },
})

const envWith = (rec: EnvRecommendation) =>
  ({
    ok: true,
    python: '/x/python',
    source: 'bundled',
    matplotlib: '3.11',
    managed: false,
    bundled: true,
    runtime: {} as never,
    state: 'idle',
    project: { open: true, consent: 'none', recommendation: rec },
  }) as never

beforeEach(() => {
  vi.mocked(fetchEngineEnvironment).mockReset()
  vi.mocked(checkProjectEnvironment).mockReset()
  vi.mocked(adoptEnvironmentCandidate).mockReset()
  useEnvStore.setState({ env: null, checkingEnvironment: false })
})
afterEach(() => useEnvStore.getState().resetProject())

describe('明确的检查动作', () => {
  it('把后端回的建议原样换进 env.project.recommendation（不自己改状态、不算可运行）', async () => {
    const before = recommendation([candidate()])
    const after = recommendation([candidate({ status: 'healthy', checked: true })])
    useEnvStore.setState({ env: envWith(before) })
    vi.mocked(checkProjectEnvironment).mockResolvedValue({
      checked: ['abc123'],
      skipped: [],
      cancelled: false,
      recommendation: after,
    })

    const error = await useEnvStore.getState().checkEnvironment({ script: 'fig.py' })

    expect(error).toBeNull()
    expect(checkProjectEnvironment).toHaveBeenCalledWith({ script: 'fig.py' })
    expect(useEnvStore.getState().env?.project?.recommendation).toEqual(after)
    expect(useEnvStore.getState().checkingEnvironment).toBe(false)
  })

  it('检查进行中不重复发（单飞在后端也有，这里不让按钮连点）', async () => {
    useEnvStore.setState({ env: envWith(recommendation([candidate()])), checkingEnvironment: true })
    await useEnvStore.getState().checkEnvironment()
    expect(checkProjectEnvironment).not.toHaveBeenCalled()
  })

  it('换项目之后才回来的检查结论不写进新项目', async () => {
    useEnvStore.setState({ env: envWith(recommendation([candidate()])) })
    let finish!: (v: Awaited<ReturnType<typeof checkProjectEnvironment>>) => void
    vi.mocked(checkProjectEnvironment).mockReturnValue(new Promise((r) => (finish = r)))
    vi.mocked(fetchEngineEnvironment).mockResolvedValue(envWith(recommendation([], false)))

    const pending = useEnvStore.getState().checkEnvironment()
    useEnvStore.getState().resetProject() // 切到另一个项目
    finish({
      checked: ['abc123'],
      skipped: [],
      cancelled: false,
      recommendation: recommendation([candidate({ id: 'from-project-A' })]),
    })
    await pending

    const ids = useEnvStore.getState().env?.project?.recommendation?.candidates.map((c) => c.id) ?? []
    expect(ids).not.toContain('from-project-A')
  })
})

describe('采用：绑定候选身份与环境代', () => {
  it('把候选与它的环境代交回后端，成功后换进后端回的项目状态', async () => {
    const pick = candidate()
    useEnvStore.setState({ env: envWith(recommendation([pick])) })
    vi.mocked(adoptEnvironmentCandidate).mockResolvedValue({
      ok: true,
      project: { open: true, consent: 'confirmed', source: 'project_venv' } as never,
    })

    const error = await useEnvStore.getState().adoptCandidate(pick, 'fig.py')

    expect(error).toBeNull()
    expect(adoptEnvironmentCandidate).toHaveBeenCalledWith(pick, 'fig.py')
    expect(useEnvStore.getState().env?.project?.consent).toBe('confirmed')
  })

  it('环境在看到建议之后被重建：回一句原因，并重新拿一份建议（不静默改用另一个）', async () => {
    const pick = candidate()
    useEnvStore.setState({ env: envWith(recommendation([pick])) })
    vi.mocked(adoptEnvironmentCandidate).mockRejectedValue(
      new ApiError('x', 409, { code: 'environment_changed', error: 'x' }),
    )
    vi.mocked(fetchEngineEnvironment).mockResolvedValue(
      envWith(recommendation([candidate({ generation: 'gen-2' })])),
    )

    const error = await useEnvStore.getState().adoptCandidate(pick)

    expect(error).toBeTruthy()
    expect(fetchEngineEnvironment).toHaveBeenCalled()
    expect(useEnvStore.getState().env?.project?.recommendation?.candidates[0].generation).toBe('gen-2')
  })
})
