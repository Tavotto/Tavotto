import { create } from 'zustand'
import {
  ApiError,
  cancelProbe,
  probeScript,
  type CapturedFigureDescriptor,
  type ProbeError,
} from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { useRenderStore } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'

/**
 * 「运行并发现图」的状态机（Session 5 素材库普通入口）。
 *
 * 每个脚本一台小状态机；执行本体在后端（`/api/registry/probe`，同步阻塞
 * + SSE `probe.started`），这里只管四条纪律：
 *
 * - **同一脚本不能并行两个 probe**：`run()` 对 busy 态直接 no-op（后端另有
 *   409 `probe_in_progress` 兜底）。
 * - **cancel 真正终止工作**：`cancel()` 打后端取消端点（置标志 + 硬杀
 *   worker），行内状态等**原请求**以 `execution_cancelled` 落地——绝不
 *   「界面先装作停了、脚本还在后台跑」。
 * - **迟到响应不能覆盖新请求**：每次 run 换代（`gen`），响应回来时代际
 *   对不上就丢弃。
 * - **切项目后旧响应作废**：`clear()` 升 `epoch`，在途响应按作废处理，
 *   绝不落进新项目的状态表（负向反证 #6 的看护对象）。
 *
 * 错误存**原始 code + params**（`ProbeError`），显示那一刻才按当前语言翻
 * （i18n 纪律：活得比一次渲染长的文本不存成品字符串）。
 */
export type ScriptRunPhase =
  | 'idle'
  | 'starting_runtime' // 请求已发出，后端尚未确认开始执行
  | 'running' // SSE probe.started 已确认在执行
  | 'captured_one'
  | 'captured_many'
  | 'no_figure'
  | 'missing_dependency'
  | 'timeout'
  | 'cancelled'
  | 'failed'

export interface ScriptRunState {
  phase: ScriptRunPhase
  /** 成功那次捕获的描述符（captured_* 才有） */
  descriptors: CapturedFigureDescriptor[]
  /** pyplot 兜底超上限被丢弃的张数（如实报，不静默） */
  droppedFigures: number
  error: ProbeError | null
  /** 用户已点取消、原请求尚未落地 */
  cancelRequested: boolean
  gen: number
}

const IDLE: ScriptRunState = {
  phase: 'idle',
  descriptors: [],
  droppedFigures: 0,
  error: null,
  cancelRequested: false,
  gen: 0,
}

/** 该脚本此刻正在跑（并发闸 / 取消入口的判据） */
export const isBusyPhase = (phase: ScriptRunPhase): boolean =>
  phase === 'starting_runtime' || phase === 'running'

/**
 * 「可能需要原环境」的分组判据（总纲 §四的恢复路径文案挂在这批状态上）：
 * safe 档失败且失败形状像环境问题——缺包、超时、脚本在受控环境里跑不起来。
 * 用户显式取消、跑通没出图不算。native 入口落地（PR 2）后这批升级为
 * 实际入口，此前只给文案与「复制诊断」。
 */
export const needsNative = (state: ScriptRunState | undefined): boolean =>
  !!state && ['missing_dependency', 'timeout', 'failed'].includes(state.phase)

const PHASE_BY_CODE: Record<string, ScriptRunPhase> = {
  missing_dependency: 'missing_dependency',
  execution_timeout: 'timeout',
  execution_cancelled: 'cancelled',
  script_no_figure: 'no_figure',
}

interface ScriptRunStore {
  /** 项目代际：clear() 递增，在途响应据此作废 */
  epoch: number
  byScript: Record<string, ScriptRunState>
  run: (script: string) => Promise<void>
  cancel: (script: string) => void
  /** SSE probe.started：starting_runtime → running（其余状态不动） */
  markRunning: (script: string) => void
  /** 收起结果 / 关闭错误：回 idle */
  reset: (script: string) => void
  clear: () => void
}

export const useScriptRunStore = create<ScriptRunStore>((set, get) => ({
  epoch: 0,
  byScript: {},

  run: async (script) => {
    const prev = get().byScript[script]
    if (prev && isBusyPhase(prev.phase)) return // 同脚本防并发
    const epoch = get().epoch
    const gen = (prev?.gen ?? 0) + 1
    set((s) => ({
      byScript: {
        ...s.byScript,
        [script]: { ...IDLE, phase: 'starting_runtime', gen },
      },
    }))

    /** 迟到响应（换代 / 换项目）一律丢弃 */
    const stale = () =>
      get().epoch !== epoch || get().byScript[script]?.gen !== gen
    const settle = (patch: Partial<ScriptRunState>) => {
      if (stale()) return
      set((s) => ({
        byScript: {
          ...s.byScript,
          [script]: { ...(s.byScript[script] ?? { ...IDLE, gen }), cancelRequested: false, ...patch },
        },
      }))
    }

    try {
      const res = await probeScript(script)
      if (stale()) return
      if (res.error) {
        settle({
          phase: PHASE_BY_CODE[res.error.code] ?? 'failed',
          error: res.error,
          descriptors: [],
        })
        return
      }
      settle({
        phase: res.descriptors.length > 1 ? 'captured_many' : 'captured_one',
        descriptors: res.descriptors,
        droppedFigures: res.dropped_figures ?? 0,
        error: null,
      })
      // 成功的副作用：素材库立即出现新东西。
      // - runtime 清单重取（RuntimeFigureAsset 卡片，含刚物化的描述符）；
      // - 面板列表重取（脚本这次也可能 savefig 出了真文件 / ⚡ 状态变化）；
      // - 该脚本已有 runtime 面板的 stale 判定作废 + 预览换代；
      // - 本会话跑过的 runtime 面板转入引擎跟踪热重建（显式用户动作，
      //   lazy 门只管「重开不自动执行」，不拦这里）。
      // 刷新失败不改变运行结果（结果已经落进状态机）。
      try {
        const ids = res.descriptors.map((d) => d.asset_id)
        const runtime = useRuntimeAssetStore.getState()
        runtime.invalidate(ids)
        runtime.bumpPreview(ids)
        void runtime.loadAssets()
        void useAssetStore.getState().load()
        useRenderStore.getState().markStale(ids)
      } catch {
        /* 清单刷新是尽力而为；下一次 SSE / 手动刷新会补上 */
      }
    } catch (e) {
      if (stale()) return
      const api = e instanceof ApiError ? e : null
      const body = (api?.body ?? {}) as { code?: string; params?: Record<string, unknown> }
      const code = body.code ?? ''
      settle({
        phase: PHASE_BY_CODE[code] ?? 'failed',
        error: {
          code: code || 'internal_error',
          message: e instanceof Error ? e.message : String(e),
          params: body.params,
        },
        descriptors: [],
      })
    }
  },

  cancel: (script) => {
    const st = get().byScript[script]
    if (!st || !isBusyPhase(st.phase) || st.cancelRequested) return
    set((s) => ({
      byScript: {
        ...s.byScript,
        [script]: { ...st, cancelRequested: true },
      },
    }))
    // 真正的终止在后端（置标志 + 硬杀 worker）；行内状态等原请求落地。
    // 取消请求本身失败也不回滚 cancelRequested——原请求总会以某种结果
    // 落地（成功 / 失败 / 超时），状态机不会卡死在「取消中」。
    void cancelProbe(script).catch(() => {})
  },

  markRunning: (script) => {
    const st = get().byScript[script]
    if (!st || st.phase !== 'starting_runtime') return
    set((s) => ({
      byScript: { ...s.byScript, [script]: { ...st, phase: 'running' } },
    }))
  },

  reset: (script) => {
    const st = get().byScript[script]
    if (!st || isBusyPhase(st.phase)) return
    set((s) => {
      const byScript = { ...s.byScript }
      delete byScript[script]
      return { byScript }
    })
  },

  clear: () => set((s) => ({ byScript: {}, epoch: s.epoch + 1 })),
}))
