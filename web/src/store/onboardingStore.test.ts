/**
 * onboardingStore（ADR 0040）：状态机、持久化、迁移、提示记录、adapter。
 *
 * 守的是「坏数据回安全默认、升版本不抹历史、embedded 能关掉持久化」这三件
 * 靠读代码证明不了的事。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { STEP_IDS } from '@/lib/onboarding/stepIds'
import {
  configureOnboardingPersistence,
  hintSeen,
  migratePersisted,
  ONBOARDING_DEFAULTS,
  ONBOARDING_FLOW_VERSION,
  ONBOARDING_KEY,
  ONBOARDING_SCHEMA_VERSION,
  useOnboardingStore,
  type OnboardingPersistence,
} from './onboardingStore'

const s = () => useOnboardingStore.getState()
const stored = () => JSON.parse(localStorage.getItem(ONBOARDING_KEY) ?? 'null')

beforeEach(() => {
  localStorage.clear()
  // 回到 localStorage 后端并按空存储重读
  configureOnboardingPersistence({
    read: () => localStorage.getItem(ONBOARDING_KEY),
    write: (raw) => localStorage.setItem(ONBOARDING_KEY, raw),
    remove: () => localStorage.removeItem(ONBOARDING_KEY),
  })
})

describe('状态机', () => {
  it('start → active、第一步、绑定项目与文档；再 start 清进度但不清提示', () => {
    s().markHintSeen('multi_select')
    s().start({ projectId: 'p1', documentId: 'tavotto-tutorial' })
    expect(s().status).toBe('active')
    expect(s().currentStep).toBe(STEP_IDS[0])
    expect(s().tutorialProjectId).toBe('p1')
    expect(s().tutorialDocumentId).toBe('tavotto-tutorial')
    expect(s().startedAt).toBeTypeOf('number')
    s().markStep('welcome')
    s().goTo('open_fast_edit')
    s().start({ projectId: 'p1', documentId: 'tavotto-tutorial' })
    expect(s().completedSteps).toEqual([])
    expect(s().currentStep).toBe('welcome')
    expect(hintSeen('multi_select')).toBe(true)
  })

  it('pause / resume 记暂停来源；只有 active 能暂停、只有 paused 能继续', () => {
    s().pause('user')
    expect(s().status).toBe('not_started')
    s().start({ projectId: 'p1', documentId: 'd' })
    s().pause('system')
    expect(s().status).toBe('paused')
    expect(s().pausedBy).toBe('system')
    s().resume()
    expect(s().status).toBe('active')
    expect(s().pausedBy).toBeNull()
    s().resume() // 幂等
    expect(s().status).toBe('active')
  })

  it('关掉 coachmark 是 paused，不是 completed', () => {
    s().start({ projectId: 'p1', documentId: 'd' })
    s().pause('user')
    expect(s().status).toBe('paused')
    expect(s().completedAt).toBeNull()
    expect(stored().status).toBe('paused')
  })

  it('skip / complete 是终态；complete 记全部步骤与时间', () => {
    s().start({ projectId: 'p1', documentId: 'd' })
    s().skip()
    expect(s().status).toBe('skipped')
    expect(s().currentStep).toBeNull()
    s().start({ projectId: 'p1', documentId: 'd' })
    s().complete()
    expect(s().status).toBe('completed')
    expect(s().completedSteps).toEqual([...STEP_IDS])
    expect(s().completedAt).toBeTypeOf('number')
    s().pause('user') // 终态不再变
    expect(s().status).toBe('completed')
  })

  it('markStep 去重；back 只挪指针不撤完成；第一步 back 是 no-op', () => {
    s().start({ projectId: 'p1', documentId: 'd' })
    s().back()
    expect(s().currentStep).toBe('welcome')
    s().markStep('welcome')
    s().markStep('welcome')
    s().goTo('select_text')
    s().back()
    expect(s().currentStep).toBe('open_fast_edit')
    expect(s().completedSteps).toEqual(['welcome'])
  })

  it('resetOnboarding 清状态与提示、删掉那格存储；resetHints 只清提示', () => {
    s().start({ projectId: 'p1', documentId: 'd' })
    s().markHintSeen('panel_editable')
    s().resetHints()
    expect(s().hintSeen).toEqual({})
    expect(s().status).toBe('active')
    s().markHintSeen('panel_editable')
    s().resetOnboarding()
    expect(s().status).toBe('not_started')
    expect(s().hintSeen).toEqual({})
    expect(localStorage.getItem(ONBOARDING_KEY)).toBeNull()
  })
})

describe('持久化与迁移', () => {
  it('写进本机的是白名单字段，没有 DOM / 文案 / 路径', () => {
    s().start({ projectId: 'p1', documentId: 'd' })
    const raw = stored()
    expect(Object.keys(raw).sort()).toEqual(
      [
        'schemaVersion',
        'flowVersion',
        'status',
        'currentStep',
        'completedSteps',
        'hintSeen',
        'startedAt',
        'completedAt',
        'tutorialProjectId',
        'tutorialDocumentId',
        'pausedBy',
      ].sort(),
    )
    expect(raw.schemaVersion).toBe(ONBOARDING_SCHEMA_VERSION)
    expect(raw.flowVersion).toBe(ONBOARDING_FLOW_VERSION)
  })

  it('坏 blob / 非对象 / schema 不认识 → 安全默认，不抛', () => {
    expect(migratePersisted('garbage')).toEqual(ONBOARDING_DEFAULTS)
    expect(migratePersisted([1, 2])).toEqual(ONBOARDING_DEFAULTS)
    expect(migratePersisted({ schemaVersion: 99, status: 'active' })).toEqual(ONBOARDING_DEFAULTS)
    localStorage.setItem(ONBOARDING_KEY, '{not json')
    configureOnboardingPersistence({
      read: () => localStorage.getItem(ONBOARDING_KEY),
      write: () => {},
      remove: () => {},
    })
    expect(s().status).toBe('not_started')
  })

  it('逐字段校验：坏的那几个字段丢掉，能保住的进度保住', () => {
    const m = migratePersisted({
      schemaVersion: 1,
      flowVersion: ONBOARDING_FLOW_VERSION,
      status: 'active',
      currentStep: 'no_such_step',
      completedSteps: ['welcome', 'bogus', 42, 'open_fast_edit'],
      hintSeen: { multi_select: 123, nope: 1, problem_found: 'x' },
      startedAt: 'yesterday',
      tutorialProjectId: 7,
      tutorialDocumentId: 'tavotto-tutorial',
    })
    expect(m.status).toBe('active')
    expect(m.completedSteps).toEqual(['welcome', 'open_fast_edit'])
    // 记的步骤不存在 → 第一个未完成的
    expect(m.currentStep).toBe('select_text')
    expect(m.hintSeen).toEqual({ multi_select: 123 })
    expect(m.startedAt).toBeNull()
    expect(m.tutorialProjectId).toBeNull()
    expect(m.tutorialDocumentId).toBe('tavotto-tutorial')
  })

  it('flowVersion 升级：进行中的回到第一个未完成步骤、历史不抹；已完成的不被打扰', () => {
    const active = migratePersisted({
      schemaVersion: 1,
      flowVersion: ONBOARDING_FLOW_VERSION - 1,
      status: 'paused',
      pausedBy: 'user',
      currentStep: 'export_canvas',
      completedSteps: ['welcome', 'open_fast_edit'],
    })
    expect(active.status).toBe('paused')
    expect(active.pausedBy).toBe('user')
    expect(active.currentStep).toBe('select_text')
    expect(active.completedSteps).toEqual(['welcome', 'open_fast_edit'])
    expect(active.flowVersion).toBe(ONBOARDING_FLOW_VERSION)

    const done = migratePersisted({
      schemaVersion: 1,
      flowVersion: ONBOARDING_FLOW_VERSION - 1,
      status: 'completed',
      completedSteps: [...STEP_IDS],
      completedAt: 5,
    })
    expect(done.status).toBe('completed')
    expect(done.completedSteps).toEqual([...STEP_IDS])
    expect(done.completedAt).toBe(5)
    expect(done.currentStep).toBeNull()
  })

  it('embedded：persistence 为 null 时纯内存，localStorage 一个字节不写', () => {
    configureOnboardingPersistence(null)
    s().start({ projectId: 'p1', documentId: 'd' })
    s().markHintSeen('fast_edit_entered')
    expect(s().status).toBe('active')
    expect(localStorage.getItem(ONBOARDING_KEY)).toBeNull()
  })

  it('宿主 adapter：读写都经它', () => {
    let blob: string | null = null
    const adapter: OnboardingPersistence = {
      read: () => blob,
      write: (raw) => {
        blob = raw
      },
      remove: () => {
        blob = null
      },
    }
    configureOnboardingPersistence(adapter)
    s().start({ projectId: 'host', documentId: 'd' })
    expect(JSON.parse(blob!).tutorialProjectId).toBe('host')
    expect(localStorage.getItem(ONBOARDING_KEY)).toBeNull()
    s().resetOnboarding()
    expect(blob).toBeNull()
  })
})
