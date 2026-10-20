import { describe, expect, it } from 'vitest'
import { prepRowKey, prepView, type PrepContext } from '@/lib/preparationText'
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
