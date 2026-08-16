import { useMemo, useRef } from 'react'
import { enginePngUrl, panelSrc } from '@/lib/api'
import { alignEntries, geomGid, geomTarget } from '@/lib/elementGeom'
import { pickBucket } from '@/lib/units'
import { cn } from '@/lib/utils'
import { isJustBakedBaseline } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import type { PanelObject, PanelRotation } from '@/types/document'
import { panelFullSize, panelRotation, rotationSwaps, unrotateVec } from '@/types/document'
import {
  pickElement,
  startAxesDrag,
  startElementDrag,
  startElementGroupMove,
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
export function PanelView({ obj }: { obj: PanelObject }) {
  const zoom = useViewportStore((s) => s.zoom)
  // 「写回原始文件」后 mtime 变化 → URL 变化 → 画布上已放置的同源面板自动重取
  const mtime = useAssetStore((s) => s.byId[obj.fileId]?.mtime)
  const editing = useUiStore((s) => s.elementPanelId === obj.id)
  const render = useRenderStore((s) => s.byFile[obj.fileId])
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
  const showSvg = !!render?.svg && editing
  // 有图内修改、或脚本已领先磁盘文件时，显示都必须走引擎产物。
  // 只带基线的面板除外——磁盘文件已经是那个样子，继续用 /api/render 更省。
  const needsEngine =
    (obj.overrides.length > 0 && !isJustBakedBaseline(obj)) || !!render?.tracked
  const useEnginePng = !editing && needsEngine && (render?.rev ?? 0) > 0
  const src = useEnginePng
    ? enginePngUrl(obj.fileId, bucket, render!.rev)
    : panelSrc(obj.fileId, obj.fileKind, bucket, mtime)

  return (
    <div className="absolute inset-0 overflow-hidden">
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
            dangerouslySetInnerHTML={{ __html: render!.svg! }}
          />
        ) : (
          <img
            src={src}
            alt={obj.name ?? obj.fileId}
            draggable={false}
            className="absolute select-none"
            style={{ ...layout, maxWidth: 'none' }}
          />
        )}

        {editing && <ElementHitLayer obj={obj} layout={layout} rot={rot} />}
      </div>

      <RenderStatusBadge obj={obj} />
    </div>
  )
}

type Layout = { width: number; height: number; left: number; top: number }

/**
 * 编辑态的透明命中层：用 manifest bbox 做命中测试（面积小者优先、
 * axes 降权），不依赖 SVG 内部结构。
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
  const manifest = useRenderStore((s) => s.byFile[obj.fileId]?.manifest)
  const setHoverGid = useInteractionStore((s) => s.setHoverGid)
  const ref = useRef<HTMLDivElement>(null)

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

  return (
    <div
      ref={ref}
      className="absolute"
      style={{ ...layout, cursor: 'crosshair' }}
      onPointerMove={(e) => {
        if (useInteractionStore.getState().kind !== 'none') return
        const { fx, fy } = frac(e)
        const hit = pickElement(manifest, fx, fy, obj.lockedGids)
        setHoverGid(hit?.gid ?? null)
        if (ref.current) {
          ref.current.style.cursor = hit?.draggable || hit?.resizable ? 'move' : 'crosshair'
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
    />
  )
}

/** 渲染中 / 冷启动 / 失败 / 过期 的角标 */
function RenderStatusBadge({ obj }: { obj: PanelObject }) {
  const render = useRenderStore((s) => s.byFile[obj.fileId])
  const editing = useUiStore((s) => s.elementPanelId === obj.id)
  const zoom = useViewportStore((s) => s.zoom)

  // 角标画在世界层里，反向缩放保持屏幕上恒定大小
  const scale = 1 / zoom
  const relevant = editing || obj.overrides.length > 0 || render?.stale
  const info = useMemo(() => {
    if (!render || !relevant) return null
    if (render.status === 'rendering') {
      return {
        tone: 'busy' as const,
        text: render.cold
          ? render.cost === 'heavy'
            ? '冷启动中，可能需要几分钟…'
            : '首次构建中…'
          : '渲染中…',
      }
    }
    if (render.status === 'error') return { tone: 'error' as const, text: '渲染失败' }
    if (render.stale) return { tone: 'stale' as const, text: '脚本已更新' }
    return null
  }, [render, relevant])

  if (!info) return null

  return (
    <div
      className="pointer-events-none absolute left-0 top-0 origin-top-left p-1"
      style={{ transform: `scale(${scale})` }}
    >
      <span
        className={cn(
          'flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs',
          info.tone === 'error'
            ? 'bg-danger text-white'
            : info.tone === 'stale'
              ? 'bg-ink text-white'
              : 'bg-accent text-white',
        )}
      >
        {info.tone === 'busy' && (
          <span className="h-2 w-2 animate-pulse rounded-full bg-white/80" />
        )}
        {info.text}
      </span>
    </div>
  )
}
