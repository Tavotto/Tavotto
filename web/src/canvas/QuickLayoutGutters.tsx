import { useMemo } from 'react'
import { gutters, quickLayoutValid } from '@/lib/quickLayoutGrid'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { licenseHas, licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { useQuickLayoutStore } from '@/store/quickLayoutActions'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { startGutterDrag } from './gutterDrag'

/** 分隔线命中区的屏幕宽度（px）：看不见的细条，悬停才显出一根 1 px 线 */
const HIT_PX = 8

/**
 * 一键排版之后行 / 列之间的分隔线命中区（高级功能 `quick-layout`；没解锁时什么都不画）。
 * 只在记忆有效（`quickLayoutValid`）、选择工具空闲时挂；平时完全看不见，悬停一根 1 px 线 + 缩放光标，没有文字。
 * 拖动中画淡色的新格轮廓（`useQuickLayoutStore.preview`，松手后等方案期间留着）。命中区在选框覆盖层之下。
 */
export function QuickLayoutGutters() {
  const status = useLicenseStore((s) => s.status)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const spaceDown = useViewportStore((s) => s.spaceDown)
  const memo = useDocumentStore((s) => s.doc.quickLayout)
  const objects = useDocumentStore((s) => s.doc.objects)
  const tool = useUiStore((s) => s.tool)
  const cropTargetId = useUiStore((s) => s.cropTargetId)
  const inFigure = useUiStore((s) => s.elementPanelId != null)
  const kind = useInteractionStore((s) => s.kind)
  const busy = useQuickLayoutStore((s) => s.busy)
  const preview = useQuickLayoutStore((s) => s.preview)
  const valid = quickLayoutValid(memo, objects)
  const list = useMemo(() => (valid && memo ? gutters(memo) : []), [valid, memo])
  if (!licenseVisible(status) || !licenseHas(status, 'quick-layout') || inFigure) return null
  // 预览只在记忆还有效时画（等方案期间切了画布 / 项目，记忆对不上，轮廓一并不画）
  if (!valid) return null
  const idle = tool === 'select' && !spaceDown && cropTargetId == null && kind === 'none' && !busy
  const px = (mm: number) => mmToWorld(mm) * zoom

  return (
    <div data-quick-layout-gutters className="pointer-events-none absolute inset-0 overflow-hidden">
      {idle &&
        list.map((g) => {
          const vertical = g.axis === 'col'
          const style = vertical
            ? { left: panX + px(g.pos) - HIT_PX / 2, top: panY + px(g.from), width: HIT_PX, height: px(g.to - g.from) }
            : { left: panX + px(g.from), top: panY + px(g.pos) - HIT_PX / 2, width: px(g.to - g.from), height: HIT_PX }
          return (
            <div
              key={`${g.axis}-${g.index}`}
              data-quick-layout-gutter={`${g.axis}-${g.index}`}
              className={cn('group pointer-events-auto absolute', vertical ? 'cursor-col-resize' : 'cursor-row-resize')}
              style={style}
              onPointerDown={(e) => startGutterDrag(e, g)}
            >
              <span
                aria-hidden
                className={cn(
                  'absolute bg-sel opacity-0 group-hover:opacity-100',
                  vertical ? 'inset-y-0 left-1/2 w-px -translate-x-1/2' : 'inset-x-0 top-1/2 h-px -translate-y-1/2',
                )}
              />
            </div>
          )
        })}
      {preview &&
        Object.entries(preview).map(([id, b]) => (
          <div
            key={id}
            data-quick-layout-preview={id}
            className="pointer-events-none absolute border border-sel opacity-60"
            style={{ left: panX + px(b[0]), top: panY + px(b[1]), width: px(b[2]), height: px(b[3]) }}
          />
        ))}
    </div>
  )
}
