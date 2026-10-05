import { create } from 'zustand'
import {
  ApiError,
  cancelProbe,
  DEPENDENCY_PREPARATION_CODE,
  INPUT_REMAP_CHANGED_CODE,
  probeScript,
  WORKDIR_CONFIRMATION_CODE,
  type CapturedFigureDescriptor,
  type DependencyPreparationOffer,
  type ProbeError,
  type WorkdirConfirmation,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { useAssetStore } from '@/store/assetStore'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { probeWithDraft, useScriptArgvStore } from '@/store/scriptArgvStore'

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
 *
 * **起会话之前的两道门不是失败**（U03 运行目录 / U04 依赖准备，ADR 0057 / 0061 §六）：试运行以
 * `workdir_confirmation_required` / `dependency_preparation_required` 回来时，载荷交给 `envStore`——与渲染
 * 那条路（`renderStore`）同一个确认框 / 授权框、同一次作答；相位是 `needs_workdir` / `needs_preparation`，
 * **不进「可能需要原环境」**（那组的出口只有换环境 / 复制诊断，新脚本的图还没上画布时就再也走不到安装，
 * Windows 真机验收 main 493a1310）。作答之后由作答的那一方调 `rerunGated` 重跑停在门上的那一行。
 */
export type ScriptRunPhase =
  | 'idle'
  | 'starting_runtime' // 请求已发出，后端尚未确认开始执行
  | 'running' // SSE probe.started 已确认在执行
  | 'captured_one'
  | 'captured_many'
  | 'no_figure'
  | 'missing_dependency'
  | 'needs_workdir' // 起会话之前要先选运行目录（U03）：载荷在 error.confirmation
  | 'needs_preparation' // 起会话之前要先准备依赖（U04）：载荷在 error.dependency_preparation
  | 'missing_input'
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
  /** 这一次失败的诊断引用（T04）：只在带错误落地时有；老后端没有 */
  diagnostic?: { kind: 'script_run'; ref: string } | null
  /** 用户已点取消、原请求尚未落地 */
  cancelRequested: boolean
  gen: number
}

const IDLE: ScriptRunState = {
  phase: 'idle',
  descriptors: [],
  droppedFigures: 0,
  error: null,
  diagnostic: null,
  cancelRequested: false,
  gen: 0,
}

/** 该脚本此刻正在跑（并发闸 / 取消入口的判据） */
export const isBusyPhase = (phase: ScriptRunPhase): boolean =>
  phase === 'starting_runtime' || phase === 'running'

/**
 * 「可能需要原环境」的分组判据（总纲 §四的恢复路径文案挂在这批状态上；缺包在素材库里另归「需要修复」一组，
 * 但没有修复 offer 时恢复入口照样挂在它上面）：
 * safe 档失败且失败形状像环境问题——缺包、超时、脚本在受控环境里跑不起来。
 * 用户显式取消、跑通没出图不算。native 入口落地（PR 2）后这批升级为
 * 实际入口，此前只给文案与「复制诊断」。
 */
export const needsNative = (state: ScriptRunState | undefined): boolean =>
  !!state && ['missing_dependency', 'timeout', 'failed'].includes(state.phase)

const PHASE_BY_CODE: Record<string, ScriptRunPhase> = {
  missing_dependency: 'missing_dependency',
  // 数据找不到（ADR 0106）：不是环境问题，不进「可能需要原环境」那一组——出路是指认数据位置
  missing_input: 'missing_input',
  execution_timeout: 'timeout',
  execution_cancelled: 'cancelled',
  script_no_figure: 'no_figure',
  [WORKDIR_CONFIRMATION_CODE]: 'needs_workdir',
  [DEPENDENCY_PREPARATION_CODE]: 'needs_preparation',
}

/**
 * 错误 → 相位。门的 code 没带载荷（老后端 / 投影缺了）时没有框可弹、行上也没有再打开的东西——按失败落，
 * 至少留在「可能需要原环境」里有出口，而不是停在一句话上无路可走。
 */
const phaseOf = (error: ProbeError): ScriptRunPhase => {
  const phase = PHASE_BY_CODE[error.code] ?? 'failed'
  if (phase === 'needs_preparation' && !error.dependency_preparation) return 'failed'
  if (phase === 'needs_workdir' && !error.confirmation) return 'failed'
  return phase
}

/** 停在起会话之前那两道门上的相位（不是失败：缺的是用户的一个决定） */
export const isGatePhase = (phase: ScriptRunPhase | undefined): boolean =>
  phase === 'needs_workdir' || phase === 'needs_preparation'

/**
 * 试运行撞上起会话之前的门：载荷交给 `envStore`，弹与渲染那条路同一个框（同一时刻只开一份、换了项目的
 * 旧载荷不弹——判据在 `envStore` 那一侧）。`projectId` 是发这次试运行时的项目。回 true = 是门、已交出。
 * 素材库脚本行（经 `run`）与接入中心的试运行共用这一处，别的试运行入口也走这里。
 */
/**
 * 试运行请求**抛出来**的错误（非 2xx：门的两个 code 就是以 409 回来的）→ `ProbeError`，载荷一并带上。
 * 素材库脚本行与接入中心共用这一处：各自解析的话，一边认得门、一边把它当成普通失败（#740 Codex P2）。
 */
export function probeErrorOf(e: unknown): ProbeError {
  const api = e instanceof ApiError ? e : null
  const body = (api?.body ?? {}) as {
    code?: string
    params?: Record<string, unknown>
    dependency_preparation?: DependencyPreparationOffer
    confirmation?: WorkdirConfirmation
  }
  const code = body.code ?? ''
  return {
    code: code || 'internal_error',
    message: e instanceof Error ? e.message : String(e),
    params: body.params,
    dependency_preparation: body.dependency_preparation,
    confirmation: body.confirmation,
  }
}

/**
 * 这个脚本在本 store 里没有正在跑的试运行时 resolve。门放行后本 store 与接入中心会各自重跑同一个脚本，
 * 后端同一脚本只许一个在跑（另一个回 `probe_in_progress`）——接入中心先等本 store 那一次跑完再跑自己的
 * （#740 Codex P2）。
 */
export function whenScriptIdle(script: string): Promise<void> {
  const busy = () => {
    const e = useScriptRunStore.getState().byScript[script]
    return !!e && isBusyPhase(e.phase)
  }
  if (!busy()) return Promise.resolve()
  return new Promise((resolve) => {
    const unsub = useScriptRunStore.subscribe(() => {
      if (busy()) return
      unsub()
      resolve()
    })
  })
}

/**
 * 项目代际：每次换项目 `clear()` 都 +1（A → B → A 回到同一个项目 id，代际也已经变了）。跨 await 的
 * 副作用（门放行后的重跑）按它判「还是不是发起时的那一代」，不按项目 id 判（#740 Codex P2）。
 */
export const scriptRunEpoch = (): number => useScriptRunStore.getState().epoch

/** 门的 code → 它对应的相位（不是门回 null） */
export function gatePhaseOf(error: ProbeError | null | undefined): 'needs_workdir' | 'needs_preparation' | null {
  if (!error) return null
  if (error.code === DEPENDENCY_PREPARATION_CODE && error.dependency_preparation) return 'needs_preparation'
  if (error.code === WORKDIR_CONFIRMATION_CODE && error.confirmation) return 'needs_workdir'
  return null
}

type GateResolved = (phase: 'needs_workdir' | 'needs_preparation', script?: string) => void
const gateListeners = new Set<GateResolved>()

/**
 * 门有了答案时通知：不在本 store 里记账的试运行入口（接入中心的逐行试运行）靠它重跑自己停在门上的那一行
 * ——否则作答之后那一行停在「还差一步」上、要用户再点一次（#740 Codex P2）。回退订函数。
 */
export function onGateResolved(cb: GateResolved): () => void {
  gateListeners.add(cb)
  return () => {
    gateListeners.delete(cb)
  }
}

export function handOffProbeGate(error: ProbeError | null | undefined, projectId: string | null): boolean {
  if (!error) return false
  if (error.code === DEPENDENCY_PREPARATION_CODE && error.dependency_preparation) {
    useEnvStore.getState().requestDependencyPreparation(error.dependency_preparation, projectId)
    return true
  }
  if (error.code === WORKDIR_CONFIRMATION_CODE && error.confirmation) {
    useEnvStore.getState().requestWorkdirConfirmation(error.confirmation, projectId)
    return true
  }
  return false
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
  /**
   * 门有了答案（准备成功 / 明确跳过 / 改用了用户环境 / 选定了运行目录）：把停在这道门上的那一行重跑。
   * 给了 `script` 只重跑那一个（依赖的决定按脚本记）；不给就重跑停在这一相位上的全部（运行目录是项目级的）。
   * 用户已经重跑 / 收起过的（相位不再是这道门）不动。
   */
  rerunGated: (phase: 'needs_workdir' | 'needs_preparation', script?: string) => void
  /**
   * 此刻因「找不到数据」失败、指认了数据位置之后要重跑的脚本（ADR 0106）——带 `missing_input` 载荷的
   * 错误（`missing_input` 与「跑通了但没出图」两种）都算。只读；重跑由 envStore 代际订阅发起。
   */
  missingInputScripts: () => string[]
  /**
   * 改指表变了（ADR 0106）：按旧映射跑出来的结果作废——已捕获的描述符（尺寸、指纹、「添加到画布」
   * 用的就是它们）与「跑通了但没出图」回 idle；在飞的那次按迟到响应丢掉（它读的是旧位置）。
   * 失败态不动：因「找不到数据」失败的由代际订阅重跑（先收集、再作废、再重跑），其余与数据位置无关。
   */
  invalidateCaptured: () => void
  clear: () => void
}

export const useScriptRunStore = create<ScriptRunStore>((set, get) => ({
  epoch: 0,
  byScript: {},

  run: async (script) => {
    const prev = get().byScript[script]
    if (prev && isBusyPhase(prev.phase)) return // 同脚本防并发
    const epoch = get().epoch
    const projectAtStart = currentProjectId()
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
      // T03：参数草稿在**运行开始那一刻**取一份拷贝；此后再编辑不影响这一次
      const res = await probeWithDraft(probeScript, script)
      if (stale()) return
      if (res.error?.code === INPUT_REMAP_CHANGED_CODE) {
        // 试运行途中改了指认，后端按代次丢弃了这次结果（ADR 0106 §五）：按新表重跑一次，不报失败
        set((s) => {
          const byScript = { ...s.byScript }
          delete byScript[script]
          return { byScript }
        })
        void get().run(script)
        return
      }
      if (res.error?.missing_input) {
        // 与画布同一个对话框：请用户指认数据位置（换了项目的旧载荷由 envStore 丢掉）
        useEnvStore.getState().requestMissingInput(res.error.missing_input, projectAtStart)
      }
      if (res.error) {
        settle({
          phase: phaseOf(res.error),
          error: res.error,
          diagnostic: res.diagnostic ?? null,
          descriptors: [],
        })
        // 起会话之前的门：弹与渲染那条路同一个框；行上留着载荷，「稍后」之后能再开
        handOffProbeGate(res.error, projectAtStart)
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
      const error = probeErrorOf(e)
      settle({ phase: phaseOf(error), error, descriptors: [] })
      handOffProbeGate(error, projectAtStart)
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

  missingInputScripts: () =>
    Object.entries(get().byScript)
      .filter(([, st]) => !isBusyPhase(st.phase) && st.error?.missing_input)
      .map(([script]) => script),

  invalidateCaptured: () =>
    set((s) => {
      const byScript: Record<string, ScriptRunState> = {}
      for (const [script, st] of Object.entries(s.byScript)) {
        const derived =
          isBusyPhase(st.phase) ||
          st.phase === 'captured_one' ||
          st.phase === 'captured_many' ||
          st.phase === 'no_figure'
        // 删掉这一行 = 回 idle；在飞的那次落地时 `gen` 对不上，按迟到响应丢弃
        if (!derived) byScript[script] = st
      }
      return { byScript }
    }),

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

  rerunGated: (phase, script) => {
    const scripts = script ? [script] : Object.keys(get().byScript)
    for (const name of scripts) {
      if (get().byScript[name]?.phase === phase) void get().run(name)
    }
    for (const cb of [...gateListeners]) cb(phase, script)
  },

  clear: () => {
    // 换项目：参数草稿（可能含令牌）属于上一个项目的脚本，一并丢掉
    useScriptArgvStore.getState().clear()
    set((s) => ({ byScript: {}, epoch: s.epoch + 1 }))
  },
}))

// 改指表 / 环境变了（ADR 0106）：envStore 的两个代际——作废按旧条件捕获的结果、重跑因「找不到数据」失败的脚本
useEnvStore.subscribe((state, prev) => {
  const store = useScriptRunStore.getState()
  // 顺序是判据：**先收集**要重跑的（带「找不到数据」载荷的，含「没出图」那种），**再作废**旧条件下的
  // 结果（它会删掉「没出图」那一行），**最后重跑**收集到的——不依赖作废时留哪些行（Codex 评 #716 P2）
  const retry = state.inputRemapGeneration !== prev.inputRemapGeneration ? store.missingInputScripts() : []
  if (state.probeResultsGeneration !== prev.probeResultsGeneration) store.invalidateCaptured()
  for (const script of retry) void useScriptRunStore.getState().run(script)
})
