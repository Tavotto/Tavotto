import { useMemo } from 'react'
import { create } from 'zustand'
import { EngineError, engineRender, type Manifest } from '@/lib/api'
import { engineTransport } from '@/lib/engineTransport'
import { useAssetStore } from '@/store/assetStore'
import type { PanelObject } from '@/types/document'

export type RenderStatus = 'idle' | 'rendering' | 'ready' | 'error'

export interface PanelRender {
  /** 这份渲染态属于哪个素材文件（文件级操作靠它反查，见 markStale / reset） */
  fileId: string
  rev: number
  manifest: Manifest | null
  /** 已处理好的 SVG 文本（去掉 width/height，铺满容器） */
  svg: string | null
  status: RenderStatus
  error: string | null
  /**
   * 机器可读的失败原因。属于 ENVIRONMENT_CODES 的那几个是「缺件」而不是
   * 「脚本报错」，界面给出口而不是 traceback。
   */
  code: string
  /** code === 'missing_dependency' 时缺的那个包名 */
  module: string
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
  status: 'idle',
  error: null,
  code: '',
  module: '',
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
  /** 渲染并取回 SVG（与 manifest 同一响应）；同键渲染中重复调用只保留最后一次待办 */
  render: (fileId: string, patches: unknown[], previewDpi?: number) => Promise<void>
  /** 脚本变更：转入引擎跟踪并清掉该文件**全部变体**的 lastPatches */
  markStale: (fileIds: string[]) => void
  /** 丢掉某个文件的全部变体 */
  reset: (fileId: string) => void
  /**
   * 丢掉没人再引用的变体：`live` 是文档里现存面板的键集合。
   * 编辑期每改一个值就多一个变体条目（每条带一整份 SVG，imshow 面板能到
   * 几百 KB）——不清理的话一次长时间编辑就能把几百 MB 留在内存里。
   * 每个文件最近成功的那份与在途的那份永远保留（panelRender 的退路）。
   */
  prune: (live: Set<string>) => void
  /** 换项目：渲染态、跟踪表、在途账本一起归零 */
  clear: () => void
}

/** 每个变体一份在途状态：busy 时只记最后一次待办，避免连发把 worker 淹没 */
const inflight = new Map<
  string,
  { busy: boolean; queued: { patches: unknown[]; previewDpi?: number } | null }
>()

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

  render: async (fileId, patches, previewDpi) => {
    const key = renderKey(fileId, patches)
    const slot = inflight.get(key) ?? { busy: false, queued: null }
    inflight.set(key, slot)
    if (slot.busy) {
      // 同一变体的重复请求：只有 dpi 可能不同（patches 相同才是同一个键），
      // 排在后面的那次说了算——松手后的定稿渲染必须盖住拖动中的低清那次
      slot.queued = { patches, previewDpi }
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
          const next: Partial<PanelRender> = {
            fileId,
            rev: res.rev,
            manifest: res.manifest,
            status: 'ready',
            error: null,
            traceback: '',
            warnings: res.warnings ?? [],
            timings: res.timings ?? {},
            stale: false,
            lastPatches: JSON.stringify(current),
            previewDpi: dpi ?? null,
          }
          // 后端没给（老服务端）就保留上一版 SVG，别把画布刷成空白
          if (res.svg != null) next.svg = prepareSvg(res.svg)
          // 成功那一刻同时挪动该文件的「最近画好的那份」
          set((s) => ({
            byKey: { ...s.byKey, [key]: { ...(s.byKey[key] ?? EMPTY), ...next } },
            latest: { ...s.latest, [fileId]: key },
          }))
        } catch (err) {
          // 在途期间又排了新请求：直接跑最新那次，别停在旧请求的错误上
          // （否则 wantPatches 已等于新改动，同步器会永远跳过它）
          if (slot.queued != null) {
            current = slot.queued.patches
            dpi = slot.queued.previewDpi
            slot.queued = null
            continue
          }
          // 失败时保留旧 SVG，用户还能看到上一版
          patch(key, {
            fileId,
            status: 'error',
            code: err instanceof EngineError ? err.code : '',
            module: err instanceof EngineError ? err.module : '',
            error: timedOut
              ? `渲染超过 ${Math.round(timeoutMs / 60_000)} 分钟无响应，已断开请求；服务可能仍在后台运行，可稍后重试`
              : err instanceof Error
                ? err.message
                : String(err),
            traceback: err instanceof EngineError ? err.traceback : '',
          })
          return
        } finally {
          window.clearTimeout(watchdog)
        }
        if (slot.queued == null) break
        current = slot.queued.patches
        dpi = slot.queued.previewDpi
        slot.queued = null
      }
    } finally {
      slot.busy = false
    }
  },

  markStale: (fileIds) =>
    set((s) => {
      const byKey = { ...s.byKey }
      const tracked = { ...s.tracked }
      for (const id of fileIds) {
        // 该文件可能一个变体都还没渲染过：跟踪位记在文件级，
        // 否则同步器根本看不到「这个面板需要按新脚本重建」
        tracked[id] = true
        for (const [k, v] of Object.entries(byKey)) {
          if (v.fileId !== id) continue
          byKey[k] = { ...v, stale: true, lastPatches: null, wantPatches: null }
        }
      }
      return { byKey, tracked }
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
      delete tracked[fileId]
      delete latest[fileId]
      return { byKey, tracked, latest }
    }),

  prune: (live) => {
    const s = get()
    const keep = (key: string, v: PanelRender) =>
      live.has(key) ||
      s.latest[v.fileId] === key ||
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
    set({ byKey })
  },

  clear: () => {
    inflight.clear()
    set({ byKey: {}, tracked: {}, latest: {}, building: {} })
  },
}))

export const emptyRender = EMPTY

/**
 * 面板当前该显示/该用的渲染态。
 *
 * 优先自己那份变体；它还没画出来（刚改完值、或刚被 markStale 清掉）时，
 * **manifest / SVG / rev 退回该文件最近画好的那份**——状态类字段
 * （status/error/warnings/timings）一律取自己的，退回的只是「暂时先接着显示
 * 上一张」。不这么做的话每一次输入都会闪回磁盘原图。
 *
 * 退回的那份理论上可能是同文件另一个面板的变体（两个副本同时在渲染），
 * 但那只是一帧过渡，自己的渲染回来就被换掉；旧实现里「显示别人的图」
 * 是**稳态**，那才是要修的问题。
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
  return {
    ...own,
    manifest: own.manifest ?? prev.manifest,
    svg: own.svg ?? prev.svg,
    rev: own.rev || prev.rev,
  }
}

/**
 * 画布上**此刻挂着的**那一版 SVG 的键。
 *
 * 与 panelRender 的取舍严格同源：自己那份变体有 SVG 就是自己那份，否则退回
 * 该文件最近画好的那份（`latest`）。预览平面靠它认领「我贴的这份预览是挂在
 * 哪一版 SVG 上的」——键一变，DOM 节点就已经整个被换掉了，账本必须作废，
 * 否则还原会写到一批野引用上。
 */
export function activeRenderKey(
  state: Pick<RenderState, 'byKey' | 'latest'>,
  panel: PanelObject,
): string {
  const own = renderKeyOf(panel)
  if (state.byKey[own]?.svg) return own
  return state.latest[panel.fileId] ?? own
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

/** 只要 manifest 的场合（属性页 / 元素树 / 吸附）——写法统一，少一处 optional chain */
export function usePanelManifest(panel: PanelObject | null | undefined): Manifest | null {
  return usePanelRender(panel)?.manifest ?? null
}
