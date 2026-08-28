import { useMemo } from 'react'
import { msg, type UiMessage } from '@/i18n'
import { create } from 'zustand'
import {
  ENVIRONMENT_CODES,
  EngineError,
  engineErrorMsg,
  engineRender,
  type DependencyRepairOffer,
  type Manifest,
  type ProjectEnvFailure,
} from '@/lib/api'
import { engineTransport } from '@/lib/engineTransport'
import { resolvePreview, VECTOR_PREVIEW, type PreviewMetadata } from '@/lib/previewBudget'
import { useAssetStore } from '@/store/assetStore'
import { useEnvStore } from '@/store/envStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { fileHash, recordDiagnosticEvent, variantHash } from '@/diagnostics'

export type RenderStatus = 'idle' | 'rendering' | 'ready' | 'error'

export interface PanelRender {
  /** 这份渲染态属于哪个素材文件（文件级操作靠它反查，见 markStale / reset） */
  fileId: string
  rev: number
  manifest: Manifest | null
  /** 已处理好的 SVG 文本（去掉 width/height，铺满容器） */
  svg: string | null
  /**
   * 这一版该用哪种预览表示法（ADR 0021）。老后端不返回 `preview` 时是
   * `VECTOR_PREVIEW`，行为与从前逐字节一致。
   *
   * `mode === 'raster'` ⇒ **`svg` 必然是 null**，而且那是刻意的：引擎按硬闸
   * 决定不把那份 SVG 读进内存（issue #181）。这不是渲染失败，`status` 照样是
   * `'ready'`、`manifest` 照样在——画布改用位图显示，命中层与几何权威一个字
   * 都不放松（不变量 4）。
   */
  preview: PreviewMetadata
  status: RenderStatus
  /**
   * 失败原因（**描述符**，不是翻译好的字符串）。渲染失败会一直挂在 store 里
   * 直到这一版重新画成功或再次失败——heavy 脚本的看门狗档位是 15 分钟，
   * 中途用户在设置里切了界面语言，周围文案即时跟着换，唯独这条角标会永远
   * 停在旧语言（参数已经拼死在字符串里，再也换不回来）。
   * 后端/脚本原文属于诊断材料、不翻译，用 `literal()` 原样透出。
   */
  error: UiMessage | null
  /**
   * 机器可读的失败原因。属于 ENVIRONMENT_CODES 的那几个是「缺件」而不是
   * 「脚本报错」，界面给出口而不是 traceback。
   */
  code: string
  /** code === 'missing_dependency' 时缺的那个包名 */
  module: string
  /**
   * `missing_dependency` 且**项目环境自动接手也没成**时的结构化原因
   * （ADR 0018）：找不到 venv / 找到了但也没这个包 / 没有 matplotlib /
   * Python 版本不支持。界面据此给四种不同的恢复引导。
   */
  projectEnv: ProjectEnvFailure | null
  /**
   * `missing_dependency` 时「这个包能怎么修」（ADR 0019）：解析出来的包名、
   * 可选的安装目标、还剩几轮。null = 后端没给（老服务端 / 没打开项目）。
   */
  dependencyRepair: DependencyRepairOffer | null
  traceback: string
  warnings: string[]
  /** 最近一次成功渲染的阶段计时（毫秒，键见 api.ts）；暂不做 UI */
  timings: Record<string, number>
  /** 脚本文件变了，当前 SVG 已过期 */
  stale: boolean
  /** 最近一次成功渲染所用的 patches，用于判断是否需要重渲染 */
  lastPatches: string | null
  /** 已排队但尚未完成的 patches，避免同一批改动被重复排期 */
  wantPatches: string | null
  /**
   * 最近一次渲染用的预览 dpi；null = 默认（定稿质量）。
   * 连续调整期间含图像的面板会降质换快显，松手后必须有人把它重发成
   * 默认 dpi——这个字段就是「现在这张是临时低清」的唯一标记。
   */
  previewDpi: number | null
}

const EMPTY: PanelRender = {
  fileId: '',
  rev: 0,
  manifest: null,
  svg: null,
  preview: VECTOR_PREVIEW,
  status: 'idle',
  error: null,
  code: '',
  module: '',
  projectEnv: null,
  dependencyRepair: null,
  traceback: '',
  warnings: [],
  timings: {},
  stale: false,
  lastPatches: null,
  wantPatches: null,
  previewDpi: null,
}

/**
 * 渲染态的键：**文件 + 变体**，而不是文件。
 *
 * 复制面板（structuredClone）保留原 fileId，画布上完全可能出现两个指向同一
 * 文件、overrides 不同的面板——那是两张不同的图，各有各的 SVG 与 manifest。
 * 以前按 fileId 索引，只能靠「裁出一个说了算的面板」回避，输家显示的就是
 * 赢家的图。现在各存各的，worker 里那份 live figure 轮流全量重放即可
 * （patch_apply≈0ms、热画 17–28ms，见 docs/perf-baseline.md）。
 *
 * 分隔符用空格是安全的：变体串是 `JSON.stringify(数组)`，必然以 `[` 开头，
 * 拼出来的键不可能与另一个「文件名里带空格」的组合撞上。
 */
export function renderKey(fileId: string, patches: unknown[]): string {
  return `${fileId} ${JSON.stringify(patches)}`
}

/** 面板 → 它自己那份渲染态的键（唯一出处，消费方一律用它取状态） */
export function renderKeyOf(panel: PanelObject): string {
  return renderKey(panel.fileId, panel.overrides)
}

/**
 * matplotlib 的 SVG 自带 pt 单位的 width/height，去掉后配合
 * preserveAspectRatio=none 才能精确铺满面板框（面板宽高由文档决定）。
 */
function prepareSvg(text: string): string {
  return text.replace(/<svg([^>]*)>/, (_m, attrs: string) => {
    const cleaned = attrs.replace(/\s(?:width|height)="[^"]*"/g, '')
    return `<svg${cleaned} preserveAspectRatio="none" style="width:100%;height:100%;display:block">`
  })
}

/** 诊断用：这次渲染是怎么被触发的。**与渲染行为无关**，只进 trace */
export type RenderRequestPolicy = 'immediate' | 'defer' | 'none' | 'sync'

interface RenderState {
  /** 键见 renderKey()：一个面板变体一条 */
  byKey: Record<string, PanelRender>
  /**
   * 文件级：脚本已经领先于磁盘上的 PDF/PNG，显示必须走引擎而不是 /api/render。
   * AI 改完脚本、watcher 报出文件变更后置位。**与变体无关**，而且必须在
   * 「这个文件还一个变体都没渲染过」时也成立——同步器正是靠它决定要不要
   * 立刻重建。
   */
  tracked: Record<string, boolean>
  /**
   * 每个文件最近一次**画成功**的那个变体键。用途只有一个：面板改了一个值之后
   * 变体键当场就变了，新键还没有图——不接着显示上一张的话，画布会在每一次
   * 输入时闪回磁盘原图（heavy 脚本上就是几秒钟的闪烁），属性页与命中测试
   * 也会跟着空一拍。见 panelRender()。
   */
  latest: Record<string, string>
  /**
   * `latest` 是**按请求顺序**推进的，不是「谁最后返回谁说了算」。
   * 撤销之后新变体先回来、旧变体的响应姗姗来迟时，后者不许把显示拽回旧图。
   * 存的是那次成功所对应的请求序号。
   */
  latestSeq: Record<string, number>
  /**
   * 每个文件**最近成功的若干个变体键**（新的在前，上限 RECENT_VARIANTS）。
   * 撤销 / 版本恢复的落点几乎总在这几档里：留着就能当场把精确 SVG 与 manifest
   * 换回来（选择框也跟着立刻复位），而不是文档已经退回、画面还挂着退回前那张。
   * 上限是硬的——不设上限等于把一次长编辑的每一版 SVG 全留在内存里。
   */
  recent: Record<string, string[]>
  get: (key: string) => PanelRender
  patch: (key: string, next: Partial<PanelRender>) => void
  /**
   * 文件级「正在构建」（SSE 的 render.started/done，只带 fileId）。
   * **不写进变体条目**：manifest / status 属于某个具体变体，被一条文件级事件
   * 盖掉的话，同文件另一个副本会永远转着圈——它自己根本没在渲染，也就没人
   * 来把那个状态收掉。冷启动提示本来就是文件粒度的（worker 一个 stem 一份）。
   */
  building: Record<string, { cold: boolean; cost: string }>
  noteBuilding: (fileId: string, info: { cold: boolean; cost: string } | null) => void
  /** 渲染并取回 SVG（与 manifest 同一响应）；同键渲染中重复调用只保留最后一次待办。
   *  `policy` 只进诊断事件，**不影响任何渲染行为**——它是「这次是定稿还是防抖」
   *  的说明，缺省按 immediate 记。 */
  render: (
    fileId: string,
    patches: unknown[],
    previewDpi?: number,
    policy?: RenderRequestPolicy,
  ) => Promise<void>
  /** 脚本变更：转入引擎跟踪并清掉该文件**全部变体**的 lastPatches */
  markStale: (fileIds: string[]) => void
  /**
   * 环境变了（装完缺的那个包、换了解释器）：把**所有因缺件失败**的面板
   * 重新排队。
   *
   * 少了这一步，「点一次「安装并继续」→ 图出来」这条主路根本走不完：
   * 失败那次的 `wantPatches` 仍等于当前 overrides，`useEngineSync` 会
   * 认为「这一版已经排过了」直接跳过，于是卡片停在原地，用户要么改点
   * 别的、要么刷新页面才看得到图（Codex 评审 P1）。
   *
   * 按**错误码**收面板而不是按脚本名：同一个环境上因缺包失败的可能不止
   * 一个面板，而装上那个包对它们是同一件好事。
   */
  retryEnvironmentFailures: () => void
  /** 丢掉某个文件的全部变体 */
  reset: (fileId: string) => void
  /**
   * 丢掉没人再引用的变体：`live` 是文档里现存面板的键集合。
   * 编辑期每改一个值就多一个变体条目（每条带一整份 SVG，imshow 面板能到
   * 几百 KB）——不清理的话一次长时间编辑就能把几百 MB 留在内存里。
   * 每个文件最近成功的那份、最近 RECENT_VARIANTS 档与在途的那份永远保留
   * （前者是 panelRender 的退路，后者是撤销的落点）。
   */
  prune: (live: Set<string>) => void
  /** 换项目：渲染态、跟踪表、在途账本一起归零 */
  clear: () => void
}

/** 每个变体一份在途状态：busy 时只记最后一次待办，避免连发把 worker 淹没 */
const inflight = new Map<
  string,
  { busy: boolean; queued: { patches: unknown[]; previewDpi?: number; seq: number } | null }
>()

/**
 * 每个文件保留的近期精确变体档数。撤销一步、版本恢复一格几乎总落在这几档里，
 * 留着就不用为了「回到刚才那一版」再白跑一次引擎（heavy 脚本上是分钟级）。
 * 4 档 × 一份 SVG 是明确的内存上限，imshow 面板最坏约几 MB/文件。
 */
const RECENT_VARIANTS = 4

/**
 * 渲染请求的全局单调序号。用途只有一个：判断一次成功回来的响应**是不是最新
 * 那次请求的**——乱序返回时旧变体不许把 `latest` 拽回去。
 */
let requestSeq = 0

/**
 * 渲染请求看门狗：fetch 永不 settle（服务重启留下的半开连接、代理悬挂）时
 * busy 永远不释放，该面板从此渲染不动。阈值只兜连接悬挂，不是性能预算，
 * 按脚本 cost 取得刻意宽松——heavy 冷启动本身就是分钟级。
 */
const WATCHDOG_MS: Record<string, number> = {
  light: 2 * 60_000,
  medium: 5 * 60_000,
  heavy: 15 * 60_000,
}

function watchdogMs(fileId: string): number {
  // 调试后门：agent-browser 实测超时链路时把阈值压到秒级
  const dev = (window as { __MM_RENDER_TIMEOUT_MS__?: unknown }).__MM_RENDER_TIMEOUT_MS__
  if (typeof dev === 'number' && dev > 0) return dev
  const cost = useAssetStore.getState().byId[fileId]?.cost ?? ''
  return WATCHDOG_MS[cost] ?? WATCHDOG_MS.medium
}

export const useRenderStore = create<RenderState>((set, get) => ({
  byKey: {},
  tracked: {},
  latest: {},
  latestSeq: {},
  recent: {},
  building: {},

  get: (key) => get().byKey[key] ?? EMPTY,

  patch: (key, next) =>
    set((s) => ({
      byKey: { ...s.byKey, [key]: { ...(s.byKey[key] ?? EMPTY), ...next } },
    })),

  noteBuilding: (fileId, info) =>
    set((s) => {
      if (!info) {
        if (!(fileId in s.building)) return {}
        const building = { ...s.building }
        delete building[fileId]
        return { building }
      }
      return { building: { ...s.building, [fileId]: info } }
    }),

  render: async (fileId, patches, previewDpi, policy) => {
    const key = renderKey(fileId, patches)
    // 序号在**请求进来的那一刻**取，不是发出的那一刻：忙时排队的那次要带着
    // 自己的序号走完全程，否则一个早就该被覆盖的旧变体会因为「重试发得晚」
    // 而显得最新，把 latest 拽回去（撤销之后画面弹回对齐后的样子）。
    let seq = ++requestSeq
    const slot = inflight.get(key) ?? { busy: false, queued: null }
    inflight.set(key, slot)
    if (slot.busy) {
      // 同一变体的重复请求：只有 dpi 可能不同（patches 相同才是同一个键），
      // 排在后面的那次说了算——松手后的定稿渲染必须盖住拖动中的低清那次
      slot.queued = { patches, previewDpi, seq }
      return
    }
    slot.busy = true
    const patch = get().patch

    try {
      let current = patches
      let dpi = previewDpi
      for (;;) {
        patch(key, {
          fileId,
          status: 'rendering',
          error: null,
          traceback: '',
          code: '',
          module: '',
        })
        // 诊断：**每一次真正发出去的尝试**各记一条。重试循环里也记——
        // 「同一个变体连发了三次」正是竞态类问题最直接的证据
        const startedAt = Date.now()
        recordDiagnosticEvent({
          type: 'render.request',
          file: fileHash(fileId),
          variant: variantHash(renderKey(fileId, current)),
          policy: policy ?? 'immediate',
          preview_dpi: dpi ?? null,
        })
        const ctrl = new AbortController()
        const timeoutMs = watchdogMs(fileId)
        let timedOut = false
        const watchdog = window.setTimeout(() => {
          timedOut = true
          ctrl.abort()
        }, timeoutMs)
        try {
          // SVG 与 manifest 同一响应（inline_svg）：第二跳 GET 读的是磁盘上
          // 那一份，另一个变体的渲染插进来就会与本次 manifest 错配
          // 装了替代传输就走它（Codex 内嵌画布 → MCP 的 tools/call），
          // 否则还是原来那条 HTTP。两侧最终落到同一个 worker.override，
          // 这里以下的逻辑一行都不分叉
          const opts = { signal: ctrl.signal, previewDpi: dpi }
          const transport = engineTransport()
          const res = transport
            ? await transport.render(fileId, current, opts)
            : await engineRender(fileId, current, opts)
          if (res.environment_switched) {
            // 内置环境缺包，Tavotto 自己找到并换用了项目的 .venv（ADR 0018）。
            // 一条轻量 toast 就够——**不弹阻断式对话框**：用户点的是「渲染」，
            // 不是「读一段技术说明」。完全不提示也不行，跑脚本的解释器换了，
            // 版本对不上时用户得知道去哪儿看。
            useUiStore.getState().setStatus(
              msg('render.projectEnvSwitched',
                  { path: res.environment_switched.python }, 'errors'))
            void useEnvStore.getState().refresh()
          }
          const next: Partial<PanelRender> = {
            fileId,
            rev: res.rev,
            manifest: res.manifest,
            status: 'ready',
            error: null,
            projectEnv: null,
            dependencyRepair: null,
            traceback: '',
            warnings: res.warnings ?? [],
            timings: res.timings ?? {},
            stale: false,
            lastPatches: JSON.stringify(current),
            previewDpi: dpi ?? null,
          }
          // 二道闸（ADR 0021）：后端的 pre-read 闸才是第一道保护，这里拦的是
          // 「字节已经进了浏览器进程」之后的那一步——绝不 prepareSvg + 存进
          // store + 塞进 DOM。判据收在 `resolvePreview` 一处。
          const guarded = resolvePreview(res)
          next.preview = guarded.preview
          if (guarded.svg != null) {
            next.svg = prepareSvg(guarded.svg)
          } else if (guarded.preview.mode === 'raster') {
            // raster 是**这一版的裁决**：留着上一次的矢量 SVG 既占内存，
            // 又会让「画布上挂的是哪一版」说不清楚。后端没给 `svg` 而 mode
            // 仍是 vector（老服务端）时才走 else，保留上一版别刷成空白。
            next.svg = null
          }
          // 成功那一刻同时挪动该文件的「最近画好的那份」，并把这一档记进
          // 近期缓存。**晚到的旧请求只入库、不挪 latest**——撤销之后新变体
          // 已经上屏，旧变体的响应再回来不该把画面拽回去（同文件的另一个副本
          // 仍可能在等这份结果，所以不能整个丢掉）。
          set((s) => {
            const fresher = seq >= (s.latestSeq[fileId] ?? 0)
            const recent = [
              key,
              ...(s.recent[fileId] ?? []).filter((k) => k !== key),
            ].slice(0, RECENT_VARIANTS)
            return {
              byKey: { ...s.byKey, [key]: { ...(s.byKey[key] ?? EMPTY), ...next } },
              latest: fresher ? { ...s.latest, [fileId]: key } : s.latest,
              latestSeq: fresher ? { ...s.latestSeq, [fileId]: seq } : s.latestSeq,
              recent: { ...s.recent, [fileId]: recent },
            }
          })
          recordDiagnosticEvent({
            type: 'render.success',
            file: fileHash(fileId),
            variant: variantHash(renderKey(fileId, current)),
            duration_ms: Date.now() - startedAt,
            // manifest 摘要**只有计数与图幅**：元素的 label 是图内文字
            element_count: res.manifest?.elements.length ?? 0,
            size_mm: res.manifest?.size_mm,
            warning_count: res.warnings?.length ?? 0,
            rev: res.rev,
          })
        } catch (err) {
          // 诊断先记，**再**分「被新请求顶掉」还是「终态失败」——被顶掉的那次
          // 同样是一次真实的失败尝试，不记的话 trace 里就是一条有去无回的
          // render.request，读起来像卡死
          recordDiagnosticEvent({
            type: 'render.error',
            file: fileHash(fileId),
            variant: variantHash(renderKey(fileId, current)),
            duration_ms: Date.now() - startedAt,
            // **机器可读的 code**，不是 traceback、不是报错原文——
            // 那两样里装着用户的脚本与路径
            code: timedOut ? 'timeout' : err instanceof EngineError ? err.code : 'unknown',
          })
          // 在途期间又排了新请求：直接跑最新那次，别停在旧请求的错误上
          // （否则 wantPatches 已等于新改动，同步器会永远跳过它）
          if (slot.queued != null) {
            current = slot.queued.patches
            dpi = slot.queued.previewDpi
            seq = slot.queued.seq
            slot.queued = null
            continue
          }
          // 失败时保留旧 SVG，用户还能看到上一版
          patch(key, {
            fileId,
            status: 'error',
            code: err instanceof EngineError ? err.code : '',
            module: err instanceof EngineError ? err.module : '',
            projectEnv: err instanceof EngineError ? (err.projectEnv ?? null) : null,
            dependencyRepair:
              err instanceof EngineError ? (err.dependencyRepair ?? null) : null,
            error: timedOut
              ? msg('render.timeout',
                    { minutes: Math.round(timeoutMs / 60_000) }, 'errors')
              // code 有文案时按当前语言包装（worker 的 error 原文是中文，
              // 英文界面直接透出 = 泄漏系统文案，issue #30 实测撞见）
              : engineErrorMsg(err),
            traceback: err instanceof EngineError ? err.traceback : '',
          })
          return
        } finally {
          window.clearTimeout(watchdog)
        }
        if (slot.queued == null) break
        current = slot.queued.patches
        dpi = slot.queued.previewDpi
        seq = slot.queued.seq
        slot.queued = null
      }
    } finally {
      slot.busy = false
    }
  },

  retryEnvironmentFailures: () => {
    const ids = new Set<string>()
    for (const v of Object.values(get().byKey)) {
      if (v.status === 'error' && (ENVIRONMENT_CODES as readonly string[]).includes(v.code)) {
        ids.add(v.fileId)
      }
    }
    // `markStale` 已经做了要做的三件事：清 lastPatches/wantPatches（否则
    // 同步器跳过）、置 stale、把文件级跟踪位打开（该文件可能一个变体都还
    // 没成功渲染过——冷启动就缺包的面板正是这种）。
    if (ids.size) get().markStale([...ids])
  },

  markStale: (fileIds) =>
    set((s) => {
      const byKey = { ...s.byKey }
      const tracked = { ...s.tracked }
      const recent = { ...s.recent }
      for (const id of fileIds) {
        // 脚本变了：近期档里那几张已经不是这个脚本的样子，留着只占内存。
        // `latest` 那一张仍留作显示退路（画布别闪白），但它已经不是权威
        // ——lastPatches 一并清掉，`exactPanelRender` 会当场拒绝它。
        delete recent[id]
        // 该文件可能一个变体都还没渲染过：跟踪位记在文件级，
        // 否则同步器根本看不到「这个面板需要按新脚本重建」
        tracked[id] = true
        let touched = 0
        for (const [k, v] of Object.entries(byKey)) {
          if (v.fileId !== id) continue
          byKey[k] = { ...v, stale: true, lastPatches: null, wantPatches: null }
          touched++
        }
        recordDiagnosticEvent({
          type: 'render.stale',
          file: fileHash(id),
          variant_count: touched,
        })
      }
      return { byKey, tracked, recent }
    }),

  reset: (fileId) =>
    set((s) => {
      const byKey = { ...s.byKey }
      for (const [k, v] of Object.entries(byKey)) {
        if (v.fileId !== fileId) continue
        delete byKey[k]
        inflight.delete(k)
      }
      const tracked = { ...s.tracked }
      const latest = { ...s.latest }
      const latestSeq = { ...s.latestSeq }
      const recent = { ...s.recent }
      delete tracked[fileId]
      delete latest[fileId]
      delete latestSeq[fileId]
      delete recent[fileId]
      return { byKey, tracked, latest, latestSeq, recent }
    }),

  prune: (live) => {
    const s = get()
    const keep = (key: string, v: PanelRender) =>
      live.has(key) ||
      s.latest[v.fileId] === key ||
      // 撤销的落点：最近几档精确变体留着，回退时当场换回精确图与 manifest
      s.recent[v.fileId]?.includes(key) === true ||
      v.status === 'rendering' ||
      inflight.get(key)?.busy === true
    const drop = Object.entries(s.byKey).filter(([k, v]) => !keep(k, v))
    // 没得清就**一个 set 都不发**：这个动作跟在渲染同步的 effect 后面，
    // 每次都写一遍 store 等于给自己造一个无限循环
    if (!drop.length) return
    const byKey = { ...s.byKey }
    for (const [k] of drop) {
      delete byKey[k]
      inflight.delete(k)
    }
    // 近期档索引跟着收敛：条目都清了还留着键会让下一轮 keep() 认一个空壳
    const recent = { ...s.recent }
    for (const [fileId, keys] of Object.entries(recent)) {
      const alive = keys.filter((k) => k in byKey)
      if (alive.length !== keys.length) recent[fileId] = alive
    }
    set({ byKey, recent })
  },

  clear: () => {
    inflight.clear()
    set({ byKey: {}, tracked: {}, latest: {}, latestSeq: {}, recent: {}, building: {} })
  },
}))

export const emptyRender = EMPTY

/* -------------------------------------------------------------------------- */
/*  显示回退 ≠ 几何权威                                                        */
/* -------------------------------------------------------------------------- */

/**
 * 面板当前该**显示**的渲染态。
 *
 * 优先自己那份变体；它还没画出来（刚改完值、或刚被 markStale 清掉）时，
 * **manifest / SVG / rev 退回该文件最近画好的那份**——状态类字段
 * （status/error/warnings/timings）一律取自己的，退回的只是「暂时先接着显示
 * 上一张」。不这么做的话每一次输入都会闪回磁盘原图。
 *
 * ⚠️ 返回的 `manifest` **可能来自别的变体、甚至同文件另一个面板**。它只够用来
 * 列元素、判角标、认 role，**绝不能喂给任何几何写操作**——那条路一律走
 * `exactPanelRender` / `exactPanelManifest`。issue #131 就是这么来的：
 * 对齐拿上一版的墨迹 bbox 配当前版的锚点算落点，算出来的位置不属于任何一版。
 */
export function panelRender(
  state: Pick<RenderState, 'byKey' | 'latest'>,
  panel: PanelObject,
): PanelRender | undefined {
  const own = state.byKey[renderKeyOf(panel)]
  if (own?.manifest) return own
  const prev = state.byKey[state.latest[panel.fileId] ?? '']
  return mergeRender(own, prev)
}

function mergeRender(
  own: PanelRender | undefined,
  prev: PanelRender | undefined,
): PanelRender | undefined {
  if (!prev || prev === own) return own
  if (!own) return prev
  const svg = own.svg ?? prev.svg
  return {
    ...own,
    manifest: own.manifest ?? prev.manifest,
    svg,
    rev: own.rev || prev.rev,
    // **表示法跟着 SVG 走**：退回来的是上一版那张图，那么「该怎么显示它」
    // 也得是上一版的答案。拿自己那份（还没画出来 = 默认 vector）去解读别人
    // 的 SVG，正是 raster 面板会在退回窗口里闪一下矢量图的那条路。
    preview: own.svg ? own.preview : prev.preview,
  }
}

/**
 * 这个面板**此刻的**几何权威。判据四条，缺一不可：
 *
 *   1. 条目就是 `byKey[renderKeyOf(panel)]`——**不许退回 `latest[fileId]`**
 *      （那可能是上一版，也可能是同文件另一个副本的变体）；
 *   2. 真的有 manifest；
 *   3. `lastPatches` 与当前 overrides 逐字相等——这一版确实画出来过；
 *   4. 没被 `markStale` 标记（脚本改过，旧墨迹框可能整个不作数）。
 *
 * **刻意不要求 `status === 'ready'`**：同一个键重发一次（松手补定稿 dpi）
 * 会把状态打回 'rendering'，而键相同 = overrides 相同 = 几何不变，那份 manifest
 * 依旧对得上；渲染失败时同理，条目里留着的是**这一版**最后一次成功的结果。
 * 真正会让几何失效的是「换了变体」和「脚本变了」，上面两条已经盖住。
 */
export function exactPanelRender(
  state: Pick<RenderState, 'byKey'>,
  panel: PanelObject,
): PanelRender | null {
  return exactOf(state.byKey[renderKeyOf(panel)], panel)
}

function exactOf(own: PanelRender | undefined, panel: PanelObject): PanelRender | null {
  if (!own?.manifest || own.stale) return null
  if (own.lastPatches !== JSON.stringify(panel.overrides)) return null
  return own
}

/** 几何权威的 manifest；拿不到就是「现在不许做几何写操作」 */
export function exactPanelManifest(
  state: Pick<RenderState, 'byKey'>,
  panel: PanelObject,
): Manifest | null {
  return exactPanelRender(state, panel)?.manifest ?? null
}

/**
 * 画布上此刻挂着的那一版的来源。**判别联合**，不是一个带注释的可选字段：
 * `fallback` 分支在类型上就没有 `manifest`，退回来的墨迹框想流进写路径
 * 得先过 TypeScript 这一关。
 */
export type PanelDisplayView =
  | {
      kind: 'exact'
      currentKey: string
      sourceKey: string
      svg: string
      manifest: Manifest
      render: PanelRender
    }
  | {
      /**
       * 这一版画出来了，但**刻意没有矢量 SVG**（ADR 0021 的 raster 档）：
       * 画布挂的是这一版自己的位图。`manifest` 在——几何权威一个字都不放松。
       * 与 `fallback` 的区别是要害：那个是「暂时挂着**别人**的图」，
       * 这个是「挂着**自己**的图，只是画法不同」。
       */
      kind: 'raster'
      currentKey: string
      sourceKey: string
      svg: null
      manifest: Manifest
      render: PanelRender
    }
  | {
      kind: 'fallback'
      currentKey: string
      sourceKey: string
      svg: string
      manifest?: never
      render: PanelRender | undefined
    }
  | {
      kind: 'empty'
      currentKey: string
      sourceKey: null
      svg: null
      manifest?: never
      render: PanelRender | undefined
    }

/**
 * 面板此刻的显示视图。`exact` 只在「挂着的就是自己这一版、且它同时是几何
 * 权威」时给出——所以 `view.manifest` 拿得到就一定能写。
 */
export function panelDisplayView(
  state: Pick<RenderState, 'byKey' | 'latest'>,
  panel: PanelObject,
): PanelDisplayView {
  const currentKey = renderKeyOf(panel)
  const own = state.byKey[currentKey]
  const exact = exactOf(own, panel)
  if (exact?.svg) {
    return {
      kind: 'exact',
      currentKey,
      sourceKey: currentKey,
      svg: exact.svg,
      manifest: exact.manifest!,
      render: exact,
    }
  }
  if (exact && exact.preview.mode === 'raster') {
    // 没有 SVG **不等于**没画出来。这一档要是掉进下面的 fallback 分支，
    // 诊断里的 `display_variant` 会指向另一个变体，`display_exact` 报 false
    // ——画布上明明挂的就是这一版。
    return {
      kind: 'raster',
      currentKey,
      sourceKey: currentKey,
      svg: null,
      manifest: exact.manifest!,
      render: exact,
    }
  }
  const sourceKey = own?.svg ? currentKey : (state.latest[panel.fileId] ?? '')
  const prev = state.byKey[sourceKey]
  if (prev?.svg) {
    return { kind: 'fallback', currentKey, sourceKey, svg: prev.svg, render: own ?? prev }
  }
  return { kind: 'empty', currentKey, sourceKey: null, svg: null, render: own }
}

/**
 * 画布上**此刻挂着的**那一版 SVG 的键（与 panelDisplayView 严格同源）。
 * 预览平面靠它认领「我贴的这份预览是挂在哪一版 SVG 上的」——键一变，DOM
 * 节点就已经整个被换掉了，账本必须作废，否则还原会写到一批野引用上。
 */
export function activeRenderKey(
  state: Pick<RenderState, 'byKey' | 'latest'>,
  panel: PanelObject,
): string {
  const view = panelDisplayView(state, panel)
  return view.sourceKey ?? view.currentKey
}

/**
 * panelRender 的 hook 版；引用稳定（自己那份画好之后就是 store 里那个对象）。
 * 接受 null 是为了调用方不必为「还没选中面板」再套一层条件 hook。
 */
export function usePanelRender(panel: PanelObject | null | undefined): PanelRender | undefined {
  const own = useRenderStore((s) => (panel ? s.byKey[renderKeyOf(panel)] : undefined))
  const prev = useRenderStore((s) =>
    panel ? s.byKey[s.latest[panel.fileId] ?? ''] : undefined,
  )
  return useMemo(() => (own?.manifest ? own : mergeRender(own, prev)), [own, prev])
}

/**
 * **显示用** manifest：可能来自上一版或同文件另一个副本。
 * 只用于列元素 / 认 role / 画角标这类读操作。
 * 名字里的 `Display` 是刻意的——调用点上一眼就该看出这不是权威。
 * 要写几何（对齐、拖动、缩放、命中后改文档）一律用 `useExactPanelManifest`。
 */
export function usePanelDisplayManifest(panel: PanelObject | null | undefined): Manifest | null {
  return usePanelRender(panel)?.manifest ?? null
}

/**
 * `panelDisplayView` 的 hook 版。画布用它把「此刻挂的是哪一版、是不是精确的」
 * 落进 DOM（`data-display` / `data-display-key`）——这既是诊断线索，也是 e2e
 * 唯一能诚实断言「撤销之后画面真的换了」的观测点：撤销命中缓存时**根本不会
 * 发渲染请求**，拿 HTTP 往返当代理会把正确行为判成红。
 */
export function usePanelDisplayView(panel: PanelObject | null | undefined): PanelDisplayView | null {
  const byKey = useRenderStore((s) => s.byKey)
  const latest = useRenderStore((s) => s.latest)
  const variant = panel ? JSON.stringify(panel.overrides) : ''
  return useMemo(
    () => (panel ? panelDisplayView({ byKey, latest }, panel) : null),
    // variant 表达 overrides 的内容变化（panel 每次 commit 都是新引用）
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [byKey, latest, panel?.id, panel?.fileId, variant],
  )
}

/** 几何权威的渲染态；null = 现在不许做几何写操作 */
export function useExactPanelRender(panel: PanelObject | null | undefined): PanelRender | null {
  const own = useRenderStore((s) => (panel ? s.byKey[renderKeyOf(panel)] : undefined))
  const variant = panel ? JSON.stringify(panel.overrides) : ''
  return useMemo(
    () => (own?.manifest && !own.stale && own.lastPatches === variant ? own : null),
    [own, variant],
  )
}

/** 几何权威的 manifest；null = 正在同步，几何交互一律禁用 */
export function useExactPanelManifest(panel: PanelObject | null | undefined): Manifest | null {
  return useExactPanelRender(panel)?.manifest ?? null
}
