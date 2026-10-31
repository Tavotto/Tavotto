/**
 * #1254：沙盒里 glob / listdir / exists 落空、脚本目录里找得到时，错误上挂 `workdir_hint`
 * （与首开确认框同形）。错误块与素材库这一行**不论错误码**都给「在脚本目录里运行」的入口；
 * 没有 hint 不显示；`no_figures_captured` 原有的建议不变。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EngineEnvironment, WorkdirConfirmation } from '@/lib/api'
import { ErrorBlock } from '@/components/inspector/ElementInspector'
import { WorkdirHintRecovery } from '@/components/left/ScriptLibrary'
import { msg, t } from '@/i18n'
import { useEnvStore } from '@/store/envStore'
import type { ScriptRunState } from '@/store/scriptRunStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const en = (key: string) => t(`engine.${key}`, { ns: 'errors' })

const hint = {
  kind: 'workdir',
  code: 'workdir_confirmation_required',
  script: 'fig.py',
  reason: 'script_dir_evidence',
  recommended: 'project',
  options: [],
  conflicts: [],
  reads: [],
  probes: ['data/run_*.txt'],
  message: '',
  source: 'runtime',
} as unknown as WorkdirConfirmation

const sandboxEnv = {
  ok: true,
  project: { open: true, workdir: { mode: 'sandbox', modes: ['sandbox', 'project', 'project_root'] } },
} as unknown as EngineEnvironment

let host: HTMLDivElement
let root: Root
async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(node))
}
const text = () => document.body.textContent ?? ''
const error = msg('render.timeout', { minutes: 1 }, 'errors')

beforeEach(() => {
  useEnvStore.setState({ env: sandboxEnv })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.restoreAllMocks()
})

describe('渲染错误块', () => {
  it('script_error 带 hint → 显示入口，点它打开同一个确认框', async () => {
    const request = vi.fn()
    useEnvStore.setState({ requestWorkdirConfirmation: request } as never)
    await render(<ErrorBlock error={error} traceback="" code="script_error" workdirHint={hint} />)
    expect(text()).toContain(en('workdirChooseButton'))
    const button = [...document.querySelectorAll('button')].find(
      (b) => b.textContent === en('workdirChooseButton'),
    )!
    await act(async () => button.click())
    expect(request).toHaveBeenCalledWith(hint)
  })

  it('没有 hint → 不显示', async () => {
    await render(<ErrorBlock error={error} traceback="" code="script_error" />)
    expect(text()).not.toContain(en('workdirChooseButton'))
    expect(text()).not.toContain(en('workdirSuggestButton'))
  })

  it('no_figures_captured 的老行为不变：仍是 WorkdirSuggestion，没有 hint 就没有第二个入口', async () => {
    await render(<ErrorBlock error={error} traceback="" code="no_figures_captured" />)
    expect(text()).toContain(en('workdirSuggestButton'))
    expect(text()).not.toContain(en('workdirChooseButton'))
  })

  it('no_figures_captured 带 hint → 两个入口并存', async () => {
    await render(<ErrorBlock error={error} traceback="" code="no_figures_captured" workdirHint={hint} />)
    expect(text()).toContain(en('workdirSuggestButton'))
    expect(text()).toContain(en('workdirChooseButton'))
  })

  it('workdir_confirmation_required 自己的入口不重复', async () => {
    await render(
      <ErrorBlock error={error} traceback="" code="workdir_confirmation_required" confirmation={hint} workdirHint={hint} />,
    )
    expect(text().split(en('workdirChooseButton')).length - 1).toBe(1)
  })
})

describe('提示这一句说的是事实', () => {
  const envDecided = (decided: boolean) =>
    ({
      ok: true,
      project: {
        open: true,
        workdir: { mode: 'sandbox', modes: ['sandbox', 'project', 'project_root'], decided },
      },
    }) as unknown as EngineEnvironment
  const runtimeSentence = () => t('engine.workdirHintRuntime', { ns: 'errors', script: 'fig.py' })

  it('已经选过「继续沙盒」（decided 为真）：说为什么，不说「还没决定」', async () => {
    useEnvStore.setState({ env: envDecided(true) })
    await render(<ErrorBlock error={error} traceback="" code="script_error" workdirHint={hint} />)
    expect(text()).toContain(runtimeSentence())
    expect(text()).not.toContain(en('workdirChooseSuggest'))
    expect(text()).toContain(en('workdirChooseButton'))
  })

  it('还没决定过（decided 为假）：保留原来那句', async () => {
    useEnvStore.setState({ env: envDecided(false) })
    await render(<ErrorBlock error={error} traceback="" code="script_error" workdirHint={hint} />)
    expect(text()).toContain(en('workdirChooseSuggest'))
    expect(text()).not.toContain(runtimeSentence())
  })

  it('首开的确认载荷（没有 source）即使 decided 为真也不换句子', async () => {
    useEnvStore.setState({ env: envDecided(true) })
    const first = { ...hint, source: undefined } as WorkdirConfirmation
    await render(<ErrorBlock error={error} traceback="" code="script_error" workdirHint={first} />)
    expect(text()).toContain(en('workdirChooseSuggest'))
  })
})

describe('素材库试运行的这一行', () => {
  const run = (workdir_hint?: WorkdirConfirmation): ScriptRunState =>
    ({
      phase: 'failed',
      error: { code: 'script_probe_failed', message: 'x', ...(workdir_hint ? { workdir_hint } : {}) },
    }) as never

  it('试运行失败带 hint → 显示入口', async () => {
    await render(<WorkdirHintRecovery run={run(hint)} />)
    expect(text()).toContain(en('workdirChooseButton'))
  })

  it('没有 hint / 没有运行 → 不显示', async () => {
    await render(<WorkdirHintRecovery run={run()} />)
    expect(text()).not.toContain(en('workdirChooseButton'))
  })
})

describe('渲染响应 → EngineError', () => {
  it('错误体里的 workdir_hint 原样挂在 EngineError 上，码不变', async () => {
    const { engineRender, EngineError } = await import('@/lib/api')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({ ok: false, code: 'script_error', error: 'boom', workdir_hint: hint }),
          { status: 500, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )
    try {
      await engineRender('fig.py', [])
      expect.unreachable()
    } catch (e) {
      expect(e).toBeInstanceOf(EngineError)
      expect((e as InstanceType<typeof EngineError>).code).toBe('script_error')
      expect((e as InstanceType<typeof EngineError>).workdirHint).toEqual(hint)
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
