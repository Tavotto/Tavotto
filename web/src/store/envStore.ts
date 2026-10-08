import { create } from 'zustand'
import { t } from '@/i18n'
import {
  addInputRemap,
  ApiError,
  backendErrorText,
  commitScriptEdit,
  previewInputPathEdit,
  type ScriptEditPreview,
  type ScriptEditSkipped,
  type DependencyPreparationOffer,
  type InputRemapRule,
  type MissingInputOffer,
  removeInputRemap,
  fetchEngineEnvironment,
  installEngineEnvironment,
  setEngineEnvironment,
  setProjectEnvironment,
  setProjectWorkdir,
  type EngineEnvironment,
  type UserEnvironmentSource,
  type WorkdirConfirmation,
  type WorkdirMode,
  restoreScriptBackup as restoreScriptBackupRequest,
} from '@/lib/api'
import { createDismissTimer } from '@/lib/dismissTimer'
import { retryArtifactEntry } from '@/lib/artifactValidation'
import { currentProjectId } from '@/lib/session'
import { askConfirm, useUiStore } from '@/store/uiStore'
import { msg } from '@/i18n'

/**
 * 渲染环境状态。⚡ 参数化编辑需要一个能 import 用户脚本依赖的 Python；
 * 找不到时不该把「找不到装有 matplotlib 的 Python」这句话甩给用户，
 * 而是给一个能点的出口：让 Tavotto 自己装一个，或指定已有的解释器。
 *
 * 安装进度由后端经 SSE `engine.bootstrap` 推过来（useServerEvents 转发到这里）。
 */
interface EnvState {
  env: EngineEnvironment | null
  /** 安装过程的滚动日志 */
  log: string
  installing: boolean
  refresh: () => Promise<void>
  install: () => Promise<void>
  setPython: (path: string | null) => Promise<string | null>
  /**
   * 只为**当前项目**指定解释器（ADR 0018）。传 `null` = 回到默认链条。
   * 与 `setPython` 的区别只有作用域，但那个区别很大：`setPython` 写的是
   * 全局设置，会连带改变别的项目的渲染环境。
   */
  setProjectPython: (path: string | null, module?: string) => Promise<string | null>
  /**
   * 切当前项目 safe worker 的工作目录模式（ADR 0047）。开到 `project` 要先
   * 确认一次——文案与机制逐条一致：相对路径读得到、相对路径写的文件落进项目、
   * 守卫与 savefig 捕获不变。用户取消回 `null` 且什么都不改；失败回错误文案。
   * 成功后把「脚本跑完没出图」那些面板重新排上。
   */
  setWorkdirMode: (mode: WorkdirMode, opts?: { confirmed?: boolean }) => Promise<string | null>
  /**
   * 首开的那一次确认（U03，ADR 0057）：后端在起第一个 worker 之前判出「数据只有项目根
   * 找得到」或「两处同名不同值」，渲染以 `workdir_confirmation_required` 回来——这不是
   * 错误块，是一次选择。渲染 store 把载荷交到这里，`WorkdirConfirmDialog` 渲染它；
   * 同一时刻只开一份（同一项目多张图同时撞上时后来的不覆盖先到的）。
   */
  workdirConfirmation: WorkdirConfirmation | null
  /**
   * `projectId` 是**发那次渲染时**的项目：渲染在途中用户切了项目，A 的失败回来时不许把 A 的
   * 问题摆到 B 上（一点「运行」就把真实目录的授权给错项目，Codex #456 P1）——对不上就丢。
   */
  requestWorkdirConfirmation: (payload: WorkdirConfirmation, projectId?: string | null) => void
  dismissWorkdirConfirmation: () => void
  /**
   * 跑前的那一次授权（U04，ADR 0061）：后端在起第一个 worker 之前判出「脚本开跑要的包目标环境
   * 里没有、能一次装全」，渲染以 `dependency_preparation_required` 回来——同样不是错误块，是
   * 一次授权。载荷放这里（与运行目录的确认同一个家），`DependencyPrepareDialog` 渲染它，执行
   * 归 `depRepairStore.prepare`。同一时刻只开一份；换了项目的旧载荷不弹。
   */
  dependencyPreparation: DependencyPreparationOffer | null
  requestDependencyPreparation: (offer: DependencyPreparationOffer, projectId?: string | null) => void
  dismissDependencyPreparation: () => void
  /**
   * 数据找不到（ADR 0106）：渲染以 `missing_input`（或带 `missing_input` 载荷的「没出图」）回来时，
   * 请用户指认那个文件或它所在的文件夹。载荷放这里（与运行目录的确认同一个家），
   * `MissingInputDialog` 渲染它；同一时刻只开一份，换了项目的旧载荷不弹。
   */
  missingInput: MissingInputOffer | null
  /** 每记住一条数据位置加一：素材库的试运行状态机订阅它，重跑因「找不到数据」失败的脚本 */
  inputRemapGeneration: number
  /** 由试运行派生的结果作废一次加一（改指表增 / 换 / 删、换环境）：scriptRunStore 订阅它丢掉已捕获的结果 */
  probeResultsGeneration: number
  requestMissingInput: (offer: MissingInputOffer, projectId?: string | null) => void
  dismissMissingInput: () => void
  /**
   * 用户指认了数据位置：后端推规则、按项目记住、关掉会话；这里更新设置里的规则表、关框、把因
   * 「找不到数据」失败的面板重新排上。回 null 或一句失败原文（本地化过的）。
   */
  pointAtData: (requested: string, chosen: string, kind: 'file' | 'dir' | 'auto') => Promise<string | null>
  /**
   * 改指表换代了（ADR 0106 §五）：**唯一**的作废入口，按代次幂等——`generation` ≤ 已见代次就忽略，同一代
   * 不论从哪条路来只作废一次。三条路径都调它：改指 / 删指接口的响应（发起的窗口收到就本地作废，不等事件）、
   * 事件流 `input_remap_changed`（同项目的其它窗口）、重连 / 页面恢复时补拉到的代次（`noteInputRemapGeneration`）。
   * 删掉之外的换代顺带重跑因「找不到数据」失败的脚本。
   */
  onInputRemapChanged: (generation: number, reason: 'added' | 'removed' | 'catch_up' | string) => void
  /**
   * 拉到的此刻代次（环境刷新里带着）：这个窗口还没见过任何一代时只记作起点；比已见的新 = 期间有事件丢了，
   * 补一次作废（`onInputRemapChanged(g, 'catch_up')`）。
   */
  noteInputRemapGeneration: (generation: number) => void
  /** 这个窗口已按哪一代作废过（换项目清空；null = 还没见过） */
  inputRemapSeen: number | null
  /** 设置里删一条改指规则；回 null 或一句失败原文 */
  forgetInputRemap: (rule: InputRemapRule) => Promise<string | null>
  /**
   * 经确认改写脚本里的数据路径（ADR 0110）：改指救不回的条目（exists / glob / C++ 读取器）。
   * `rewritePreview` 非空时对话框显示确认页（逐行 diff、备份位置、勾选）；`rewriteSkipped` 是
   * 「一处都改不了」时后端逐条说的原因（对话框列出来，回到「请把数据放回原处」）。
   */
  rewritePreview: ScriptEditPreview | null
  rewriteSkipped: ScriptEditSkipped[]
  /** 提交失败的原因（令牌单次，确认页随之作废）：回到选位置那一步时显示 */
  rewriteError: string | null
  /** 用户指认了数据现在的位置：请后端生成改写预览（脚本一个字节都不改）。回 null 或失败原文 */
  previewRewrite: (entry: string, chosen: string, kind: 'file' | 'dir' | 'auto') => Promise<string | null>
  /** 确认页的「返回」：丢掉这份预览（令牌过十分钟自己失效） */
  cancelRewrite: () => void
  /** 用户勾选确认后提交：两处备份 → 替换 → 关框、重跑因「找不到数据」失败的面板与脚本 */
  commitRewrite: () => Promise<string | null>
  /** 设置里的备份列表要重读：每次改写 / 复原加一 */
  scriptBackupGeneration: number
  bumpScriptBackups: () => void
  /**
   * 设置里的「恢复原脚本 / 只撤销那几处路径 / 整份恢复」。回 null 或一句失败原文（本地化过的）；
   * 请求在飞时换了项目：A 的状态、报错、重排与备份列表刷新一个都不落到 B 上（同样回 null）。
   */
  restoreScriptBackup: (
    backup: { id: string; script: string; current_sha256?: string | null },
    mode: 'full' | 'undo_edits',
  ) => Promise<string | null>
  /**
   * 跑前的门刚刚**自动改用**了用户自己的环境（ADR 0079，SSE `engine.environment_adopted`）：
   * 通知轨上说一句「改用了哪个」并给「改回」。只是说出口，不是一次授权——改用已经发生了。
   */
  adoptedEnvironment: AdoptedEnvironment | null
  noteEnvironmentAdopted: (env: Omit<AdoptedEnvironment, 'token'>) => void
  dismissAdoptedEnvironment: () => void
  /**
   * 「改回」：本项目明确选回默认链条（后端 `remember_default`，之后不再自动挑），所有面板按原来的
   * 环境重渲——缺的包于是又会走依赖弹窗。回 null 或一句失败原文。
   */
  revertAdoptedEnvironment: () => Promise<string | null>
  /**
   * 换项目：`env.project`（项目环境 / 工作目录模式）属于旧项目，立刻清掉再按
   * 新项目重取。不清的话在请求回来之前，开关与错误块的建议说的都是上一个
   * 项目的模式（Codex 评审 P1）。
   */
  resetProject: () => void
  /** SSE 推进度时调用 */
  onProgress: (p: { state: string; log: string; error: string | null }) => void
}

/** 「已改用你的环境」那条提示多久自己走（指针 / 焦点停在上面不走表，见 `NotificationRail`） */
export const ADOPTED_NOTICE_MS = 12_000
export const adoptedDismissTimer = createDismissTimer()

export interface AdoptedEnvironment {
  source: UserEnvironmentSource
  label: string
  python_version: string
  /** 每次改用自增：同一条提示重复出现时 key 换掉，走表重新开始 */
  token: number
}

/**
 * 项目代际（issue #605 评审 / #606 第 1 条）：`resetProject()`（换项目）加一。`env.project`、工作目录模式、
 * 项目解释器说的都是**发请求那个项目**；A 的 GET / PATCH 在切到 B 之后才回来的话，写进来就是把 A 的项目
 * 环境摆在 B 上。所以每个 await 之后、写状态之前按代际判一次——判在写的这一侧，调用方（`depRepairStore`
 * 的采用、设置页）不必各自再挡一遍，也挡不住（写在它们看得见结果之前就发生了）。
 */
let projectEpoch = 0

/**
 * 后端刚关掉本项目的会话、且变的东西说不清影响哪些面板（换环境、改指表增 / 换 / 删）：
 * 每个在用的面板都标 stale 重建——不只是失败的那些，成功画过的可能是按旧条件画的。
 * 由试运行 / 渲染派生、会随之变的前端缓存全在这一处作废（ADR 0106 的清单）：
 *   - renderStore：在途的渲染作废（换代 + abort，晚到的旧回包丢弃），每个面板标 stale（SVG / manifest /
 *     近期档随之换代，预览与挂载层跟着渲染键走）；
 *   - runtimeAssetStore：已查过的判定重查、素材清单重取（后端 stale 阶梯把改指表指纹算在判据里）；
 *   - scriptRunStore：「运行并发现图」已捕获的结果与在飞的那次作废。
 * 回 false = 等 store 加载期间换了项目（B 的面板与素材一个都不动）。
 */
async function restaleProjectRenders(epoch: number, retryMissingInput = false): Promise<boolean> {
  const [{ useRenderStore, renderEpoch }, { useRuntimeAssetStore }] = await Promise.all([
    import('@/store/renderStore'),
    import('@/store/runtimeAssetStore'),
  ])
  if (epoch !== projectEpoch) return false
  // 素材库「运行并发现图」的结果：按旧条件捕获的描述符不能再拿去「添加到画布」。scriptRunStore
  // 依赖本 store，这里 import 它会让 import 环变大——它订阅这两个代际，自己作废 / 重跑。
  // **同一次更新里一起推进**：订阅方先记下要重跑的（带「找不到数据」载荷的），再作废，再重跑——
  // 分两次推进的话，作废先删掉「没出图 + 找不到数据」那一行，重跑那一代就找不到它（Codex 评 #716 P2）
  useEnvStore.setState((s) => ({
    probeResultsGeneration: s.probeResultsGeneration + 1,
    ...(retryMissingInput ? { inputRemapGeneration: s.inputRemapGeneration + 1 } : {}),
  }))
  const render = useRenderStore.getState()
  // 在途的那几次是按旧条件画的：先作废（换代 + abort），晚到的回包不会把 stale 清掉、把旧图当权威
  const previousRenderEpoch = renderEpoch()
  render.invalidateInflight()
  const nextRenderEpoch = renderEpoch()
  const ids = [...new Set(Object.values(render.byKey).map((v) => v.fileId))]
  if (ids.length) render.markStale(ids)
  const runtime = useRuntimeAssetStore.getState()
  runtime.invalidate(Object.keys(runtime.byId))
  if (runtime.assets !== null) void runtime.loadAssets()
  // PNG 准入尚未加入 renderStore，也要在真实改指完成后重新校验源；只放行本次作废。
  if (retryMissingInput) retryArtifactEntry({ from: previousRenderEpoch, to: nextRenderEpoch })
  return true
}

export const useEnvStore = create<EnvState>((set, get) => ({
  env: null,
  adoptedEnvironment: null,
  log: '',
  installing: false,
  workdirConfirmation: null,
  dependencyPreparation: null,
  missingInput: null,
  inputRemapGeneration: 0,
  probeResultsGeneration: 0,
  inputRemapSeen: null,
  rewritePreview: null,
  rewriteSkipped: [],
  rewriteError: null,
  scriptBackupGeneration: 0,

  requestWorkdirConfirmation: (payload, projectId) => {
    if (projectId !== undefined && projectId !== currentProjectId()) return
    if (get().workdirConfirmation) return
    set({ workdirConfirmation: payload })
  },
  dismissWorkdirConfirmation: () => set({ workdirConfirmation: null }),
  requestMissingInput: (offer, projectId) => {
    if (projectId !== undefined && projectId !== currentProjectId()) return
    if (get().missingInput) return
    set({ missingInput: offer })
  },
  dismissMissingInput: () =>
    set({ missingInput: null, rewritePreview: null, rewriteSkipped: [], rewriteError: null }),
  previewRewrite: async (entry, chosen, kind) => {
    const epoch = projectEpoch
    const offer = get().missingInput
    if (!offer) return null
    set({ rewriteSkipped: [], rewriteError: null })
    try {
      const preview = await previewInputPathEdit(offer.script, entry, chosen, kind)
      if (epoch !== projectEpoch || get().missingInput !== offer) return null
      set({ rewritePreview: preview })
      return null
    } catch (e) {
      if (epoch !== projectEpoch) return null
      // 一处都改不了：后端逐条说了为什么（f-string 拼的、在字典键里……），对话框列出来
      const skipped = e instanceof ApiError ? (e.body?.params as { skipped?: unknown })?.skipped : null
      if (Array.isArray(skipped)) set({ rewriteSkipped: skipped as ScriptEditSkipped[] })
      return backendErrorText(e)
    }
  },
  cancelRewrite: () => set({ rewritePreview: null }),
  commitRewrite: async () => {
    const epoch = projectEpoch
    const preview = get().rewritePreview
    if (!preview) return null
    try {
      const res = await commitScriptEdit(preview.token)
      if (epoch !== projectEpoch) return null
      set((s) => ({
        missingInput: null,
        rewritePreview: null,
        rewriteSkipped: [],
        rewriteError: null,
        scriptBackupGeneration: s.scriptBackupGeneration + 1,
      }))
      // 后端已经关掉会话、发了 panel.file_changed；因「找不到数据」失败的面板与素材库脚本也各自重排
      const { useRenderStore } = await import('@/store/renderStore')
      if (epoch !== projectEpoch) return null
      useRenderStore.getState().retryEnvironmentFailures()
      set((s) => ({ inputRemapGeneration: s.inputRemapGeneration + 1 }))
      useUiStore
        .getState()
        .setStatus(
          res.durable === false
            ? msg('engine.scriptEditNotDurable', { script: res.script }, 'errors')
            : msg('engine.rewriteDone', { script: res.script }, 'errors'),
          // 改写做成了打 ✓；没确认落盘的那句是提醒，留 ⓘ
          res.durable === false ? 'info' : 'done',
        )
      return null
    } catch (e) {
      if (epoch !== projectEpoch) return null
      // 令牌失效 / 预览之后脚本或数据又变了：确认页作废（令牌单次），回到选位置那一步说原因
      const text = backendErrorText(e)
      set({ rewritePreview: null, rewriteError: text })
      return text
    }
  },
  bumpScriptBackups: () => set((s) => ({ scriptBackupGeneration: s.scriptBackupGeneration + 1 })),
  restoreScriptBackup: async (backup, mode) => {
    const epoch = projectEpoch
    let error: string | null = null
    try {
      const res = await restoreScriptBackupRequest(backup.id, mode, backup.current_sha256)
      if (epoch !== projectEpoch) return null
      // 已换好、只是没确认落盘（`durable: false`）：照常刷新，但换一句提醒，不说「已恢复」就完事
      useUiStore
        .getState()
        .setStatus(
          res.durable === false
            ? msg('engine.scriptEditNotDurable', { script: backup.script }, 'errors')
            : msg('engine.scriptBackupRestored', { script: backup.script }, 'errors'),
          res.durable === false ? 'info' : 'done',
        )
      const { useRenderStore } = await import('@/store/renderStore')
      if (epoch !== projectEpoch) return null
      useRenderStore.getState().retryEnvironmentFailures()
    } catch (e) {
      if (epoch !== projectEpoch) return null
      error = backendErrorText(e)
    }
    get().bumpScriptBackups()
    return error
  },
  pointAtData: async (requested, chosen, kind) => {
    const epoch = projectEpoch
    const offer = get().missingInput
    try {
      const res = await addInputRemap(requested, chosen, kind)
      // 规则记在 A 上；B 的设置、确认框、渲染重排一个都不动
      if (epoch !== projectEpoch) return null
      const env = get().env
      if (env?.project) set({ env: { ...env, project: { ...env.project, input_remap: res.input_remap } } })
      // SSE may already have resumed PNG validation and opened the next missing-data gate.
      if (get().missingInput === offer) set({ missingInput: null })
      // 响应带回新代次：本地就作废，不等事件（事件丢了也不漏）；事件随后到时按代次去重，不再作废第二次
      get().onInputRemapChanged(res.input_remap.generation, 'added')
      useUiStore.getState().setStatus(msg('engine.missingInputRemembered', undefined, 'errors'), 'done')
      return null
    } catch (e) {
      if (epoch !== projectEpoch) return null
      return backendErrorText(e)
    }
  },
  forgetInputRemap: async (rule) => {
    const epoch = projectEpoch
    try {
      const res = await removeInputRemap(rule.kind, rule.from)
      if (epoch !== projectEpoch) return null
      const env = get().env
      if (env?.project) set({ env: { ...env, project: { ...env.project, input_remap: res.input_remap } } })
      // 重画按「找不到就报错」：同样本地按响应的代次作废，事件按代次去重
      get().onInputRemapChanged(res.input_remap.generation, 'removed')
      return null
    } catch (e) {
      if (epoch !== projectEpoch) return null
      return backendErrorText(e)
    }
  },
  onInputRemapChanged: (generation, reason) => {
    // 事件已按 pj 过滤到本项目（`handleServerEvent`）；换项目期间由 `restaleProjectRenders` 自己按代际丢弃
    const seen = get().inputRemapSeen
    if (seen !== null && generation <= seen) return
    set({ inputRemapSeen: generation })
    const epoch = projectEpoch
    // 删掉：只作废，回到「找不到就报错」；其余（新增 / 换了一处 / 补拉到的不知道是哪种）：因「找不到数据」
    // 失败的脚本也重跑——没有规则救它就再失败一次，不会多错
    void restaleProjectRenders(epoch, reason !== 'removed')
    // 设置里的「数据位置」列表：别的窗口改的也要看得见（回来的代次 ≤ 已见，不会再作废一次）
    void get().refresh()
  },
  noteInputRemapGeneration: (generation) => {
    const seen = get().inputRemapSeen
    if (seen === null) set({ inputRemapSeen: generation })
    else if (generation > seen) get().onInputRemapChanged(generation, 'catch_up')
  },
  requestDependencyPreparation: (offer, projectId) => {
    if (projectId !== undefined && projectId !== currentProjectId()) return
    if (get().dependencyPreparation) return
    set({ dependencyPreparation: offer })
  },
  dismissDependencyPreparation: () => set({ dependencyPreparation: null }),
  noteEnvironmentAdopted: (env) => {
    set((s) => ({ adoptedEnvironment: { ...env, token: (s.adoptedEnvironment?.token ?? 0) + 1 } }))
    adoptedDismissTimer.start(ADOPTED_NOTICE_MS, () => get().dismissAdoptedEnvironment())
  },
  dismissAdoptedEnvironment: () => {
    adoptedDismissTimer.cancel()
    set({ adoptedEnvironment: null })
  },
  revertAdoptedEnvironment: async () => {
    const epoch = projectEpoch
    const error = await get().setProjectPython(null)
    // 「改回」记在 A 上；切到 B 之后才回来的话，B 的提示与面板一个都不动
    if (epoch !== projectEpoch) return null
    if (error) return error
    get().dismissAdoptedEnvironment()
    // 后端已关掉本项目的会话；每个在用的面板都要按原来的环境重建（不只是失败的那些）
    await restaleProjectRenders(epoch)
    return null
  },

  refresh: async () => {
    const epoch = projectEpoch
    try {
      const env = await fetchEngineEnvironment()
      if (epoch !== projectEpoch) return // 响应里的 project 是发请求那个项目的
      set({ env })
      // 改指表的代次（ADR 0106 §五）：重连 / 页面恢复的补拉就是这一次刷新
      const g = env.project?.input_remap?.generation
      if (typeof g === 'number') get().noteInputRemapGeneration(g)
    } catch {
      // 探测失败不该打扰用户：真要渲染时自然会报错
    }
  },

  install: async () => {
    if (get().installing) return
    set({ installing: true, log: '' })
    try {
      await installEngineEnvironment()
    } catch (e) {
      set({
        installing: false,
        log: e instanceof Error ? e.message : t('engine.installFailed', { ns: 'errors' }),
      })
    }
  },

  setPython: async (path) => {
    const epoch = projectEpoch
    try {
      const env = await setEngineEnvironment(path)
      // 全局解释器已经改了（它不属于哪个项目），但响应里带着发请求那个项目的 `project`，切过就不写它。
      // 也不能就此丢掉（#606 第 4 条）：切项目时那次 refresh 可能比这次 PATCH 先回，B 显示的还是旧的全局
      // 解释器——这里按**此刻的**项目重新问一次（refresh 自己按代际判，属于 B）
      if (epoch !== projectEpoch) {
        await get().refresh()
        return null
      }
      set({ env })
      // PATCH 的响应现在与 GET 同形（带 `project`）；老服务端没带的话整体替换会把
      // 受管环境 / 工作目录那几行藏到下一次无关刷新——补一次 GET（Codex 评审 P2）
      if (!env.project) await get().refresh()
      return null
    } catch (e) {
      if (epoch !== projectEpoch) return null
      // 按 code 翻（`environment_mutating` = 安装进行中，暂时不能改），查不到才原文
      return e instanceof Error ? backendErrorText(e) : t('engine.setPythonFailed', { ns: 'errors' })
    }
  },

  setProjectPython: async (path, module) => {
    const epoch = projectEpoch
    try {
      const res = await setProjectEnvironment(path, module)
      // A 的项目环境不写进 B 的 env（#605 评审 P1）；A 的错误也不在 B 上说
      if (epoch !== projectEpoch) return null
      // 项目那半边变了，全局状态里的 project 换成后端刚算出来的那份
      const env = get().env
      if (env) set({ env: { ...env, project: res.project } })
      else await get().refresh()
      return null
    } catch (e) {
      if (epoch !== projectEpoch) return null
      return e instanceof Error ? e.message : t('engine.setPythonFailed', { ns: 'errors' })
    }
  },

  setWorkdirMode: async (mode, opts) => {
    const current = get().env?.project?.workdir?.mode ?? 'sandbox'
    // 「决定过」与「模式相同」是两件事：首开确认框里选「继续沙盒」时模式没变，但要把
    // 这个决定记下来（否则下一张图又问一遍）——那时 `confirmed` 为真，照样发 PATCH
    if (mode === current && !opts?.confirmed) return null
    // 两个真实 cwd 模式都要点头一次（ADR 0047 / 0057）：文案与机制逐条一致。首开确认框
    // 自己就是那一次点头（`confirmed`），不再弹第二层
    if (mode !== 'sandbox' && !opts?.confirmed) {
      const ok = await askConfirm(
        mode === 'project_root'
          ? {
              title: msg('engine.workdirRootConfirmTitle', undefined, 'errors'),
              body: msg('engine.workdirRootConfirmBody', undefined, 'errors'),
              confirmLabel: msg('engine.workdirRootConfirmOk', undefined, 'errors'),
            }
          : {
              title: msg('engine.workdirConfirmTitle', undefined, 'errors'),
              body: msg('engine.workdirConfirmBody', undefined, 'errors'),
              confirmLabel: msg('engine.workdirConfirmOk', undefined, 'errors'),
            },
      )
      if (!ok) return null
    }
    const epoch = projectEpoch
    try {
      const res = await setProjectWorkdir(mode)
      // 模式记在 A 上；B 的环境行、确认框、渲染重排、状态栏一个都不动
      if (epoch !== projectEpoch) return null
      const env = get().env
      if (env) set({ env: { ...env, project: res.project } })
      else await get().refresh()
      if (epoch !== projectEpoch) return null
      set({ workdirConfirmation: null })
      // 后端已经关掉了这个项目的会话；把因「没出图」/「要先选目录」失败的面板重新排上
      const { useRenderStore } = await import('@/store/renderStore')
      if (epoch !== projectEpoch) return null
      useRenderStore.getState().retryEnvironmentFailures()
      const now =
        mode === 'project_root'
          ? 'engine.workdirNowProjectRoot'
          : mode === 'project'
            ? 'engine.workdirNowProject'
            : 'engine.workdirNowSandbox'
      useUiStore.getState().setStatus(msg(now, undefined, 'errors'), 'done')
      return null
    } catch (e) {
      if (epoch !== projectEpoch) return null
      return e instanceof Error ? e.message : t('engine.setPythonFailed', { ns: 'errors' })
    }
  },

  resetProject: () => {
    // 换代**排在清空与重取之前**：之前发出的请求作废，下面这次 refresh 属于新项目
    projectEpoch += 1
    const env = get().env
    // 首开确认框属于旧项目：A 项目问的问题不能由 B 项目回答；已见的改指表代次也是旧项目的——
    // 新项目的第一次刷新重新记起点
    if (env)
      set({
        env: { ...env, project: { open: false } },
        inputRemapSeen: null,
        workdirConfirmation: null,
        dependencyPreparation: null,
        missingInput: null,
        rewritePreview: null,
        rewriteSkipped: [],
        rewriteError: null,
        adoptedEnvironment: null,
      })
    else
      set({
        inputRemapSeen: null,
        workdirConfirmation: null,
        dependencyPreparation: null,
        missingInput: null,
        rewritePreview: null,
        rewriteSkipped: [],
        rewriteError: null,
        adoptedEnvironment: null,
      })
    void get().refresh()
  },

  onProgress: (p) => {
    set({ log: p.log })
    if (p.state === 'done' || p.state === 'failed') {
      set({ installing: false })
      void get().refresh()
    } else if (p.state === 'running') {
      set({ installing: true })
    }
  },
}))
