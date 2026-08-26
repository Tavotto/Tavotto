import { useEffect, useMemo, useRef, useState } from 'react'
import { t as translate } from '@/i18n'
import { enginePreviewPng, panelSrc } from '@/lib/api'
import { engineTransport } from '@/lib/engineTransport'
import { alignEntries, geomGid, geomTarget, segIntersectsRect } from '@/lib/elementGeom'
import { DURATION, prefersReducedMotion, usePresence } from '@/lib/motion'
import { geomHitsRect } from '@/lib/pathGeom'
import { pickBucket } from '@/lib/units'
import { cn } from '@/lib/utils'
import { shortHash } from '@/lib/authorityTrace'
import { isJustBakedBaseline } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useInteractionStore } from '@/store/interactionStore'
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

  // 编辑态用 SVG（要 gid 命中）；退出后有 override 的用引擎 PNG（imshow 面板不发糊）
  const svgHtml = editing ? (render?.svg ?? null) : null

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
  const useEnginePng = !editing && needsEngine && (render?.rev ?? 0) > 0
  const enginePng = useEnginePngBlob(obj, bucket, useEnginePng, render?.rev ?? 0)
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
  // 一个可寻址地址都拿不到时**退回这一版的权威 SVG**，绝不留一个空 src。
  // Codex 内嵌画布退出图内编辑后正好落在这一格：会话把文件标成 tracked
  // → `useEnginePng` 为真 → MCP 传输拒掉每一次 `previewPngUrl()`，而它的
  // `panelSrc()` 本来就回 null（iframe 里没有 HTTP 服务）。旧写法在这里
  // 解出空串，用户看到的是**图整个消失**。
  const inlineSvg = svgHtml ?? (src ? null : (render?.svg ?? null))
  const showSvg = inlineSvg != null

  return (
    <div
      className="absolute inset-0 overflow-hidden"
      // 此刻画布挂的是哪一版、是不是这一版自己的精确图。
      // exact = 图与文档同源；fallback = 暂时挂着上一张（几何交互已停摆）。
      // key 过短 hash：变体键里带文件名与 overrides 原文，不落进 DOM。
      data-display={displayView?.kind ?? 'empty'}
      data-display-key={shortHash(displayView?.sourceKey ?? null)}
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

      <RenderStatusBadge obj={obj} />
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
): string | null {
  const [url, setUrl] = useState<string | null>(null)
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
        setUrl(next)
      })
      .catch(() => {
        /* 失败保留上一张：渲染失败由角标表达，不该让画布空掉 */
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

  return url
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
        setHoverGid(hit?.gid ?? null)
        if (ref.current) {
          ref.current.style.cursor =
            hit?.draggable || hit?.resizable || hit?.arrow_endpoints ? 'move' : 'crosshair'
        }
      }}
      onPointerLeave={() => setHoverGid(null)}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        e.stopPropagation()
        const { fx, fy } = frac(e)
        const hit = pickElement(manifest, fx, fy, obj.lockedGids)
        const ui = useUiStore.getState()
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
    </div>
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

function RenderStatusBadge({ obj }: { obj: PanelObject }) {
  const render = usePanelRender(obj)
  // 冷启动/构建中是**文件级**的事实（一个 stem 一份 live figure），由 SSE 写；
  // 「这一份变体正在渲染」才是变体级的
  const building = useRenderStore((s) => s.building[obj.fileId])
  const editing = useUiStore((s) => s.elementPanelId === obj.id)
  const runtimeStatus = useRuntimeAssetStore((s) =>
    panelKind(obj) === 'runtime' ? s.byId[obj.fileId]?.status : undefined,
  )
  const zoom = useViewportStore((s) => s.zoom)

  // 角标画在世界层里，反向缩放保持屏幕上恒定大小
  const scale = 1 / zoom
  const runtimeBadge = runtimeStatus ? RUNTIME_BADGE[runtimeStatus] : undefined
  const relevant = editing || obj.overrides.length > 0 || render?.stale || !!runtimeBadge
  const info = useMemo(() => {
    if (!relevant) return null
    if (render?.status === 'rendering' || building) {
      return {
        tone: 'busy' as const,
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
      return { tone: 'error' as const, cold: false, text: badge('error') }
    }
    if (render?.stale) return { tone: 'stale' as const, cold: false, text: badge('stale') }
    // runtime 的 stale 语义（诚实文案：说「可能已变化」，不说「数据未变」）
    if (runtimeBadge) {
      return { tone: runtimeBadge.tone, cold: false, text: badge(runtimeBadge.key) }
    }
    return null
  }, [render, relevant, building, runtimeBadge])

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
              : 'bg-accent text-white',
        )}
      >
        {shown.tone === 'busy' && (
          <span className="h-2 w-2 animate-pulse rounded-full bg-white/80" />
        )}
        {shown.text}
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
