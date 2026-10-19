/**
 * onboardingStore（ADR 0040；三步原生教程 ADR 0136）：状态机、持久化、迁移、提示记录、adapter。
 *
 * 守的是「坏数据回安全默认、升版本不抹历史也不把老用户拽回教程、embedded 能关掉持久化、
 * 『下一步』不能替用户声称完成」这几件靠读代码证明不了的事。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { clampLegendOffset, TITLE_SIZE } from '@/lib/onboarding/demoFigure'
import { REAL_STEP_IDS, STEP_IDS } from '@/lib/onboarding/stepIds'
import {
  configureOnboardingPersistence,
  DEMO_EDITS_EMPTY,
  hintSeen,
  migrateDemo,
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
  useOnboardingStore.setState({ surface: 'none' })
})

describe('状态机', () => {
  it('三步 + 完成页；真实步骤不含完成页', () => {
    expect(STEP_IDS).toEqual(['demo_import', 'demo_move_legend', 'demo_change_title', 'done'])
    expect(REAL_STEP_IDS).toEqual(['demo_import', 'demo_move_legend', 'demo_change_title'])
  })

  it('start → active、第一步、清进度与演示改动；不清提示；不绑任何项目', () => {
    s().markHintSeen('multi_select')
    s().start()
    s().markStep('demo_import')
    s().setDemo({ legendOffset: [3, -4], titleSizePt: 11 })
    s().goTo('demo_change_title')
    s().start()
    expect(s().status).toBe('active')
    expect(s().currentStep).toBe('demo_import')
    expect(s().completedSteps).toEqual([])
    expect(s().demo).toEqual(DEMO_EDITS_EMPTY)
    expect(s().tutorialProjectId).toBeNull()
    expect(s().tutorialDocumentId).toBeNull()
    expect(s().startedAt).toBeTypeOf('number')
    expect(hintSeen('multi_select')).toBe(true)
  })

  it('next 只在这一步做到了之后前进；完成页之后没有下一步', () => {
    s().start()
    s().next()
    expect(s().currentStep).toBe('demo_import')
    s().markStep('demo_import')
    s().next()
    expect(s().currentStep).toBe('demo_move_legend')
    s().next()
    expect(s().currentStep).toBe('demo_move_legend')
    s().markStep('demo_move_legend')
    s().next()
    s().markStep('demo_change_title')
    s().next()
    expect(s().currentStep).toBe('done')
    s().markStep('done')
    s().next()
    expect(s().currentStep).toBe('done')
  })

  it('back 只挪指针：不撤完成、不清演示改动；第一步 back 是 no-op', () => {
    s().start()
    s().back()
    expect(s().currentStep).toBe('demo_import')
    s().markStep('demo_import')
    s().markStep('demo_import')
    s().next()
    s().setDemo({ legendOffset: [0, -30] })
    s().markStep('demo_move_legend')
    s().back()
    expect(s().currentStep).toBe('demo_import')
    expect(s().completedSteps).toEqual(['demo_import', 'demo_move_legend'])
    expect(s().demo.legendOffset).toEqual([0, -30])
    // 回来时「下一步」照样亮（做过的事仍然做过）
    s().next()
    expect(s().currentStep).toBe('demo_move_legend')
  })

  it('openTutorial：没开始 / 终态 → 从头；进行中 / 暂停 → 接着那一步（暂停的恢复成进行中）', () => {
    expect(s().openTutorial()).toBe('started')
    expect(s().surface).toBe('tutorial')
    s().markStep('demo_import')
    s().next()
    s().closeSurface()
    expect(s().status).toBe('active') // 收起不改状态
    expect(s().openTutorial()).toBe('resumed')
    expect(s().currentStep).toBe('demo_move_legend')
    useOnboardingStore.setState({ status: 'paused', pausedBy: 'user' })
    expect(s().openTutorial()).toBe('resumed')
    expect(s().status).toBe('active')
    expect(s().pausedBy).toBeNull()
    s().skip()
    expect(s().openTutorial()).toBe('started')
    expect(s().currentStep).toBe('demo_import')
  })

  it('openWelcome 记下欢迎页出现过（只第一次写时间），不改教程状态', () => {
    s().openWelcome()
    expect(s().surface).toBe('welcome')
    const at = s().welcomeSeenAt
    expect(at).toBeTypeOf('number')
    expect(stored().welcomeSeenAt).toBe(at)
    s().openWelcome()
    expect(s().welcomeSeenAt).toBe(at)
    expect(s().status).toBe('not_started')
  })

  it('skip / complete 是终态并收起教程层；跳过不记完成时间', () => {
    s().openTutorial()
    s().skip()
    expect(s().status).toBe('skipped')
    expect(s().surface).toBe('none')
    expect(s().currentStep).toBeNull()
    expect(s().completedAt).toBeNull()
    s().openTutorial()
    s().complete()
    expect(s().status).toBe('completed')
    expect(s().surface).toBe('none')
    expect(s().completedSteps).toEqual([...STEP_IDS])
    expect(s().completedAt).toBeTypeOf('number')
    s().skip() // 终态不再变
    expect(s().status).toBe('completed')
  })

  it('markStep 从 v2 留下的跳过账里移出（做过就不是跳过）', () => {
    useOnboardingStore.setState({ status: 'active', currentStep: 'demo_import', skippedSteps: ['demo_import'] })
    s().markStep('demo_import')
    expect(s().skippedSteps).toEqual([])
    expect(s().completedSteps).toEqual(['demo_import'])
  })

  it('resetOnboarding 清状态、提示、演示改动，删掉那格存储；resetHints 只清提示', () => {
    s().start()
    s().setDemo({ titleSizePt: 12 })
    s().markHintSeen('panel_editable')
    s().resetHints()
    expect(s().hintSeen).toEqual({})
    expect(s().status).toBe('active')
    s().markHintSeen('panel_editable')
    s().resetOnboarding()
    expect(s().status).toBe('not_started')
    expect(s().hintSeen).toEqual({})
    expect(s().demo).toEqual(DEMO_EDITS_EMPTY)
    expect(s().surface).toBe('none')
    expect(localStorage.getItem(ONBOARDING_KEY)).toBeNull()
  })
})

describe('持久化与迁移', () => {
  it('写进本机的是白名单字段，没有 DOM / 文案 / 路径；surface 不落盘', () => {
    s().openTutorial()
    s().setDemo({ legendOffset: [1.5, -20], titleSizePt: 10.5 })
    const raw = stored()
    expect(Object.keys(raw).sort()).toEqual(
      [
        'schemaVersion',
        'flowVersion',
        'status',
        'currentStep',
        'completedSteps',
        'skippedSteps',
        'hintSeen',
        'startedAt',
        'completedAt',
        'tutorialProjectId',
        'tutorialDocumentId',
        'pausedBy',
        'welcomeSeenAt',
        'demo',
      ].sort(),
    )
    expect(raw.schemaVersion).toBe(ONBOARDING_SCHEMA_VERSION)
    expect(raw.flowVersion).toBe(ONBOARDING_FLOW_VERSION)
    expect(raw.demo).toEqual({ legendOffset: [1.5, -20], titleSizePt: 10.5 })
  })

  it('重启之后演示改动按模型坐标原样回来（不是 DOM 坐标）', () => {
    s().start()
    s().setDemo({ legendOffset: [0, -25.5], titleSizePt: 11 })
    configureOnboardingPersistence({
      read: () => localStorage.getItem(ONBOARDING_KEY),
      write: (raw) => localStorage.setItem(ONBOARDING_KEY, raw),
      remove: () => localStorage.removeItem(ONBOARDING_KEY),
    })
    expect(s().demo).toEqual({ legendOffset: [0, -25.5], titleSizePt: 11 })
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

  it('持久化的标题字号只认控件真实区间（TITLE_SIZE）：区间外丢掉，区间内吸附 0.5pt', () => {
    // 12 / 100 曾被宽松范围收下，第三步会把它当「进入时字号」而永远调不大
    for (const bad of [TITLE_SIZE.max + 1, 12, 100, TITLE_SIZE.min - 0.5, 1]) {
      expect(migrateDemo({ titleSizePt: bad }).titleSizePt, String(bad)).toBeNull()
    }
    expect(migrateDemo({ titleSizePt: TITLE_SIZE.max }).titleSizePt).toBe(TITLE_SIZE.max)
    expect(migrateDemo({ titleSizePt: TITLE_SIZE.min }).titleSizePt).toBe(TITLE_SIZE.min)
    expect(migrateDemo({ titleSizePt: 9.3 }).titleSizePt).toBe(9.5)
  })

  it('持久化的图例位移夹进拖动控件的边界（clampLegendOffset）：图例不会被画到指针够不着的地方', () => {
    const out = migrateDemo({ legendOffset: [400, 400] }).legendOffset
    expect(out).toEqual(clampLegendOffset([400, 400]))
    expect(out).not.toEqual([400, 400])
    expect(migrateDemo({ legendOffset: [-400, -400] }).legendOffset).toEqual(clampLegendOffset([-400, -400]))
    expect(migrateDemo({ legendOffset: [0, -20] }).legendOffset).toEqual([0, -20])
  })

  it('演示改动逐项校验：NaN / Infinity / 字符串 / 离谱的数一律丢掉，好的那项保住', () => {
    expect(migrateDemo({ legendOffset: [1, 2], titleSizePt: 11 })).toEqual({ legendOffset: [1, 2], titleSizePt: 11 })
    expect(migrateDemo({ legendOffset: [Number.NaN, 2], titleSizePt: 11 })).toEqual({ legendOffset: null, titleSizePt: 11 })
    expect(migrateDemo({ legendOffset: [1, 2, 3], titleSizePt: Number.POSITIVE_INFINITY })).toEqual(DEMO_EDITS_EMPTY)
    expect(migrateDemo({ legendOffset: ['1', 2], titleSizePt: -3 })).toEqual(DEMO_EDITS_EMPTY)
    expect(migrateDemo({ legendOffset: [1e9, 0], titleSizePt: 1e9 })).toEqual(DEMO_EDITS_EMPTY)
    expect(migrateDemo('x')).toEqual(DEMO_EDITS_EMPTY)
    const m = migratePersisted({
      schemaVersion: 1,
      flowVersion: ONBOARDING_FLOW_VERSION,
      status: 'active',
      currentStep: 'demo_change_title',
      completedSteps: ['demo_import', 'demo_move_legend'],
      demo: { legendOffset: [0, -20], titleSizePt: 'big' },
    })
    expect(m.demo).toEqual({ legendOffset: [0, -20], titleSizePt: null })
    expect(m.currentStep).toBe('demo_change_title')
  })

  it('逐字段校验：坏的那几个字段丢掉，能保住的进度保住', () => {
    const m = migratePersisted({
      schemaVersion: 1,
      flowVersion: ONBOARDING_FLOW_VERSION,
      status: 'active',
      currentStep: 'no_such_step',
      completedSteps: ['welcome', 'bogus', 42, 'demo_import'],
      hintSeen: { multi_select: 123, nope: 1, problem_found: 'x' },
      startedAt: 'yesterday',
      welcomeSeenAt: 'never',
      tutorialProjectId: 7,
      tutorialDocumentId: 'tavotto-tutorial',
    })
    expect(m.status).toBe('active')
    expect(m.completedSteps).toEqual(['demo_import'])
    // 记的步骤不存在 → 第一个未完成的
    expect(m.currentStep).toBe('demo_move_legend')
    expect(m.hintSeen).toEqual({ multi_select: 123 })
    expect(m.startedAt).toBeNull()
    expect(m.welcomeSeenAt).toBeNull()
    expect(m.tutorialProjectId).toBeNull()
    expect(m.tutorialDocumentId).toBe('tavotto-tutorial')
  })

  it('v2 → v3：八步教程进行到一半的用户落到新第一步、记成暂停（不在启动时被拽进新教程），旧步骤不算新步骤的完成', () => {
    for (const status of ['active', 'paused'] as const) {
      const m = migratePersisted({
        schemaVersion: 1,
        flowVersion: 2,
        status,
        pausedBy: status === 'paused' ? 'system' : null,
        currentStep: 'export_canvas',
        completedSteps: ['open_fast_edit', 'select_text', 'change_typography'],
        skippedSteps: ['select_text'],
        tutorialProjectId: 'p_tut',
        tutorialDocumentId: 'tavotto-tutorial',
        demo: { legendOffset: [5, 5], titleSizePt: 12 },
      })
      expect(m.status).toBe('paused')
      expect(m.pausedBy).toBe(status === 'paused' ? 'system' : 'user')
      expect(m.currentStep).toBe('demo_import')
      expect(m.completedSteps).toEqual([])
      expect(m.skippedSteps).toEqual([])
      // 上一版的演示改动不属于这一版的图
      expect(m.demo).toEqual(DEMO_EDITS_EMPTY)
      expect(m.flowVersion).toBe(ONBOARDING_FLOW_VERSION)
    }
  })

  it('v2 → v3：已完成 / 已跳过的用户原样（不被重新打扰），完成时间保留', () => {
    for (const status of ['completed', 'skipped'] as const) {
      const m = migratePersisted({
        schemaVersion: 1,
        flowVersion: 2,
        status,
        currentStep: null,
        completedSteps: ['open_fast_edit', 'select_text', 'done'],
        skippedSteps: ['select_text'],
        completedAt: status === 'completed' ? 9 : null,
      })
      expect(m.status).toBe(status)
      expect(m.currentStep).toBeNull()
      expect(m.completedAt).toBe(status === 'completed' ? 9 : null)
      // 跳过过的旧步骤不会被当成任何新步骤的完成；done 是两版共有的完成页 id
      expect(m.completedSteps).toEqual(['done'])
    }
  })

  it('更早的 welcome 步骤：停在它上面的进行中用户同样落到新的第一步（并按升版记成暂停）', () => {
    const m = migratePersisted({
      schemaVersion: 1,
      flowVersion: 1,
      status: 'active',
      currentStep: 'welcome',
      completedSteps: [],
    })
    expect(m.currentStep).toBe('demo_import')
    expect(m.status).toBe('paused')
  })

  it('记的步骤越过了没完成的步骤（手改 / 半截写入）：回到第一个未完成的，往回翻的位置照常保留', () => {
    const base = { schemaVersion: 1, flowVersion: ONBOARDING_FLOW_VERSION, status: 'active' }
    expect(migratePersisted({ ...base, currentStep: 'done', completedSteps: [] }).currentStep).toBe('demo_import')
    expect(
      migratePersisted({ ...base, currentStep: 'done', completedSteps: ['demo_import', 'demo_move_legend'] }).currentStep,
    ).toBe('demo_change_title')
    // 合法：三步都做完停在完成页；做完前两步又翻回第一步
    expect(
      migratePersisted({ ...base, currentStep: 'done', completedSteps: ['demo_import', 'demo_move_legend', 'demo_change_title'] })
        .currentStep,
    ).toBe('done')
    expect(
      migratePersisted({ ...base, currentStep: 'demo_import', completedSteps: ['demo_import', 'demo_move_legend'] }).currentStep,
    ).toBe('demo_import')
  })

  it('跳着完成的历史（手改 / 半截写入）在第一个缺口处截断：后面那一步不能被「下一步」直接绕过', () => {
    const base = { schemaVersion: 1, flowVersion: ONBOARDING_FLOW_VERSION, status: 'active' }
    const m = migratePersisted({ ...base, currentStep: 'demo_import', completedSteps: ['demo_move_legend'] })
    expect(m.completedSteps).toEqual([])
    expect(m.currentStep).toBe('demo_import')
    const m2 = migratePersisted({ ...base, currentStep: 'demo_change_title', completedSteps: ['demo_import', 'demo_change_title'] })
    expect(m2.completedSteps).toEqual(['demo_import'])
    expect(m2.currentStep).toBe('demo_move_legend')
    // 连续的历史原样
    const m3 = migratePersisted({ ...base, currentStep: 'demo_change_title', completedSteps: ['demo_move_legend', 'demo_import'] })
    expect(m3.completedSteps).toEqual(['demo_import', 'demo_move_legend'])
  })

  it('同一版里的进行中用户：状态与步骤原样（重启后接着来）', () => {
    const m = migratePersisted({
      schemaVersion: 1,
      flowVersion: ONBOARDING_FLOW_VERSION,
      status: 'active',
      currentStep: 'demo_change_title',
      completedSteps: ['demo_import', 'demo_move_legend'],
    })
    expect(m.status).toBe('active')
    expect(m.currentStep).toBe('demo_change_title')
  })

  it('embedded：persistence 为 null 时纯内存，localStorage 一个字节不写', () => {
    configureOnboardingPersistence(null)
    s().start()
    s().setDemo({ titleSizePt: 10 })
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
    s().start()
    s().setDemo({ titleSizePt: 10 })
    expect(JSON.parse(blob!).demo.titleSizePt).toBe(10)
    expect(localStorage.getItem(ONBOARDING_KEY)).toBeNull()
    s().resetOnboarding()
    expect(blob).toBeNull()
  })
})
