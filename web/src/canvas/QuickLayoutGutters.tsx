import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { gutters, quickLayoutValid } from '@/lib/quickLayoutGrid'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useInteractionStore } from '@/store/interactionStore'
import { licenseHas, licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { useQuickLayoutStore } from '@/store/quickLayoutActions'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { startGutterDrag } from './gutterDrag'

/** 分隔线命中区的屏幕宽度（px）：看不见的细条，悬停才显出一根 1 px 线 */
const HIT_PX = 8
/** 握柄：分隔线中点的一颗小圆角条（屏幕 px，不随缩放变）；分隔线比它加两头留白还短就不画 */
const GRIP_LONG = 18
const GRIP_SHORT = 3
const GRIP_MIN_STRIP = GRIP_LONG + 8

/**
 * 一键排版之后行 / 列之间的分隔线命中区（高级功能 `quick-layout`；没解锁时什么都不画）。
 * 只在记忆有效（`quickLayoutValid`）、选择工具空闲时挂。鼠标在页面上（或选中了这份排版里的图）时，每条分隔线中点淡入一颗淡色握柄（没条件时仍挂着但透明）
 * （`data-quick-layout-grip`，只提示「这里能拖」，在命中区里面、不另吃点击）；悬停命中区：握柄变选中蓝 + 一根 1 px 线 + 缩放光标，
 * 原生气泡「拖动调整比例」，没有别的文字。
 * 拖动中图本身跟手（`quickLayoutFollow`）；没跟手的图（没挂上画面）画淡色的新格轮廓（`useQuickLayoutStore.preview`，松手后等方案期间留着）。命中区在选框覆盖层之下。
 */
export function QuickLayoutGutters() {
  const status = useLicenseStore((s) => s.status)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const spaceDown = useViewportStore((s) => s.spaceDown)
  const { t } = useTranslation('workspace')
  const memo = useDocumentStore((s) => s.doc.quickLayout)
  const objects = useDocumentStore((s) => s.doc.objects)
  const tool = useUiStore((s) => s.tool)
  const cropTargetId = useUiStore((s) => s.cropTargetId)
  const inFigure = useUiStore((s) => s.elementPanelId != null)
  const kind = useInteractionStore((s) => s.kind)
  const busy = useQuickLayoutStore((s) => s.busy)
  const preview = useQuickLayoutStore((s) => s.preview)
  const live = useQuickLayoutStore((s) => s.live)
  const page = useDocumentStore((s) => s.doc.page)
  // 鼠标在页面上：指针坐标（mm，按帧节流）落在页面矩形里。只订阅布尔，指针在页内移动不重渲染
  const overPage = useInteractionStore((s) =>
    s.cursor ? s.cursor.x >= 0 && s.cursor.y >= 0 && s.cursor.x <= page.w && s.cursor.y <= page.h : false,
  )
  const selectedInLayout = useSelectionStore((s) => !!memo && s.ids.some((id) => id in memo.cells))
  const valid = quickLayoutValid(memo, objects)
  const list = useMemo(() => (valid && memo ? gutters(memo) : []), [valid, memo])
  if (!licenseVisible(status) || !licenseHas(status, 'quick-layout') || inFigure) return null
  // 预览只在记忆还有效时画（等方案期间切了画布 / 项目，记忆对不上，轮廓一并不画）
  if (!valid) return null
  const idle = tool === 'select' && !spaceDown && cropTargetId == null && kind === 'none' && !busy
  const px = (mm: number) => mmToWorld(mm) * zoom
  const showGrips = overPage || selectedInLayout

  return (
    <div data-quick-layout-gutters className="pointer-events-none absolute inset-0 overflow-hidden">
      {idle &&
        list.map((g) => {
          const vertical = g.axis === 'col'
          // 握柄落在分隔线第一段（第一行 / 第一列）的中点：多行多列时两个方向的分隔线中点会重合成一个十字，错开
          const cross = list.filter((o) => o.axis !== g.axis).reduce<number | null>((m, o) => (m === null || o.pos < m ? o.pos : m), null)
          const bandEnd = cross !== null && cross > g.from && cross < g.to ? cross : g.to
          const along = ((g.from + bandEnd) / 2 - g.from) / (g.to - g.from)
          const style = vertical
            ? { left: panX + px(g.pos) - HIT_PX / 2, top: panY + px(g.from), width: HIT_PX, height: px(g.to - g.from) }
            : { left: panX + px(g.from), top: panY + px(g.pos) - HIT_PX / 2, width: px(g.to - g.from), height: HIT_PX }
          return (
            <div
              key={`${g.axis}-${g.index}`}
              data-quick-layout-gutter={`${g.axis}-${g.index}`}
              className={cn('group pointer-events-auto absolute', vertical ? 'cursor-col-resize' : 'cursor-row-resize')}
              style={style}
              title={t('quickLayout.gutterTip')}
              onPointerDown={(e) => startGutterDrag(e, g)}
            >
              {px(bandEnd - g.from) >= GRIP_MIN_STRIP && (
                <span
                  aria-hidden
                  data-quick-layout-grip={showGrips ? 'shown' : 'hidden'}
                  className={cn(
                    // 握柄是画在纸上的记号：用纸上的墨（两套主题同值，不跟界面 ink 变浅）；悬停换成选中蓝
                    'pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--color-paper-ink-3)]',
                    'transition-opacity duration-fast motion-reduce:transition-none',
                    showGrips ? 'opacity-60 group-hover:bg-sel group-hover:opacity-100' : 'opacity-0',
                  )}
                  style={
                    vertical
                      ? { width: GRIP_SHORT, height: GRIP_LONG, left: '50%', top: `${along * 100}%` }
                      : { width: GRIP_LONG, height: GRIP_SHORT, top: '50%', left: `${along * 100}%` }
                  }
                />
              )}
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
        Object.entries(preview)
          .filter(([id]) => !live?.includes(id))
          .map(([id, b]) => (
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
