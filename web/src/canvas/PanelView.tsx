import { useEffect, useMemo, useRef, useState } from 'react'
import { t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { enginePreviewPng, panelSrc, type ManifestElement } from '@/lib/api'
import { engineTransport } from '@/lib/engineTransport'
import { alignEntries, geomGid, geomTarget, segIntersectsRect } from '@/lib/elementGeom'
import { DURATION, prefersReducedMotion, usePresence } from '@/lib/motion'
import { geomHitsRect } from '@/lib/pathGeom'
import { pickBucket } from '@/lib/units'
import { cn } from '@/lib/utils'
import {
  pickSpineZone,
  readAxesTickModel,
  toggleSidePlan,
  zoneRectFrac,
  zoneWidthsFor,
  type SidePlan,
  type SpineGeom,
  type SpineSide,
  type SpineZone,
  type ZoneWidths,
} from '@/lib/tickSides'
import { diagnosticHash } from '@/diagnostics'
import { applyTickSidePlan, isJustBakedBaseline } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useInteractionStore } from '@/store/interactionStore'
import { nativePanelState, useNativeSessionStore } from '@/store/nativeSessionStore'
import {
  renderKeyOf,
  useExactPanelManifest,
  usePanelDisplayView,
  usePanelRender,
  useRenderStore,
} from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { reattachPreview, settleFailedAuthority } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import type { PanelObject, PanelRotation } from '@/types/document'
import {
  panelFullSize,
  panelKind,
  panelRotation,
  rotationSwaps,
  unrotateVec,
} from '@/types/document'
import {
  isElementHidden,
  pickElement,
  startArrowDrag,
  startAxesDrag,
  startElementDrag,
  startElementGroupMove,
  trackPointer,
} from './interactions'
import { openQuickEdit } from './quickEditStore'

/**
 * 面板显示：
 * - 普通面板 → 矢量走分档 PNG（档位只升不降），位图走原文件
 * - 有图内修改 / 正在编辑 → 内联引擎 SVG，所见即所得
 * 裁剪用「放大的图 + overflow hidden」实现，不改动源文件。
 *
 * 旋转：对象的 x/y/w/h 是旋转后的页面落位，内容层按未旋转尺寸铺好、
 * 居中后整体 CSS rotate；90/270 时两者长宽互换，正好填满包围盒。
 */
/** 面板角标的文案（workspace:panelBadge.*） */
const badge = (key: string) => translate(`panelBadge.${key}`, { ns: 'workspace' })

export function PanelView({ obj }: { obj: PanelObject }) {
  const zoom = useViewportStore((s) => s.zoom)
  // 「写回原始文件」后 mtime 变化 → URL 变化 → 画布上已放置的同源面板自动重取
  const mtime = useAssetStore((s) => s.byId[obj.fileId]?.mtime)
  // 基线有效性在会话中翻转也要能唤醒本组件：下面 `needsEngine` 读的
  // `isJustBakedBaseline` 走 getState（不是订阅），而 `/api/panels` 的 mtime
  // 是**整数秒**——外部重写落在同一秒（或保留 mtime 的同步工具）时上面那条
  // 订阅接不到；该变体若已有 exact 渲染，useEngineSync 那一轮 syncEngine 又
  // 零 state 变更。缺这条订阅，画布会把被重写的磁盘原图当基线继续挂着。
  // 只为触发重渲染取值；判据本体仍只有 isJustBakedBaseline 一份。
  const bakedCurrent = useAssetStore((s) => s.byId[obj.fileId]?.baked_current)
  void bakedCurrent
  const editing = useUiStore((s) => s.elementPanelId === obj.id)
  // 自己那份变体的渲染态（同文件的另一个副本有它自己的一份，互不相干）
  const render = usePanelRender(obj)
  const displayView = usePanelDisplayView(obj)
  const bucketRef = useRef(0)

  const crop = obj.crop
  const rot = panelRotation(obj)
  const boxW = mmToWorld(obj.w)
  const boxH = mmToWorld(obj.h)
  // 内容（未旋转）占的世界像素；90/270 与包围盒互换
  const contentW = rotationSwaps(rot) ? boxH : boxW
  const contentH = rotationSwaps(rot) ? boxW : boxH
  // 裁剪后画布上只露一部分，整图仍按未裁剪尺寸摆放
  const layout = crop
    ? {
        width: contentW / crop.w,
        height: contentH / crop.h,
        left: -(crop.x / crop.w) * contentW,
        top: -(crop.y / crop.h) * contentH,
      }
    : { width: contentW, height: contentH, left: 0, top: 0 }

  const dpr = window.devicePixelRatio || 1
  // 取图档位按整图（未裁剪、未旋转）的显示宽度算
  const needed = panelFullSize(obj).w * mmToWorld(1) * zoom * dpr
  const bucket = Math.max(bucketRef.current, pickBucket(needed))
  bucketRef.current = bucket

  // 这一版该用哪种预览表示法（ADR 0022）。老后端不返回 `preview` 时是
  // `vector`，下面每一处的行为与从前逐字节相同。
  //
  // **`preview?.` 里那个问号不是防御性冗余，是这条协议的另一半。** 类型上它
  // 是必填，但类型只活在编译期：任何人 `setState` 一个裸 `PanelRender`
  // （老用例、老持久化状态、跨版本的 store）都能造出没有它的对象，而
  // `render?.preview.mode` 只保护 `render`——那时它不是"按 vector 解读"，
  // 是当场 TypeError。实测撞见过。
  const rasterPreview = render?.preview?.mode === 'raster'
  // **这一版没有可显示的矢量 payload**——两个成因，同一条显示策略（位图）：
  //   * `raster`  引擎按硬闸决定不把那份 SVG 读进内存（ADR 0022 不变量 3）；
  //   * `evicted` 这一版画出来过，但它的 SVG 被 renderStore 的字节预算清掉了
  //     （`SVG_RECENT_BUDGET_*`，issue #181 Session 04）。
  // 两种都**不是渲染失败**：manifest 在，命中层在，几何权威仍是 exact manifest。
  // `evicted` 走 displayView 而不是 `render`，因为它只在「这一版就是几何权威」
  // 时成立——退回显示别的变体时那条路是 fallback，与本档无关。
  const bitmapOnly = rasterPreview || displayView?.kind === 'evicted'
  // 编辑态用 SVG（要 gid 命中）；退出后有 override 的用引擎 PNG（imshow 面板不发糊）。
  // 命中层不受影响，见下面的 ElementHitLayer：位图只是画法，几何权威仍是
  // exact manifest（不变量 4）。
  const svgHtml = editing && !bitmapOnly ? (render?.svg ?? null) : null

  // 预览平面与权威 SVG 的接合点：内联 SVG 每换一次就来认领一次。
  // 换上来的正是等的那一版 → 预览功成身退（DOM 已经整个换掉）；还是原来那一版
  // （React 把同一份 SVG 重新插了一遍：面板重挂、标签页切回来）→ 把挂起的预览
  // 重放上去，否则用户刚拖完的元素会凭空弹回原位。判断收在 svgPreviewStore。
  //
  // 依赖只认 svgHtml 与 obj.id，**不能带整个 obj**：obj 每次 commit 都是新引用，
  // 带上它等于每写一条 override 就重放一次预览——而重放会重新采 base，
  // 那时 DOM 上还挂着预览位移，采到的 base 就是「已经挪过的位置」，位移翻倍。
  const panelId = obj.id
  useEffect(() => {
    if (svgHtml == null) return
    const st = useRenderStore.getState()
    const cur = st.byKey[renderKeyOf(obj)]?.svg ? renderKeyOf(obj) : (st.latest[obj.fileId] ?? '')
    reattachPreview(panelId, cur)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [svgHtml, panelId])

  // 权威渲染失败：等不到那一版 SVG 了，会话就地收尾。
  // **预览留在画布上**——文档里已经是用户要的值，把预览撤掉会让画布与属性页
  // 各说各话；渲染失败本身由角标表达，用户可以继续编辑或重试。
  const renderStatus = render?.status
  useEffect(() => {
    if (renderStatus === 'error') settleFailedAuthority(panelId)
  }, [renderStatus, panelId])
  // 有图内修改、或脚本已领先磁盘文件时，显示都必须走引擎产物。
  // 只带基线的面板除外——磁盘文件已经是那个样子，继续用 /api/render 更省。
  // runtime 面板（ADR 0013）没有磁盘文件：本会话跑过（rev>0）就走引擎产物。
  const kind = panelKind(obj)
  const runtime = kind === 'runtime'
  const tracked = useRenderStore((s) => !!s.tracked[obj.fileId])
  const needsEngine =
    (obj.overrides.length > 0 && !isJustBakedBaseline(obj)) || tracked || runtime
  // raster 档下**编辑态也要位图**——否则画布上什么都没有。复用的是同一条
  // PNG 链路（按 patches 出图、状态中立、AbortController + objectURL 生命周期），
  // 不另写第二套。
  const useEnginePng = (bitmapOnly ? editing || needsEngine : !editing && needsEngine) &&
    (render?.rev ?? 0) > 0
  const pngBlob = useEnginePngBlob(obj, bucket, useEnginePng, render?.rev ?? 0)
  const variantNow = JSON.stringify(obj.overrides)
  // 位图这一格挂什么：当前变体的那张最好；**上一变体的那张只在新图还在路上时
  // 暂挂**（与 Phase F 的 latest 显示退路同一条纪律）。取图一旦失败，就不能再
  // 拿上一变体的位图冒充当前变体——那正是「预览 ≠ 当前 overrides 且不吵」。
  const enginePng =
    pngBlob.url && (pngBlob.variant === variantNow || !pngBlob.failed) ? pngBlob.url : null
  // runtime 面板的 stale / cache 状态（只查询，绝不触发脚本执行）
  const runtimeState = useRuntimeAssetStore((s) => (runtime ? s.byId[obj.fileId] : undefined))
  useEffect(() => {
    if (runtime) useRuntimeAssetStore.getState().ensure(obj)
    // ensure 幂等且只认 fileId/source；obj 每次 commit 都是新引用，不能进依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runtime, obj.fileId])
  // 权威渲染的结果反哺 stale 判定：画成功 = 此刻它就是当前脚本的样子；
  // 失败（本会话跑过又失败）= rerun_failed（该状态的唯一 producer）
  useEffect(() => {
    if (!runtime) return
    const st = useRuntimeAssetStore.getState()
    if (renderStatus === 'ready') st.markFresh(obj.fileId)
    else if (renderStatus === 'error') st.markRerunFailed(obj.fileId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runtime, renderStatus, obj.fileId])
  // 替代传输给不出可寻址地址时（Codex 内嵌画布里没有 HTTP 服务）退回空串，
  // 此时显示走 SVG——绝不留一个连不上的 URL 让画布挂一个碎图标。
  // runtime 面板只有 cache 里确有预览才给 URL（404 的碎图标比占位符糟）；
  // 未知形态（更新版本文档里的新 fileKind）fail closed：不发任何请求。
  const transport = engineTransport()
  const fileSrc =
    kind === 'unknown' || (runtime && !runtimeState?.cached)
      ? null
      : transport
        ? transport.panelSrc(obj.fileId, kind, bucket, mtime)
        : panelSrc(obj.fileId, kind, bucket, mtime)
  const src = (useEnginePng && enginePng) || fileSrc || ''
  // 引擎位图还没落地（首次渲染刚回来、blob 在路上）或取图失败时，**优先挂
  // 引擎 SVG，而不是退回磁盘原图**：store 里那份 SVG 就是按 overrides 画出来
  // 的（自己这版，或 Phase F 的 latest 退路），磁盘原图才是「脚本原值」——
  // 面板有图内修改时拿它当预览，就是用户报的「预览没有使用 override」。
  // `bitmapOnly`（raster / evicted）没有矢量 payload，不在此列。
  const engineSvgStandby =
    needsEngine && !bitmapOnly && !(useEnginePng && enginePng) ? (render?.svg ?? null) : null
  // 一个可寻址地址都拿不到时**退回这一版的权威 SVG**，绝不留一个空 src。
  // Codex 内嵌画布退出图内编辑后正好落在这一格：会话把文件标成 tracked
  // → `useEnginePng` 为真 → MCP 传输拒掉每一次 `previewPngUrl()`，而它的
  // `panelSrc()` 本来就回 null（iframe 里没有 HTTP 服务）。旧写法在这里
  // 解出空串，用户看到的是**图整个消失**。
  // 没有矢量 payload 的那两档除外：`raster` 刻意没有 SVG，`evicted` 的那份刚被
  // 内存预算清掉——这条兜底都不许把**上一版**的矢量图拿来冒充（前者正是硬闸要
  // 拦的 payload，后者会让画布显示另一个变体的图）。
  const inlineSvg =
    svgHtml ?? engineSvgStandby ?? (src || bitmapOnly ? null : (render?.svg ?? null))
  const showSvg = inlineSvg != null
  // 面板需要引擎产物（有图内修改 / 脚本领先 / runtime），画布上挂的却是磁盘
  // 原图——这一格必须与「近似预览」同级地诚实说出来（web/AGENTS.md：不许无
  // 提示地拿磁盘原图冒充当前视觉状态）。渲染中 / 失败由既有角标压过本条；
  // runtime 面板另有自己的 stale 语义角标，不在此重复。
  const approxPreview =
    !runtime && needsEngine && !showSvg && !(useEnginePng && enginePng) && !!fileSrc

  return (
    <div
      className="absolute inset-0 overflow-hidden"
      // 此刻画布挂的是哪一版、是不是这一版自己的精确图。
      // exact = 图与文档同源；fallback = 暂时挂着上一张（几何交互已停摆）。
      // key 过短 hash：变体键里带文件名与 overrides 原文，不落进 DOM。
      data-display={displayView?.kind ?? 'empty'}
      data-display-key={diagnosticHash(displayView?.sourceKey ?? null)}
    >
      <div
        className="absolute overflow-hidden"
        style={{
          width: contentW,
          height: contentH,
          left: (boxW - contentW) / 2,
          top: (boxH - contentH) / 2,
          // transform 从右往左应用：先在内容空间翻转，再旋转落位
          transform:
            [
              rot ? `rotate(${rot}deg)` : '',
              obj.flipH || obj.flipV
                ? `scale(${obj.flipH ? -1 : 1}, ${obj.flipV ? -1 : 1})`
                : '',
            ]
              .filter(Boolean)
              .join(' ') || undefined,
          opacity: obj.opacity ?? undefined,
        }}
      >
        {showSvg ? (
          <div
            data-element-svg={obj.id}
            className="absolute"
            style={{ ...layout, maxWidth: 'none' }}
            dangerouslySetInnerHTML={{ __html: inlineSvg }}
          />
        ) : src ? (
          <CrossfadeImage
            src={src}
            alt={obj.name ?? obj.fileId}
            className="absolute select-none"
            style={{ ...layout, maxWidth: 'none' }}
          />
        ) : (
          // runtime 面板还没有可显示的产物（cache 未物化 / 已清理），或
          // 未知素材形态：诚实的占位，而不是碎图标或另一个面板的图
          <RuntimePlaceholder obj={obj} layout={layout} />
        )}

        {editing && <ElementHitLayer obj={obj} layout={layout} rot={rot} />}
      </div>

      <RenderStatusBadge obj={obj} approx={approxPreview} />
    </div>
  )
}

/**
 * 引擎位图：**按本面板自己的 overrides** 现出（POST /api/engine/preview_png）。
 *
 * 旧路径是 `<img src=/api/engine/png>`，那个端点从 live figure 直接出图，而
 * live 状态永远只是「最后渲染的那个变体」——画布上放两个同文件不同修改的
 * 副本时，后渲染的那个会把像素喂给前一个。要带上整份 patches 就发不了 GET，
 * 于是改成 fetch blob + objectURL。
 *
 * 新图到位之前一直挂着上一张（失败也保留）：中途置空会让画布闪一下磁盘原图。
 */
function useEnginePngBlob(
  obj: PanelObject,
  bucket: number,
  enabled: boolean,
  rev: number,
): { url: string | null; variant: string | null; failed: boolean } {
  // `variant` 记的是 `url` 那张图**按哪组 overrides**出的：消费方靠它分辨
  // 「暂挂的上一张」与「就是当前这版」。`failed` = 最近一次取图以失败告终
  // （被新请求顶掉的中断不算）——此后上一张不再冒充当前变体，退位给 SVG /
  // 「近似预览」角标，而不是安静地一直挂着。
  const [state, setState] = useState<{
    url: string | null
    variant: string | null
    failed: boolean
  }>({ url: null, variant: null, failed: false })
  const urlRef = useRef<string | null>(null)
  // 依赖用变体串而不是 overrides 数组：数组每次 commit 都是新引用
  const variant = JSON.stringify(obj.overrides)
  const { fileId, overrides } = obj

  useEffect(() => {
    if (!enabled) return
    const ctrl = new AbortController()
    let landed = false
    const transport = engineTransport()
    const pending = transport
      ? transport.previewPngUrl(fileId, overrides, bucket, ctrl.signal)
      : enginePreviewPng(fileId, overrides, bucket, ctrl.signal).then((blob) =>
          URL.createObjectURL(blob),
        )
    void pending
      .then((next) => {
        landed = true
        if (urlRef.current) URL.revokeObjectURL(urlRef.current)
        urlRef.current = next
        setState({ url: next, variant, failed: false })
      })
      .catch(() => {
        // 失败保留上一张（画布别空掉），但要记下「失败」：中断（deps 变了 /
        // 卸载，signal.aborted）不是失败，真失败才置位
        if (!ctrl.signal.aborted) setState((s) => ({ ...s, failed: true }))
      })
    return () => {
      if (!landed) ctrl.abort()
    }
    // overrides 的内容变化由 variant 表达（数组引用每次 commit 都变）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, fileId, variant, bucket, rev])

  // 卸载时把最后一张还回去，否则每个被删/被切走的面板都留一块 blob
  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current)
  }, [])

  return state
}

/**
 * 面板换图的交叉淡入。
 *
 * 引擎每出一版新图就换一次 src，硬换会让画布「啪」地闪一下——连续调参时
 * 每次松手都闪一回。这里让**旧的那一帧**压在新图之上淡出，把硬切遮掉。
 *
 * 两个不能改的实现约束：
 *
 * 1. **两个图层常驻、轮流当主角，绝不为淡出层现建 `<img>`。**
 *    引擎位图是 blob: URL，`useEnginePngBlob` 在新 URL 到手的同一刻就
 *    `revokeObjectURL` 掉了旧的——这时候现建一个 `<img>` 指过去只会加载失败、
 *    露出一块空白，比不做淡入淡出更糟。而**已经解码过**的那个 `<img>` 节点
 *    即使 URL 已失效照样画得出来，所以旧图层必须还是原来那个节点。
 * 2. **当前图层的 src 永远直接跟着最新值走，不等加载。**
 *    浏览器换 src 时会一直画着旧的一帧直到新图解码完成，底下这张不会空白；
 *    也因此这里不需要任何「等 onLoad 再切」的状态机——那种写法在图加载失败、
 *    或 jsdom 这类根本不加载图片的环境里会永久卡在旧图上。
 *
 * 淡出层 `alt=""` + `aria-hidden`：任何时刻都只有一张图带真实 alt。
 * reduced-motion 下不进入淡出态，行为与从前逐字节一致。
 */
function CrossfadeImage({
  src,
  alt,
  className,
  style,
}: {
  src: string
  alt: string
  className?: string
  style?: React.CSSProperties
}) {
  const [layers, setLayers] = useState<{ a?: string; b?: string; cur: 'a' | 'b' }>({
    a: src,
    cur: 'a',
  })
  const [fading, setFading] = useState(false)
  const seen = useRef(src)

  // 依赖只有 src。带上 layers / cur 的话它们自己的 setState 会让 effect 重跑，
  // 重跑的 cleanup 会把定时器清掉，淡出态就再也退不出来
  useEffect(() => {
    if (src === seen.current) return
    seen.current = src
    setLayers((l) => (l.cur === 'a' ? { ...l, b: src, cur: 'b' } : { ...l, a: src, cur: 'a' }))
    if (prefersReducedMotion()) return
    setFading(true)
    const t = setTimeout(() => setFading(false), DURATION.slow)
    return () => clearTimeout(t)
  }, [src])

  // 非当前层排在后面 = 画在上面（同层同栈时 DOM 顺序决定叠放）。
  // key 固定为 'a'/'b'，重排只是移动节点，不重挂、不丢已解码的那一帧
  const order: ('a' | 'b')[] = layers.cur === 'a' ? ['a', 'b'] : ['b', 'a']

  return (
    <>
      {order.map((k) => {
        const layerSrc = layers[k]
        // 还没用过的那一层不渲染：<img> 不给 src 会去请求当前页面地址
        if (!layerSrc) return null
        const isCur = k === layers.cur
        return (
          <img
            key={k}
            src={layerSrc}
            alt={isCur ? alt : ''}
            aria-hidden={isCur ? undefined : true}
            draggable={false}
            className={cn(
              className,
              !isCur && 'pointer-events-none',
              !isCur && fading && 'animate-crossfade-out',
            )}
            style={isCur || fading ? style : { ...style, opacity: 0 }}
          />
        )
      })}
    </>
  )
}

type Layout = { width: number; height: number; left: number; top: number }

/**
 * runtime 面板还没有任何可显示产物时的占位（重开后 cache 被清 / 从未物化 /
 * 未知素材形态）。一块中性的虚线框 + 脚本名——诚实说明「这张图由脚本生成、
 * 还没在本机跑」，双击进入编辑即触发 lazy build（现有交互，不另设按钮）。
 */
function RuntimePlaceholder({ obj, layout }: { obj: PanelObject; layout: Layout }) {
  const script = obj.source?.script ?? obj.script ?? ''
  const rp = (key: string, values?: Record<string, unknown>) =>
    translate(`runtimePanel.${key}`, { ns: 'workspace', ...(values ?? {}) })
  return (
    <div
      className="absolute flex flex-col items-center justify-center gap-1 rounded-sm border border-dashed border-ink/25 bg-ink/[0.03] p-2 text-center"
      style={{ ...layout, maxWidth: 'none' }}
    >
      <span className="text-xs text-ink-3">{rp('placeholder')}</span>
      {script && (
        <span className="max-w-full truncate font-mono text-[10px] text-ink/40" title={script}>
          {script}
        </span>
      )}
      <span className="text-[10px] text-ink/40">{rp('placeholderHint', { script })}</span>
    </div>
  )
}

/**
 * 编辑态的透明命中层：用 manifest bbox 做命中测试（面积小者优先、
 * axes 降权），不依赖 SVG 内部结构。
 *
 * **命中必须打在几何权威上**（issue #131）。命中不是只读的：命中完就是拖动、
 * 就是整组平移、就是框选出一个待对齐的选区——全都以那份 bbox 起算。退回来的
 * 上一版 manifest 会让「点到的」和「看到的」是两个元素，拖出来的位移也以旧
 * 锚点起算。权威没就位时这一层整个停摆（不 hover、不命中、不框选），画布
 * 照常显示上一张图，属性页给出「正在同步」的说明。
 */
function ElementHitLayer({
  obj,
  layout,
  rot,
}: {
  obj: PanelObject
  layout: Layout
  rot: PanelRotation
}) {
  const manifest = useExactPanelManifest(obj)
  const setHoverGid = useInteractionStore((s) => s.setHoverGid)
  const zoom = useViewportStore((s) => s.zoom)
  const ref = useRef<HTMLDivElement>(null)
  /** 框选带（本层局部 px；世界层随 zoom 缩放，画框时线宽反除保持 1 屏幕 px） */
  const [band, setBand] = useState<{ l: number; t: number; w: number; h: number } | null>(null)
  /** 指针悬在某条边框的内 / 外侧命中带上（Prompt 16）：高亮 + 说明 + 点击即切 */
  const [spineHover, setSpineHover] = useState<SpineHover | null>(null)

  /** 一个分数单位对应的屏幕像素：命中带按屏幕像素定宽，zoom 变了带不变 */
  const zoneScale = { pxPerFracX: layout.width * zoom, pxPerFracY: layout.height * zoom }

  /**
   * 边框命中区只在 `pickElement` 命中 figure（图外空白、偏出去的边框）或那条边
   * 所属的子图本身（含铺满它的位图）时才算：文字 / 曲线 / 别的子图 / 刻度文字
   * 永远高优先级——它们才是用户点到的东西。
   */
  const spineZoneUnder = (
    fx: number,
    fy: number,
    pointerType: string | undefined,
    picked: ManifestElement | null,
  ): SpineHover | null => {
    const widths = zoneWidthsFor(pointerType)
    const allow = (gid: string) =>
      !picked || picked.gid === 'figure' || picked.gid === gid || picked.geom_gid === gid
    const pick = pickSpineZone(manifest, fx, fy, zoneScale, widths, allow)
    if (!pick) return null
    const model = readAxesTickModel(manifest, obj.overrides, pick.gid)
    const state = model?.sides[pick.hit.side]
    if (!model || !state) return null
    const zone = pick.hit.zone
    const plan = zone === 'neutral' ? null : toggleSidePlan(model, pick.hit.side, zone)
    if (zone !== 'neutral' && !plan) return null
    const spinesOf = manifest!.elements.find((e) => e.gid === pick.gid)!.spines!
    return {
      gid: pick.gid,
      side: pick.hit.side,
      zone,
      geom: pick.geom,
      plan,
      widths,
      on: zone === 'inner' ? state.inward : zone === 'outer' ? state.outward : state.visible,
      coupledGeoms: (plan?.effect.coupled ?? [])
        .map((sd) => ({ side: sd, geom: spinesOf[sd] }))
        .filter((c): c is { side: SpineSide; geom: SpineGeom } => !!c.geom),
    }
  }

  /**
   * 屏幕点 → 内容分数坐标。旋转后 getBoundingClientRect 给的是轴对齐外框，
   * 90/270 时它的长宽正是内容长宽的互换，所以绕中心反向旋转即可还原。
   */
  const frac = (e: { clientX: number; clientY: number }) => {
    const r = ref.current!.getBoundingClientRect()
    const w = rotationSwaps(rot) ? r.height : r.width
    const h = rotationSwaps(rot) ? r.width : r.height
    const [u, v] = unrotateVec(
      e.clientX - (r.left + r.width / 2),
      e.clientY - (r.top + r.height / 2),
      rot,
    )
    return { fx: u / w + 0.5, fy: v / h + 0.5 }
  }

  /**
   * 图内元素框选：从空白处（命中 figure）按住拖出选择带，框到的元素整组选中。
   * 容器（axes/axes3d）的 bbox 盖着整块绘图区，相交即选会让任何框选都混进
   * 宿主子图，所以容器要求**整个落进框里**才入选；其余元素相交即选。
   * shift 起手 = 加选（并入既有选区）；没拖动就是普通点空白，回落到 figure。
   */
  const startBandSelect = (e: React.PointerEvent) => {
    const additive = e.shiftKey
    const ui = useUiStore.getState()
    const base = additive ? [...ui.selectedGids] : []
    const origin = frac(e)
    useInteractionStore.getState().begin('marquee')

    trackPointer(e, {
      onMove: (ev) => {
        const cur = frac(ev)
        const r = {
          x: Math.min(origin.fx, cur.fx),
          y: Math.min(origin.fy, cur.fy),
          w: Math.abs(cur.fx - origin.fx),
          h: Math.abs(cur.fy - origin.fy),
        }
        setBand({
          l: r.x * layout.width,
          t: r.y * layout.height,
          w: r.w * layout.width,
          h: r.h * layout.height,
        })
        if (!manifest) return
        const hits = manifest.elements
          .filter((el) => {
            if (el.gid === 'figure' || isElementHidden(el)) return false
            if (obj.lockedGids?.includes(el.gid)) return false
            // 图内独立箭头按线本身与框选带相交，不用一大块空白 bbox
            if (el.arrow_endpoints && el.arrow_endpoints.length >= 2) {
              return segIntersectsRect(el.arrow_endpoints[0], el.arrow_endpoints[1], r)
            }
            // 有真实路径的（曲线 / 填充 / 独立形状）按**路径**与框相交，同理：
            // 一条 U 形曲线的 bbox 中间那块全是空白，框在那儿不该圈中它
            if (el.geometry) return geomHitsRect(el.geometry, r)
            const [bx, by, bw, bh] = el.bbox
            if (el.role === 'axes' || el.role === 'axes3d') {
              return bx >= r.x && by >= r.y && bx + bw <= r.x + r.w && by + bh <= r.y + r.h
            }
            return bx < r.x + r.w && bx + bw > r.x && by < r.y + r.h && by + bh > r.y
          })
          .map((el) => el.gid)
        ui.setSelectedGids([...new Set([...base, ...hits])])
      },
      onEnd: (moved) => {
        useInteractionStore.getState().end()
        setBand(null)
        if (!moved && !additive) ui.setSelectedGid('figure')
      },
    })
  }

  // 权威没就位：留着这一层占位（布局不跳），但不接任何指针事件。
  // 选区不动——等精确 manifest 回来，框会自己回到正确位置。
  if (!manifest) {
    return (
      <div
        className="pointer-events-none absolute"
        style={{ ...layout, cursor: 'progress' }}
        data-authority="syncing"
      />
    )
  }

  return (
    <div
      ref={ref}
      className="absolute"
      style={{ ...layout, cursor: 'crosshair' }}
      data-authority="ready"
      onPointerMove={(e) => {
        if (useInteractionStore.getState().kind !== 'none') return
        const { fx, fy } = frac(e)
        const hit = pickElement(manifest, fx, fy, obj.lockedGids)
        const zone = spineZoneUnder(fx, fy, e.pointerType, hit)
        setSpineHover((prev) => (sameSpineHover(prev, zone) ? prev : zone))
        // 悬在边框带上时高亮的是那条边所属的子图（偏出去的边框 pickElement 命中
        // 的是 figure，不补这一步用户看不出「这条线是谁的」）
        setHoverGid(zone?.gid ?? hit?.gid ?? null)
        if (ref.current) {
          ref.current.style.cursor =
            zone && zone.zone !== 'neutral'
              ? 'pointer'
              : hit?.draggable || hit?.resizable || hit?.arrow_endpoints
                ? 'move'
                : 'crosshair'
        }
      }}
      onPointerLeave={() => {
        setHoverGid(null)
        setSpineHover(null)
      }}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        e.stopPropagation()
        const { fx, fy } = frac(e)
        const hit = pickElement(manifest, fx, fy, obj.lockedGids)
        const ui = useUiStore.getState()
        // 边框的内 / 外侧命中带：一次点击 = 切这一边的向内 / 向外刻度（一条历史）。
        // 选中落到那条边所属的子图上（刻度卡随之出现、状态同源）；已经选着它或
        // 它的刻度组时不动选区。中线（neutral）不切刻度，走下面的普通选中。
        const zone = spineZoneUnder(fx, fy, e.pointerType, hit)
        if (zone && zone.zone !== 'neutral' && zone.plan) {
          applyTickSidePlan(obj.id, zone.plan)
          const sel = ui.selectedGids.length === 1 ? ui.selectedGids[0] : null
          const keep = sel === zone.gid || sel?.startsWith(`${zone.gid}.`)
          if (!keep) ui.setSelectedGid(zone.gid)
          setSpineHover(null)
          return
        }
        if (zone && zone.zone === 'neutral' && (!hit || hit.gid === 'figure')) {
          // 偏出去的边框线本身：点它选中它的子图（框内那条本来就会命中子图）
          ui.setSelectedGid(zone.gid)
          return
        }
        // shift 加选放开到任何具体元素（曲线、柱形系列、误差棒都要能多选，
        // 批量改颜色/线宽靠它）。figure 除外——它是兜底命中，混进多选没有意义。
        // 加选不挑几何能力：对齐与整组平移那边由 alignEntries 自行过滤，
        // 非几何元素进了选区也不会搅乱它们。
        if (e.shiftKey && hit && hit.gid !== 'figure') {
          ui.toggleSelectedGid(hit.gid)
          return
        }
        // 空白处（兜底命中 figure）按下 → 拖出框选带；点一下不拖仍是选中 figure
        if (!hit || hit.gid === 'figure') {
          startBandSelect(e)
          return
        }
        // 拖多选里的任一成员 = 整组平移，且不改动选择（与画布层多选拖动一致）
        if (hit && manifest && ui.selectedGids.length > 1) {
          const entries = alignEntries(obj, manifest, ui.selectedGids)
          if (entries.length > 1 && entries.some((en) => en.key === geomGid(hit))) {
            startElementGroupMove(e, obj, entries, layout)
            return
          }
        }
        // 点已经在多选里的成员不收敛选区（与画布对象层的 ObjectView 一致）：
        // 「框好一组再挨个确认」是常见动作，点一下就把批量表单收掉等于惩罚确认。
        // 收窄多选仍有退路——点图内空白命中 figure 即可。
        const keepSelection = !!hit && ui.selectedGids.length > 1 && ui.selectedGids.includes(hit.gid)
        if (!keepSelection) ui.setSelectedGid(hit?.gid ?? 'figure')
        // 保持选区归保持选区，该拖的照样拖：位图没有自己的几何属性，
        // 拖它等于拖宿主子图
        if (hit?.resizable) startAxesDrag(e, obj, geomTarget(manifest, hit), layout, 'move')
        else if (hit?.arrow_endpoints) startArrowDrag(e, obj, hit, layout, 'both')
        else if (hit?.draggable && hit.anchor) startElementDrag(e, obj, hit, layout)
      }}
      onContextMenu={(e) => {
        e.preventDefault()
        e.stopPropagation()
        const { fx, fy } = frac(e)
        const gid = pickElement(manifest, fx, fy, obj.lockedGids)?.gid ?? 'figure'
        const ui = useUiStore.getState()
        // 已在多选里就保持多选（属性页跟着它走），否则先选中再弹
        if (!ui.selectedGids.includes(gid)) ui.setSelectedGid(gid)
        openQuickEdit({ kind: 'element', panelId: obj.id, gid }, e)
      }}
      onDoubleClick={(e) => {
        // 始终拦下：不能让外层 ObjectView 的双击把编辑态切成裁剪/重进编辑
        e.stopPropagation()
        const { fx, fy } = frac(e)
        const hit = pickElement(manifest, fx, fy, obj.lockedGids)
        // 双击带文字内容的元素 = 快速改字：弹层聚焦内容输入框
        if (!hit?.editable.some((f) => f.prop === 'text' && f.type === 'text')) return
        useUiStore.getState().setSelectedGid(hit.gid)
        openQuickEdit({ kind: 'element', panelId: obj.id, gid: hit.gid, focusText: true }, e)
      }}
    >
      {band && (
        <div
          className="pointer-events-none absolute"
          style={{
            left: band.l,
            top: band.t,
            width: band.w,
            height: band.h,
            // 本层随世界 zoom 缩放，线宽反除才是屏幕上恒定的 1px
            border: `${1 / zoom}px dashed var(--color-accent)`,
            background: 'color-mix(in srgb, var(--color-accent) 6%, transparent)',
          }}
        />
      )}
      {spineHover && spineHover.zone !== 'neutral' && (
        <SpineZoneFeedback hover={spineHover} layout={layout} zoom={zoom} rot={rot} />
      )}
    </div>
  )
}

/** 指针悬在边框命中带上的那一刻：谁的、哪条边、哪个带、点下去会发生什么 */
interface SpineHover {
  gid: string
  side: SpineSide
  zone: SpineZone
  geom: SpineGeom
  plan: SidePlan | null
  widths: ZoneWidths
  /** 这个带对应的刻度此刻开着没有（neutral 时是这一边显不显示） */
  on: boolean
  /** 方向那一步会连带改到的同轴另一边 */
  coupledGeoms: { side: SpineSide; geom: SpineGeom }[]
}

/**
 * 状态文字相对带中点的位移（屏幕像素系，随 1/zoom 一起缩放）。**往框里推**：
 * 往外推会撞上刻度文字那一排、再往外就出了面板的裁剪框（面板内容
 * overflow hidden），实测下边的文字整个被裁掉。框里只在 hover 那一刻盖住一点
 * 数据，指针一走就没了。
 */
const LABEL_SHIFT: Record<SpineSide, string> = {
  top: 'translate(-50%, 90%)',
  bottom: 'translate(-50%, -130%)',
  left: 'translate(28px, -50%)',
  right: 'translate(calc(-100% - 28px), -50%)',
}

const sameSpineHover = (a: SpineHover | null, b: SpineHover | null) =>
  a === b ||
  (!!a &&
    !!b &&
    a.gid === b.gid &&
    a.side === b.side &&
    a.zone === b.zone &&
    a.on === b.on &&
    a.coupledGeoms.length === b.coupledGeoms.length)

const spineTip = (key: string, values?: Record<string, unknown>) =>
  translate(`spineZone.${key}`, { ns: 'workspace', ...(values ?? {}) })

/**
 * 边框命中带的 hover 反馈（Prompt 16 §四）：
 *   * 将被控制的那条带高亮（实心），方向那一步连带的同轴另一边浅色一起亮
 *     ——matplotlib 的方向是整条轴的，装作每边独立就是骗人；
 *   * 一行状态文字说清「哪边 · 向内 / 向外 · 现在开着 / 关着 · 点击会怎样」，
 *     不只靠 cursor；文字随面板旋转反转回来，180° 的面板上也读得正；
 *   * 没有任何过渡动画（reduced motion 下也不会闪），只随指针出现 / 消失；
 *   * 只在 hover 期间存在，不常驻遮挡图形。
 */
function SpineZoneFeedback({
  hover,
  layout,
  zoom,
  rot,
}: {
  hover: SpineHover
  layout: Layout
  zoom: number
  rot: PanelRotation
}) {
  const scale = { pxPerFracX: layout.width * zoom, pxPerFracY: layout.height * zoom }
  const zone = hover.zone as 'inner' | 'outer'
  const strip = (side: SpineSide, geom: SpineGeom, strong: boolean) => {
    const r = zoneRectFrac(side, geom, zone, scale, hover.widths)
    return (
      <div
        key={side}
        data-spine-zone={side}
        data-spine-zone-kind={zone}
        data-spine-zone-strong={strong ? 'true' : 'false'}
        className="pointer-events-none absolute"
        style={{
          left: r.x * layout.width,
          top: r.y * layout.height,
          width: r.w * layout.width,
          height: r.h * layout.height,
          background: strong
            ? 'color-mix(in srgb, var(--color-accent) 28%, transparent)'
            : 'color-mix(in srgb, var(--color-accent) 12%, transparent)',
          outline: strong ? `${1 / zoom}px solid var(--color-accent)` : undefined,
        }}
      />
    )
  }
  const e = hover.plan?.effect
  const sideName = translate(`tick.side.${hover.side}`, { ns: 'inspector' })
  const dirName = translate(`tick.dir.${zone === 'inner' ? 'in' : 'out'}`, { ns: 'inspector' })
  const state = spineTip(hover.on ? 'stateOn' : 'stateOff')
  const action = e
    ? spineTip(e.hides ? 'willHide' : e.on ? 'willOn' : 'willOff')
    : ''
  const coupled = e?.coupled.length
    ? spineTip('coupled', {
        sides: listJoin(e.coupled.map((sd) => translate(`tick.side.${sd}`, { ns: 'inspector' }))),
      })
    : ''
  const text = spineTip('label', { side: sideName, dir: dirName, state, action })
  // 文字锚在这条带的中点外侧一点；随面板旋转反转回来，并按 1/zoom 缩放保持字号
  const r = zoneRectFrac(hover.side, hover.geom, zone, scale, hover.widths)
  const cx = (r.x + r.w / 2) * layout.width
  const cy = (r.y + r.h / 2) * layout.height
  return (
    <>
      {strip(hover.side, hover.geom, true)}
      {hover.coupledGeoms.map((c) => strip(c.side, c.geom, false))}
      <div
        role="status"
        data-spine-zone-label={hover.side}
        className={cn(
          'pointer-events-none absolute z-10 whitespace-nowrap rounded-sm border border-border bg-surface px-1.5 py-0.5',
          'text-[11px] leading-4 text-ink shadow-sm',
        )}
        style={{
          left: cx,
          top: cy,
          transform: `${LABEL_SHIFT[hover.side]} rotate(${-rot}deg) scale(${1 / zoom})`,
          transformOrigin: 'center',
        }}
      >
        {text}
        {coupled ? <span className="text-ink-3"> {coupled}</span> : null}
      </div>
    </>
  )
}

/** 渲染中 / 冷启动 / 失败 / 过期 的角标 */
/** runtime stale 状态 → 角标文案键与色调（fresh 不出角标） */
const RUNTIME_BADGE: Record<string, { key: string; tone: 'error' | 'stale' }> = {
  possibly_stale: { key: 'runtimePossiblyStale', tone: 'stale' },
  missing_source: { key: 'runtimeMissingSource', tone: 'error' },
  missing_environment: { key: 'runtimeMissingEnvironment', tone: 'error' },
  needs_rerun: { key: 'runtimeNeedsRerun', tone: 'stale' },
  rerun_failed: { key: 'runtimeRerunFailed', tone: 'error' },
}

/**
 * 这张图与 `tavotto run` 会话的关系（ADR 0021 §9）——**只在需要说话时说话**。
 *
 * 判据本体在 `nativeSessionStore.nativePanelState`（那里有完整的四格表与
 * 用例）；这里只是把两个 store 的读取接上去。
 *
 * 「脚本正在运行」与「会话已结束」都是**在用户动手之前**说的。不说的话，他
 * 点进图内编辑得到的是一条 409（`native_session_not_at_barrier` /
 * `native_session_offline`），而那两句话描述的是正常状态、不是故障——用错误
 * 弹窗讲正常状态最劝退。
 */
function useNativePanelState(obj: PanelObject): 'running' | 'offline' | null {
  const profile = useRuntimeAssetStore((s) =>
    panelKind(obj) === 'runtime' ? s.byId[obj.fileId]?.profile : undefined,
  )
  const sessions = useNativeSessionStore((s) => s.sessions)
  return nativePanelState(sessions, obj.fileId, profile)
}

/** 角标的内容。`hint` 只有需要解释的那一档才有（tooltip） */
type BadgeInfo = {
  tone: 'busy' | 'error' | 'stale' | 'info'
  cold: boolean
  text: string
  hint?: string
}

function RenderStatusBadge({ obj, approx = false }: { obj: PanelObject; approx?: boolean }) {
  const render = usePanelRender(obj)
  // 冷启动/构建中是**文件级**的事实（一个 stem 一份 live figure），由 SSE 写；
  // 「这一份变体正在渲染」才是变体级的
  const building = useRenderStore((s) => s.building[obj.fileId])
  const editing = useUiStore((s) => s.elementPanelId === obj.id)
  const runtimeStatus = useRuntimeAssetStore((s) =>
    panelKind(obj) === 'runtime' ? s.byId[obj.fileId]?.status : undefined,
  )
  const nativeState = useNativePanelState(obj)
  const zoom = useViewportStore((s) => s.zoom)

  // 角标画在世界层里，反向缩放保持屏幕上恒定大小
  const scale = 1 / zoom
  const runtimeBadge = runtimeStatus ? RUNTIME_BADGE[runtimeStatus] : undefined
  // 低内存编辑预览（ADR 0022）只在**编辑态**说一次：退出编辑后画布本来就走
  // 位图，没什么可解释的。
  const rasterEditing = editing && render?.preview?.mode === 'raster'
  const relevant =
    editing ||
    obj.overrides.length > 0 ||
    render?.stale ||
    !!runtimeBadge ||
    !!nativeState ||
    rasterEditing ||
    approx
  const info = useMemo((): BadgeInfo | null => {
    if (!relevant) return null
    if (render?.status === 'rendering' || building) {
      return {
        tone: 'busy',
        cold: !!building?.cold,
        text: badge(
          building?.cold
            ? building.cost === 'heavy'
              ? 'cold'
              : 'firstBuild'
            : 'rendering',
        ),
      }
    }
    if (render?.status === 'error') {
      return { tone: 'error', cold: false, text: badge('error') }
    }
    // **阻塞性的压过信息性的。** native 的两句说的是「现在不能编辑」，而
    // `stale`（脚本已更新）/ runtime 的 stale 语义说的是「内容可能不是最新」
    // ——后者不妨碍用户动手，前者妨碍。把 `stale` 排在前面的表现是：native
    // 会话跑着的时候用户看到「脚本已更新」，以为可以重新渲染，点进去撞一条
    // 409；而那时真正该告诉他的是「停下来才能编辑」。
    //
    // 这一档也压过下面 raster 的低内存预览角标（那条同样是信息性的），
    // **同一条理由**：`'running'` 与 `'offline'` 都会让图内编辑撞 409
    // （`_NATIVE_STATUS` 把两个码都映射成 409，`enginesession.resolve()`
    // 在 profile=native、无活会话时直接抛），解锁动作不同但都失败。
    if (nativeState) {
      return {
        tone: 'stale',
        cold: false,
        text: badge(nativeState === 'running' ? 'nativeRunning' : 'nativeOffline'),
      }
    }
    if (render?.stale) return { tone: 'stale', cold: false, text: badge('stale') }
    // 画布上挂的还是磁盘原图，而这个面板的图内修改要求引擎产物（引擎图没
    // 落地 / 取图失败）——与布局版本预览的「近似预览」同一等级的诚实表达：
    // 不吵、可忽略，但**必须说出来**，否则用户以为看到的就是自己的修改。
    // 渲染中 / 失败已被上面的 busy / error 压过，这里只兜「静默挂着原图」那档。
    if (approx) {
      return {
        tone: 'info',
        cold: false,
        text: badge('approxPreview'),
        hint: badge('approxPreviewHint'),
      }
    }
    // **不弹对话框、不责怪用户**：这是我们主动做出的一个显示决定，用户什么
    // 都没做错，而且导出质量一点没变（不变量 2）——所以是一枚可以忽略的
    // 角标 + 一句 tooltip，不是一次打断。
    if (rasterEditing) {
      return {
        tone: 'info',
        cold: false,
        text: badge('memoryEfficientPreview'),
        hint: badge('memoryEfficientPreviewHint'),
      }
    }
    // runtime 的 stale 语义（诚实文案：说「可能已变化」，不说「数据未变」）
    if (runtimeBadge) {
      return { tone: runtimeBadge.tone, cold: false, text: badge(runtimeBadge.key) }
    }
    return null
  }, [render, relevant, building, runtimeBadge, nativeState, rasterEditing, approx])

  // 退场那 90ms 里 info 已经是 null 了，留住最后一版才播得完
  const last = useRef(info)
  useEffect(() => {
    if (info) last.current = info
  }, [info])
  const { mounted, state } = usePresence(!!info, DURATION.exit)
  const shown = info ?? last.current

  if (!mounted || !shown) return null

  return (
    <div
      data-state={state}
      className={cn(
        'pointer-events-none absolute left-0 top-0 origin-top-left p-1',
        'data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out',
      )}
      style={{ transform: `scale(${scale})` }}
    >
      <span
        className={cn(
          'relative flex items-center gap-1 overflow-hidden rounded-sm px-1.5 py-0.5 text-xs',
          shown.tone === 'error'
            ? 'bg-danger text-white'
            : shown.tone === 'stale'
              ? 'bg-ink text-white'
              : shown.tone === 'info'
                ? 'bg-ink/70 text-white'
                : 'bg-accent text-white',
        )}
      >
        {shown.tone === 'busy' && (
          <span className="h-2 w-2 animate-pulse rounded-full bg-white/80" />
        )}
        {shown.text}
        {/* 只有这一小块接指针事件。整枚角标收回指针事件是不行的：外层刻意是
            `pointer-events-none`（角标画在面板左上角，图内标题常常就在那儿），
            而 raster 那一档的角标**整个编辑期间常驻**——89×19 的一块死区会让
            用户点不到自己的标题。实测撞见过。 */}
        {shown.hint && (
          <span
            title={shown.hint}
            aria-label={shown.hint}
            className="pointer-events-auto cursor-help font-bold opacity-80"
          >
            ⓘ
          </span>
        )}
        {/* 冷启动可能要几分钟：一个呼吸的圆点表达不出「还在动」，补一条来回扫的
            不确定进度条。**不做百分比**——worker 那边根本没有进度可报，
            假进度条比没有更坏。 */}
        {shown.cold && (
          <span aria-hidden className="absolute inset-x-0 bottom-0 h-0.5">
            <span className="block h-full w-1/4 rounded-full bg-white/75 animate-sweep" />
          </span>
        )}
      </span>
    </div>
  )
}
