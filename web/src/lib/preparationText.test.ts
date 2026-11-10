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
  placeIn: null,
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

  it('revert_ai_edit：一句话说是 AI 改过之后坏的 + 主按钮「回滚 AI 的修改」；AI 次按钮仍在（TVD-H1M5）', () => {
    const v = view(failed({ kind: 'revert_ai_edit', session: 'ai-s1' }))
    expect(v.state).toBe('failed')
    expect(v.sentence.key).toBe('aiEditBroke')
    expect(v.primary).toEqual({ kind: 'revert_ai_edit', session: 'ai-s1' })
    expect(v.aiFix).toBe(true)
    expect(t('prep.line.aiEditBroke', { ns: 'workspace' })).toBe('AI 改过之后运行出错了')
    expect(t('prep.btn.revertAiEdit', { ns: 'workspace' })).toBe('回滚 AI 的修改')
  })

  it('feedback：一句话 + 主按钮「发送」；后端判定 AI 修不了，不再摆 AI 次按钮', () => {
    const v = view(failed({ kind: 'feedback', tried: ['switch_native'] }))
    expect(v.state).toBe('feedback')
    expect(v.sentence.key).toBe('feedback')
    expect(v.primary).toEqual({ kind: 'feedback' })
    expect(v.aiFix).toBe(false)
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
      failed({ kind: 'change_python', from: '3.9.1', to: null, env: null }, {
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

  it('只复制现成图片的脚本（TVD-537W）：一句话指向输出的图片，不摆 AI 修复', () => {
    const copied = (remedy: unknown) =>
      view(
        failed(remedy, {
          phase: 'partial',
          outcome: { kind: 'execution_finished_no_figure', code: 'no_figures_captured' },
          no_figure_hint: { kind: 'copied_images', in_project: true, path: 'Figure3.pdf' },
        }),
      )
    // 后端此时不给 remedy；就算老后端给了 ai_fix，这张卡也不摆 AI
    for (const remedy of [null, { kind: 'ai_fix' }]) {
      const v = copied(remedy)
      expect(v.state).toBe('no_figure_copied')
      expect(v.sentence.key).toBe('noFigureCopied')
      expect(v.primary).toEqual({ kind: 'open_assets' })
      expect(v.aiFix).toBe(false)
    }
    // 没有这条提示的「没出图」照旧（AI 主按钮）
    const plain = view(
      failed({ kind: 'ai_fix' }, { phase: 'partial', outcome: { kind: 'execution_finished_no_figure', code: 'no_figures_captured' } }),
    )
    expect(plain.state).toBe('no_figure')
    expect(plain.primary).toEqual({ kind: 'ai_fix' })
    expect(t('prep.line.noFigureCopied', { ns: 'workspace' })).not.toMatch(/[。.]$/)
  })

  describe('装不了的缺包不再以 AI 为主按钮（ADR 0148 修订 2026-11-01）', () => {
    const missing = (remedy: unknown) =>
      failed(remedy, { outcome: { kind: 'failed', code: 'missing_dependency' } })

    it('choose_environment：主按钮「选择运行环境」，AI 退为次入口', () => {
      const v = view(missing({ kind: 'choose_environment' }))
      expect(v.state).toBe('failed')
      expect(v.primary).toEqual({ kind: 'pick_environment' })
      expect(v.aiFix).toBe(true)
    })

    it('没连 AI 也一样：主按钮不是「连接 AI」', () => {
      const v = view(missing({ kind: 'choose_environment' }))
      expect(v.primary?.kind).not.toBe('ai_fix')
    })

    it('其余确实适合 AI 的失败不变（脚本自身报错仍是 AI 主按钮）', () => {
      expect(view(failed({ kind: 'ai_fix' })).primary).toEqual({ kind: 'ai_fix' })
      expect(view(failed({ kind: 'connect_ai' })).primary).toEqual({ kind: 'ai_fix' })
    })
  })

  describe('Python 版本不合：有满足的现成环境就一键改用，没有才进选择器', () => {
    const versionFail = (remedy: unknown) =>
      failed(remedy, { outcome: { kind: 'failed', code: 'project_env_unsupported_python' } })
    const env = { id: 'e1', generation: 'g1', name: 'sci', version: '3.12.4' }

    it('带 env：主按钮 switch_python（只是按钮，不自动切），AI 次入口仍在', () => {
      const v = view(versionFail({ kind: 'change_python', from: '3.9.1', to: '3.12', env }))
      expect(v.primary).toEqual({ kind: 'switch_python', env })
      expect(v.aiFix).toBe(true)
    })

    it('没有候选：主按钮是选择运行环境', () => {
      const v = view(versionFail({ kind: 'change_python', from: '3.9.1', to: '3.12', env: null }))
      expect(v.primary).toEqual({ kind: 'pick_environment' })
    })

    it('没有目标版本（to: null，如写不了缓存之外的无建议情形）不改主按钮', () => {
      const v = view(versionFail({ kind: 'change_python', from: '3.14', to: null, env: null }))
      expect(v.primary).toMatchObject({ kind: 'action', action: 'run' })
    })
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
      if (vec.remedy.kind === 'switch_native' || vec.remedy.kind === 'feedback') expect(v.aiFix).toBe(false)
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

describe('失败出口不是死胡同（2026-11-10）', () => {
  const action = (kind: string) => ({ id: `a-${kind}`, kind, config_revision: 1, impact: {} })
  const base = (over: Record<string, unknown> = {}) =>
    ({
      phase: 'action_required',
      outcome: { kind: 'failed', code: 'script_error' },
      target: { kind: 'script', script: 'a.py' },
      checks: [],
      requirements: [],
      actions: [action('run'), action('recheck')],
      provider: { plan_id: 'p', attempt_id: 'att-1', attempts: 1, dependency: null },
      ...over,
    }) as unknown as PreparationReport
  const view = (r: PreparationReport, c: Partial<PrepContext> = {}) =>
    prepView({ ...lost, connection: 'ok', report: r } as PrepEntry, { ...ctx, ...c })
  const send = { canSendReport: true }

  describe('1. 没连 AI（connect_ai）：主按钮是「发给我们看看」，AI 降为次按钮', () => {
    it('发送入口开着：主按钮 feedback / sendUs，AI 次按钮在，remedy 仍是 connect_ai', () => {
      const v = view(base({ remedy: { kind: 'connect_ai' } }), send)
      expect(v.state).toBe('failed')
      expect(v.primary).toEqual({ kind: 'feedback', label: 'sendUs' })
      expect(v.aiFix).toBe(true)
      expect(v.remedy?.kind).toBe('connect_ai')
      expect(t('prep.btn.sendUs', { ns: 'workspace' })).toBe('发给我们看看')
    })
    it('发送入口关着（或没传）：维持原样，主按钮仍是 AI', () => {
      expect(view(base({ remedy: { kind: 'connect_ai' } }), { canSendReport: false }).primary).toEqual({ kind: 'ai_fix' })
      expect(view(base({ remedy: { kind: 'connect_ai' } })).primary).toEqual({ kind: 'ai_fix' })
    })
    it('已连 AI（ai_fix）不变：主按钮仍是 AI', () => {
      const v = view(base({ remedy: { kind: 'ai_fix' } }), send)
      expect(v.primary).toEqual({ kind: 'ai_fix' })
      expect(v.aiFix).toBe(false)
    })
  })

  describe('2. blocked 把后端给的原因当那一句话', () => {
    const blocked = (code: string, reasons: string[] = []) =>
      base({
        outcome: { kind: 'blocked', code },
        checks: [{ id: 'dependencies', status: 'blocked', code, detail: { reasons } }],
        actions: [action('recheck')],
      })
    it('冲突类原因：句子是 dependencyBlocked_*，发送入口开着主按钮退到发给我们看看，不是必败的重新检查', () => {
      const v = view(blocked('dependency_blocked', ['dependency_conflict']), send)
      expect(v.state).toBe('blocked')
      expect(v.sentence.key).toBe('errorText')
      expect(v.sentence.values.textKey).toBe('engine.dependencyBlocked_dependency_conflict')
      expect(v.primary).toEqual({ kind: 'feedback', label: 'sendUs' })
      expect(t(String(v.sentence.values.textKey), { ns: 'errors' })).toContain('依赖声明')
    })
    it('发送入口关着：仍给重新检查（维持原样），但句子已是原因', () => {
      const v = view(blocked('dependency_blocked', ['dependency_conflict']))
      expect(v.sentence.key).toBe('errorText')
      expect(v.primary).toMatchObject({ kind: 'action', action: 'recheck' })
    })
    it('有出路的原因：声明里有装不了的几行 -> 换用我的 Python；目标环境检查不了 -> 打开设置', () => {
      expect(view(blocked('dependency_blocked', ['dependency_declaration_unsupported']), send).primary).toEqual({ kind: 'pick_environment' })
      expect(view(blocked('dependency_blocked', ['dependency_target_unavailable']), send).primary).toEqual({ kind: 'open_environment' })
    })
    it('有出路的原因不被 remedy 换成 AI 主按钮（AI 仍是次按钮）', () => {
      const v = view({ ...blocked('dependency_blocked', ['dependency_declaration_unsupported']), remedy: { kind: 'ai_fix' } } as PreparationReport, send)
      expect(v.primary).toEqual({ kind: 'pick_environment' })
      expect(v.aiFix).toBe(true)
    })
    it('原因认不出：笼统的「现在还不能运行」照旧', () => {
      const v = view(blocked('dependency_blocked', ['something_new']), send)
      expect(v.sentence.key).toBe('blocked')
    })
  })

  describe('3. 跑完没出图 + 缺数据：走与 missing_data 相同的指认出口', () => {
    const noFigure = (over: Record<string, unknown> = {}) =>
      base({
        phase: 'partial',
        outcome: { kind: 'execution_finished_no_figure', code: 'no_figures_captured' },
        requirements: [{ id: 'data', kind: 'input_location', code: 'missing_input', origin: 'last_attempt', blocking: false, payload: { script: 'a.py', requested: null, others: [] } }],
        ...over,
      })
    it('有 input_location：missing_data 卡 + 指认数据，AI 次按钮在', () => {
      const v = view(noFigure({ remedy: { kind: 'connect_ai' } }), send)
      expect(v.state).toBe('missing_data')
      expect(v.primary).toMatchObject({ kind: 'missing_input' })
      expect(v.aiFix).toBe(true)
    })
    it('没有 input_location：仍是「跑完了，没有出图」', () => {
      expect(view(noFigure({ requirements: [] })).state).toBe('no_figure')
    })
    it('只复制现成图片的判断优先（#1823 不回退）', () => {
      const v = view(noFigure({ no_figure_hint: { kind: 'copied_images', in_project: true, path: 'F.pdf' } }))
      expect(v.state).toBe('no_figure_copied')
    })
  })

  describe('4. 图名归属冲突：保留「打开接入中心」，不摆 AI', () => {
    const conflict = base({
      phase: 'partial',
      outcome: { kind: 'failed', code: 'multiple_stem_conflict' },
      remedy: { kind: 'feedback', tried: [] },
    })
    it('主按钮仍是打开接入中心，没有 AI 次按钮', () => {
      const v = view(conflict, send)
      expect(v.state).toBe('not_registered')
      expect(v.primary).toEqual({ kind: 'open_registry' })
      expect(v.aiFix).toBe(false)
    })
  })

  describe('5. 必败的「再试一次」改成发给我们看看', () => {
    const cacheDenied = (switchTo?: string) =>
      base({
        outcome: { kind: 'failed', code: 'interpreter_cache_denied' },
        result: { error: { code: 'interpreter_cache_denied', params: switchTo ? { switch: switchTo } : {} } },
        remedy: { kind: 'change_python', from: '3.9', to: null, env: null },
      })
    it('interpreter_cache_denied 后端没给 switch：发送入口开着 -> 发给我们看看', () => {
      expect(view(cacheDenied(), send).primary).toEqual({ kind: 'feedback', label: 'sendUs' })
      expect(view(cacheDenied()).primary).toMatchObject({ kind: 'action', action: 'run' })
    })
    it('给了 switch 仍是「改用自带的 Python」', () => {
      expect(view(cacheDenied('global'), send).primary).toEqual({ kind: 'use_builtin_python', scope: 'global' })
    })
    it('change_python 但没有目标版本：发给我们看看；没有入口时不动', () => {
      const r = base({ outcome: { kind: 'failed', code: 'python_out_of_range' }, remedy: { kind: 'change_python', from: '3.14', to: null, env: null } })
      expect(view(r, send).primary).toEqual({ kind: 'feedback', label: 'sendUs' })
      expect(view(r).primary).toMatchObject({ kind: 'action', action: 'run' })
    })
  })

  describe('6. 失败码自己的人话当那一句话，原始报错留在详情', () => {
    const withCode = (code: string, over: Record<string, unknown> = {}) =>
      base({
        outcome: { kind: 'failed', code },
        result: { error: { code, message: 'x', traceback: 'Traceback\nPermissionError: [Errno 13]', params: {} } },
        ...over,
      })
    it.each(['output_folder_read_only', 'script_exited', 'import_folder_unreadable'])('%s -> errorText', (code) => {
      const v = view(withCode(code))
      expect(v.state).toBe('failed')
      expect(v.sentence.key).toBe('errorText')
      expect(v.sentence.values.textKey).toBe(`backend.${code}`)
      expect(t(`backend.${code}`, { ns: 'errors', product: 'T', error: 'SystemExit: 1', folder: 'D' })).not.toMatch(/^backend\./)
    })
    it('输出文件夹没写权限：重试没用，发送入口开着主按钮是发给我们看看', () => {
      expect(view(withCode('output_folder_read_only'), send).primary).toEqual({ kind: 'feedback', label: 'sendUs' })
      expect(view(withCode('output_folder_read_only')).primary).toMatchObject({ action: 'run' })
    })
    it('Windows 有修复令牌的 import_folder_unreadable 仍是修复权限', () => {
      const v = view(withCode('import_folder_unreadable', { result: { error: { code: 'import_folder_unreadable', params: { repair_token: 'tok', folder: 'D' } } } }))
      expect(v.state).toBe('folder_unreadable')
    })
  })
})

describe('设置里固定的 Python 缺组件：列出缺哪些（QA-WIN-12）', () => {
  const pinned = (payload: Record<string, unknown>) =>
    prepView(
      {
        ...lost,
        connection: 'ok',
        report: {
          phase: 'action_required',
          outcome: { kind: 'blocked', code: 'dependency_interpreter_pinned' },
          target: { kind: 'script', script: 'a.py' },
          checks: [],
          requirements: [{ id: 'dependencies', kind: 'dependency_pinned', code: 'dependency_interpreter_pinned', payload }],
          actions: [],
        } as unknown as PreparationReport,
      } as PrepEntry,
      ctx,
    )

  it('后端给了缺的模块名：一行里带上名字', () => {
    const v = pinned({ source: 'settings', missing: ['scipy', 'seaborn'] })
    expect(v.state).toBe('deps_pinned')
    expect(v.sentence.key).toBe('envLockedMissing')
    const line = t('prep.line.envLockedMissing', { ns: 'workspace', ...v.sentence.values })
    expect(line).toContain('scipy')
    expect(line).toContain('seaborn')
  })

  it('后端没量到：保持原来那一句，不硬造', () => {
    expect(pinned({ source: 'settings' }).sentence.key).toBe('envLocked')
    expect(pinned({ source: 'settings', missing: [] }).sentence.key).toBe('envLocked')
  })
})
