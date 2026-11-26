/**
 * 改图助手「先出方案卡还是照旧改脚本」的判定（ADR 0206 PR 4）：每一条判据各用一个反例钉住——
 * 只有「开关开 + 输入框 / 历史重发 + 没附图 + 不超长 + 单图有脚本 + 不是内嵌 + 没有在途方案」同时成立才走方案路径。
 */
import { describe, expect, it } from 'vitest'
import type { PanelObject } from '@/types/document'
import { assistantUsesPlan, MAX_ASSISTANT_INSTRUCTION, type AssistantRouteInput } from './assistantPlanRoute'

const panel = (over: Partial<PanelObject> = {}): PanelObject =>
  ({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: [], ...over }) as PanelObject

const base = (over: Partial<AssistantRouteInput> = {}): AssistantRouteInput => ({
  enabled: undefined,
  source: 'composer',
  pinned: false,
  hasRefs: false,
  length: 12,
  panel: panel(),
  embedded: false,
  planBusy: false,
  ...over,
})

describe('助手走方案路径的判据', () => {
  it('全部成立（开关缺省 = 开）', () => {
    expect(assistantUsesPlan(base())).toBe(true)
    expect(assistantUsesPlan(base({ enabled: true }))).toBe(true)
    expect(assistantUsesPlan(base({ source: 'history' }))).toBe(true)
  })

  it.each([
    ['开关关着（ai.change_plan.enabled = false）', { enabled: false }],
    ['失败后的重试（钉着那一次的目标）', { pinned: true }],
    ['附了参考图（结构化调用只有文字）', { hasRefs: true }],
    ['没有指令', { length: 0 }],
    ['指令超过后端上限', { length: MAX_ASSISTANT_INSTRUCTION + 1 }],
    ['没有这张图', { panel: undefined }],
    ['位图', { panel: panel({ fileKind: 'raster' }) }],
    ['没有脚本（没有图内编辑入口）', { panel: panel({ script: undefined }) }],
    ['内嵌画布 / playground', { embedded: true }],
    ['这张图上已经有方案在生成', { planBusy: true }],
  ] as [string, Partial<AssistantRouteInput>][])('不走：%s', (_name, over) => {
    expect(assistantUsesPlan(base(over))).toBe(false)
  })

  it('刚好等于上限还走', () => {
    expect(assistantUsesPlan(base({ length: MAX_ASSISTANT_INSTRUCTION }))).toBe(true)
  })
})
