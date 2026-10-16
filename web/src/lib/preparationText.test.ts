import { describe, expect, it } from 'vitest'
import { prepView, type PrepContext } from '@/lib/preparationText'
import type { PrepEntry } from '@/store/projectPreparationStore'
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
