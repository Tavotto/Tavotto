/**
 * 渲染环境卡片里「项目已改用：<路径>」那一行的缘由（ADR 0044 / 0107）：缺包时无提示自动采用的系统 Python
 * 不是「你为项目选择的」——措辞按 `automatic` 分；两种都给「改用内置环境」一键改回。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchEngineEnvironment: vi.fn(),
  adoptEnvironmentCandidate: vi.fn(),
}))

import { adoptEnvironmentCandidate, type EngineEnvironment, type EnvRecommendation } from '@/lib/api'
import { EngineEnvironmentCard } from '@/components/EngineEnvironmentCard'
import { t } from '@/i18n'
import { useEnvStore } from '@/store/envStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const en = (key: string, values?: Record<string, unknown>) =>
  t(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

const envWith = (automatic: boolean): EngineEnvironment =>
  ({
    ok: true,
    python: 'C:\\Python312\\python.exe',
    source: 'system',
    matplotlib: '3.11.2',
    managed: false,
    bundled: false,
    runtime: {} as never,
    state: 'idle',
    project: {
      open: true,
      source: 'system',
      python: 'C:\\Python312\\python.exe',
      automatic,
      trigger: automatic ? 'missing_dependency' : 'user_selected',
      module: 'adjustText',
    },
  }) as never

let host: HTMLDivElement
let root: Root
async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(node)
  })
  await act(async () => {})
}
const text = () => document.body.textContent ?? ''

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('项目改用的系统 Python：缘由按 automatic 分', () => {
  it('自动采用的：说「已自动改用这台机器上装着它的 Python」，不说「你选择的」', async () => {
    useEnvStore.setState({ env: envWith(true) })
    await render(<EngineEnvironmentCard />)
    expect(text()).toContain(en('projectEnvUsingSystem', { path: 'C:\\Python312\\python.exe' }))
    expect(text()).toContain(en('projectEnvWhySystemAuto', { module: 'adjustText' }))
    expect(text()).not.toContain(en('projectEnvWhySystem', { module: 'adjustText' }))
    expect(text()).toContain(en('projectEnvUseBuiltIn'))
  })

  it('用户挑的：仍说「已改用你为项目选择的现有环境」', async () => {
    useEnvStore.setState({ env: envWith(false) })
    await render(<EngineEnvironmentCard />)
    expect(text()).toContain(en('projectEnvWhySystem', { module: 'adjustText' }))
    expect(text()).not.toContain(en('projectEnvWhySystemAuto', { module: 'adjustText' }))
    expect(text()).toContain(en('projectEnvUseBuiltIn'))
  })
})

// ---------------------------------------------------------------- 环境建议（ADR 0114）
const adviceEnv = (rec: Partial<EnvRecommendation['decision']> & { recommended?: boolean }): EngineEnvironment =>
  ({
    ok: true,
    python: '/x/python',
    source: 'bundled',
    matplotlib: '3.11.2',
    managed: false,
    bundled: true,
    runtime: {} as never,
    state: 'idle',
    project: {
      open: true,
      source: 'bundled',
      recommendation: {
        version: 1,
        decision: {
          consent: 'none',
          locked_by: rec.locked_by ?? null,
          needs_decision: rec.needs_decision ?? true,
          current_id: null,
        },
        recommended_id: rec.recommended === false ? null : 'cand-1',
        candidates: [
          {
            id: 'cand-1',
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
          },
          { id: 'builtin', label: 'bundled', name: '', sources: [], scope: 'machine', python_relative: null, generation: '', status: 'unchecked', checked: false, health: null, current: false, read_only: true },
        ],
        python_requirement: { supported: { min: '3.10', max_exclusive: '3.15' }, declared: null, status: 'unknown' },
        check: { executes_candidates: true, max_candidates: 6, deadline_s: 180, per_candidate_timeout_s: 120, scopes: [] },
      },
    },
  }) as never

describe('环境建议：一句话 + 一个主按钮', () => {
  it('项目有自己的环境、用户还没决定：问一句，主按钮「使用它」把候选与环境代交回后端', async () => {
    vi.mocked(adoptEnvironmentCandidate).mockResolvedValue({
      ok: true,
      project: { open: true } as never,
    })
    useEnvStore.setState({ env: adviceEnv({}) })
    await render(<EngineEnvironmentCard />)

    expect(text()).toContain(en('envAdviceAsk', { name: '.venv/bin/python' }))
    const use = Array.from(document.querySelectorAll('button')).find((b) => b.textContent === en('envAdviceUse'))!
    await act(async () => use.click())

    expect(adoptEnvironmentCandidate).toHaveBeenCalledTimes(1)
    const [cand] = vi.mocked(adoptEnvironmentCandidate).mock.calls[0]
    expect(cand.id).toBe('cand-1')
    expect(cand.generation).toBe('gen-1')
  })

  it('已经决定过（needs_decision=false）：不再问', async () => {
    useEnvStore.setState({ env: adviceEnv({ needs_decision: false }) })
    await render(<EngineEnvironmentCard />)
    expect(text()).not.toContain(en('envAdviceUse'))
  })

  it('全局解释器压着：不给采用按钮，如实说为什么', async () => {
    useEnvStore.setState({ env: adviceEnv({ locked_by: { source: 'env_override' }, needs_decision: false }) })
    await render(<EngineEnvironmentCard />)
    expect(text()).toContain(en('envAdviceLocked'))
    expect(text()).not.toContain(en('envAdviceUse'))
  })
})

describe('ADR 0114 之前自动采用的记录（legacy_auto）：一键确认（T09）', () => {
  const legacyEnv = () => {
    const env = adviceEnv({ needs_decision: false }) as unknown as {
      project: { recommendation: EnvRecommendation }
    }
    const rec = env.project.recommendation
    rec.decision.consent = 'legacy_auto'
    rec.decision.current_id = 'cand-1'
    rec.candidates[0].current = true
    return env as unknown as EngineEnvironment
  }

  it('一句话 + 一个主按钮；确认 = 对此刻在用的那个候选走同一个采用端点', async () => {
    vi.mocked(adoptEnvironmentCandidate).mockReset()
    vi.mocked(adoptEnvironmentCandidate).mockResolvedValue({ ok: true, project: { open: true } as never })
    useEnvStore.setState({ env: legacyEnv() })
    await render(<EngineEnvironmentCard />)
    const row = document.querySelector('[data-env-advice-legacy]')!
    expect(row.textContent).toContain(en('envAdviceLegacy', { name: '.venv/bin/python' }))
    const buttons = row.querySelectorAll('button')
    expect(buttons.length).toBe(1)
    await act(async () => (buttons[0] as HTMLButtonElement).click())
    expect(adoptEnvironmentCandidate).toHaveBeenCalledTimes(1)
    expect(vi.mocked(adoptEnvironmentCandidate).mock.calls[0][0].id).toBe('cand-1')
  })

  it('已经明确确认过（confirmed）：不再问', async () => {
    const env = legacyEnv() as unknown as { project: { recommendation: EnvRecommendation } }
    env.project.recommendation.decision.consent = 'confirmed'
    useEnvStore.setState({ env: env as unknown as EngineEnvironment })
    await render(<EngineEnvironmentCard />)
    expect(document.querySelector('[data-env-advice-legacy]')).toBeNull()
  })
})
