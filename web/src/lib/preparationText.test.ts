import { describe, expect, it } from 'vitest'
import { prepRowKey, prepView, withAiUnavailable, type PrepContext } from '@/lib/preparationText'
import type { Remedy } from '@/lib/fixRemedy'
import golden from '../../../tests/golden/fix_remedy_vectors.json'
import { capturedOf, finishedWithFigures, type PrepEntry } from '@/store/projectPreparationStore'
import type { PreparationReport } from '@/lib/api'
import { t } from '@/i18n'

const lost: PrepEntry = {
  key: 'k',
  target: { script: 'a.py' } as PrepEntry['target'],
  pj: null,
  report: null,
  connection: 'lost',
  restarted: false,
  rejection: null,
  failure: null,
  pending: null,
  editing: [],
  editRenders: {},
  armed: false,
  armMaybe: false,
  armMaybeBaseline: null,
  armMaybeFrom: null,
  wantsRun: false,
  allowRerun: true,
  transferFrom: null,
}
const ctx: PrepContext = { argsChanged: false, editReady: false, missingArgs: null }

describe('断线那一句（ADR 0129）', () => {
  it('其它原因的断线：照旧说「正在重连」', () => {
    const v = prepView(lost, ctx)
    expect(v.state).toBe('offline')
    expect(v.sentence.key).toBe('offline')
    expect(t('prep.line.offline', { ns: 'workspace' })).toContain('…')
  })

  it('壳已确认引擎退出：不再说「正在重连」（没人在重连），改说引擎已停止', () => {
    const v = prepView(lost, { ...ctx, engineExited: true })
    expect(v.state).toBe('offline')
    expect(v.sentence.key).toBe('engineStopped')
    expect(t('prep.line.engineStopped', { ns: 'workspace' })).not.toMatch(/重连|reconnect/i)
  })

  it('连接没断时 engineExited 不影响别的卡片', () => {
    const ok = { ...lost, connection: 'ok' as const }
    expect(prepView(ok, { ...ctx, engineExited: true }).sentence.key).toBe('checking')
  })
})

describe('部分捕获（#1134）：partial + succeeded 不是 completed', () => {
  const partial = {
    phase: 'partial',
    outcome: { kind: 'succeeded' },
    target: { kind: 'script', script: 'a.py' },
    captured: [{ stem: 'good' }],
    failed_figures: [{ stem: 'bad', code: 'preview_capture_failed' }],
    actions: [],
  } as unknown as PreparationReport
  const entry = { ...lost, connection: 'ok', report: partial } as PrepEntry

  it('标题点名失败的图并给 N/M，主按钮仍是进入编辑', () => {
    const v = prepView(entry, ctx)
    expect(v.state).toBe('completed_partial')
    expect(v.sentence.key).toBe('completedPartial')
    expect(v.sentence.values).toMatchObject({ done: 1, total: 2, names: 'bad' })
    expect(v.primary?.kind).toBe('enter_edit')
    expect(v.tone).toBe('bad')
  })

  it('全部成功仍是 completed；没出图的 partial 不算有图', () => {
    const ok = { ...partial, phase: 'completed', failed_figures: [] } as PreparationReport
    expect(prepView({ ...entry, report: ok }, ctx).state).toBe('completed')
    const noFigure = { ...partial, outcome: { kind: 'execution_finished_no_figure' } } as PreparationReport
    expect(finishedWithFigures(noFigure)).toBe(false)
    expect(capturedOf(noFigure)).toEqual([])
    expect(capturedOf(partial)).toHaveLength(1)
  })

  it('脚本库的行也如实说 N/M', () => {
    expect(prepRowKey(entry)).toEqual({ key: 'capturedPartial', values: { done: 1, total: 2 } })
  })
})

describe('智能修复（ADR 0148）：主按钮读 remedy，每张失败卡都有「使用你的 AI 来修复」', () => {
  const action = (kind: string) => ({ id: `a-${kind}`, kind, config_revision: 1, impact: {} })
  const failed = (remedy?: unknown, over: Record<string, unknown> = {}) =>
    ({
      phase: 'action_required',
      outcome: { kind: 'failed', code: 'script_error' },
      target: { kind: 'script', script: 'a.py' },
      checks: [],
      requirements: [],
      actions: [action('run'), action('recheck')],
      provider: { plan_id: 'p', attempt_id: 'att-1', attempts: 1, dependency: null },
      remedy,
      ...over,
    }) as unknown as PreparationReport
  const view = (r: PreparationReport, c: Partial<PrepContext> = {}) =>
    prepView({ ...lost, connection: 'ok', report: r } as PrepEntry, { ...ctx, ...c })

  it('ai_fix / connect_ai：主按钮就是「使用你的 AI 来修复」（不再重复摆次按钮）', () => {
    for (const kind of ['ai_fix', 'connect_ai'] as const) {
      const v = view(failed({ kind }))
      expect(v.state).toBe('failed')
      expect(v.primary).toEqual({ kind: 'ai_fix' })
      expect(v.aiFix).toBe(false)
      expect(v.remedy?.kind).toBe(kind)
    }
    expect(t('prep.btn.aiFix', { ns: 'workspace' })).toBe('使用你的 AI 来修复')
  })

  it('feedback：一句话 + 主按钮「发送」；AI 次按钮仍在（每次失败都出）', () => {
    const v = view(failed({ kind: 'feedback', tried: ['switch_native'] }))
    expect(v.state).toBe('feedback')
    expect(v.sentence.key).toBe('feedback')
    expect(v.primary).toEqual({ kind: 'feedback' })
    expect(v.aiFix).toBe(true)
    expect(t('prep.line.feedback', { ns: 'workspace' })).toBe('这个脚本暂时用不了，发给我们看看？')
  })

  it('确定的修法（装包 / 选环境 / 转写）：主按钮照旧，AI 次按钮也在', () => {
    const install = view(
      failed({ kind: 'install_packages', packages: ['scipy'], ask: false }, {
        phase: 'awaiting_confirmation',
        outcome: { kind: 'needs_dependencies', code: 'missing_dependency', reason: 'rerun_required' },
        requirements: [{ id: 'd', kind: 'dependency_authorization', origin: 'runtime_missing', code: 'missing_dependency', payload: { module: 'scipy', installable: true } }],
        actions: [action('prepare_dependencies'), action('recheck')],
      }),
    )
    expect(install.state).toBe('deps_runtime')
    expect(install.primary).toMatchObject({ kind: 'action', action: 'prepare_dependencies' })
    expect(install.aiFix).toBe(true)

    const python = view(
      failed({ kind: 'change_python', from: '3.9.1', to: null }, {
        phase: 'awaiting_confirmation',
        outcome: { kind: 'needs_dependencies', code: 'missing_dependency' },
        requirements: [{ id: 'd', kind: 'dependency_authorization', origin: 'runtime_missing', code: 'missing_dependency', payload: { module: 'mylab', installable: false } }],
        actions: [action('recheck')],
      }),
    )
    expect(python.state).toBe('deps_unknown')
    expect(python.primary).toEqual({ kind: 'pick_environment' })
    expect(python.aiFix).toBe(true)

    const convert = view(
      failed({ kind: 'convert_to_matplotlib', source: 'python' }, {
        phase: 'partial',
        outcome: { kind: 'execution_finished_no_figure', code: 'no_figures_captured_silent' },
        no_figure_hint: { kind: 'raster_script', library: 'pillow', in_project: true, path: 'o.png' },
      }),
    )
    expect(convert.state).toBe('no_figure_raster')
    expect(convert.primary).toMatchObject({ kind: 'convert' })
    expect(convert.aiFix).toBe(true)
  })

  it('switch_native 是静默回退：不摆 AI 次按钮', () => {
    expect(view(failed({ kind: 'switch_native', silent: true })).aiFix).toBe(false)
  })

  it('没有 remedy（老后端 / 认不得的形状）的失败卡也有 AI 次按钮，主按钮照旧', () => {
    for (const bad of [undefined, null, { kind: 'teleport' }]) {
      const v = view(failed(bad))
      expect(v.aiFix).toBe(true)
      expect(v.primary).toMatchObject({ kind: 'action', action: 'run' })
      expect(v.remedy).toBeNull()
    }
  })

  it('读不到数据的卡（missing_data）：主按钮仍是「指认数据」，AI 次按钮也在；教程层里没有', () => {
    const r = failed(undefined, {
      outcome: { kind: 'failed', code: 'missing_input' },
      requirements: [{ id: 'data', kind: 'input_location', code: 'missing_input', origin: 'last_attempt', blocking: false, payload: { script: 'a.py' } }],
    })
    const v = view(r)
    expect(v.state).toBe('missing_data')
    expect(v.primary).toMatchObject({ kind: 'missing_input' })
    expect(v.aiFix).toBe(true)
    expect(view(r, { tutorial: true }).aiFix).toBe(false)
  })

  it('不是失败卡就没有：ready / 运行中 / 成功', () => {
    const ready = { phase: 'ready_to_run', outcome: { kind: 'pending' }, target: { kind: 'script', script: 'a.py' }, checks: [], requirements: [], actions: [action('run')], provider: { plan_id: 'p', attempt_id: null, attempts: 0, dependency: null } } as unknown as PreparationReport
    expect(view(ready).aiFix).toBe(false)
    expect(view({ ...ready, phase: 'running', outcome: { kind: 'running' }, actions: [action('cancel')] } as PreparationReport).aiFix).toBe(false)
    expect(view({ ...ready, phase: 'completed', outcome: { kind: 'succeeded' }, captured: [] } as unknown as PreparationReport).aiFix).toBe(false)
  })

  it('教程层激活：不摆任何 AI 相关按钮，主按钮也不换', () => {
    const v = view(failed({ kind: 'ai_fix' }), { tutorial: true })
    expect(v.aiFix).toBe(false)
    expect(v.primary).toMatchObject({ kind: 'action', action: 'run' })
  })

  it('golden 里每种后端产出的 remedy 前端都能画出一张卡（不抛、有主按钮或明确静默）', () => {
    for (const vec of golden.vectors) {
      const v = view(failed(vec.remedy))
      expect(v.remedy?.kind, vec.name).toBe((vec.remedy as Remedy).kind)
      if (vec.remedy.kind === 'switch_native') expect(v.aiFix).toBe(false)
      else expect(v.aiFix || v.primary?.kind === 'ai_fix', vec.name).toBe(true)
    }
  })

  it('AI 暂时修不了：同一张卡落成「这个问题 AI 暂时还不会修」+ 发送，次按钮不再摆', () => {
    const v = withAiUnavailable(view(failed({ kind: 'ai_fix' })))
    expect(v.state).toBe('ai_unavailable')
    expect(v.sentence.key).toBe('aiUnavailable')
    expect(v.primary).toEqual({ kind: 'feedback' })
    expect(v.aiFix).toBe(false)
    expect(t('prep.line.aiUnavailable', { ns: 'workspace' })).toBe('这个问题 AI 暂时还不会修')
  })
})
