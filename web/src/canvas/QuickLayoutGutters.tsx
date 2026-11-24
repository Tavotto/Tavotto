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
/** 握柄：一颗 18 px 的瓷白小圆钮（屏幕 px，不随缩放变，样式见 index.css `.ql-knob`）；分隔线比它加两头留白还短就不画 */
const GRIP = 18
const GRIP_MIN_STRIP = GRIP + 8

/** 圆钮里两枚向外的细箭头（列分隔线左右；行分隔线整颗转 90°） */
function Chevrons() {
  return (
    <svg viewBox="0 0 14 14" width={10} height={10} overflow="visible">
      <path className="ql-knob-l" d="M5 3.5 L2 7 L5 10.5" />
      <path className="ql-knob-r" d="M9 3.5 L12 7 L9 10.5" />
    </svg>
  )
}

/** 读数里的一个尺寸：数字 + 淡一档的单位 */
function Mm({ v, unit }: { v: number; unit: string }) {
  return (
    <>
      {(Math.round(v * 10) / 10).toFixed(1)}
      <span className="ql-readout-unit">{unit}</span>
    </>
  )
}

/**
 * 一键排版之后行 / 列之间的分隔线命中区（高级功能 `quick-layout`；没解锁时什么都不画）。
 * 只在记忆有效（`quickLayoutValid`）、选择工具空闲时挂。鼠标在页面上（或选中了这份排版里的图）时，每条分隔线中点淡入一颗淡色握柄（没条件时仍挂着但透明）
 * （`data-quick-layout-grip`，只提示「这里能拖」，在命中区里面、不另吃点击）；悬停命中区：握柄变选中蓝 + 一根 1 px 线 + 缩放光标，
 * 原生气泡「拖动调整比例」，没有别的文字。握柄是瓷白小圆钮 + 两枚向外的细箭头（维护者 2026-11-21 从 D3 三版里选「瓷白」）：
 * 悬停边线变蓝、微微抬起、箭头张开；拖动中另画一颗按下态的钮跟着分隔线走，旁边一枚浅色小胶囊读出两侧的毫米数。
 * 拖动中图本身跟手（`quickLayoutFollow`），同时每张图都画淡蓝的新格轮廓（`useQuickLayoutStore.preview`，松手后等方案期间留着；维护者 2026-11-20 要求框要在）。命中区在选框覆盖层之下。
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
  const drag = useQuickLayoutStore((s) => s.drag)
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
              className={cn('ql-gutter group pointer-events-auto absolute', vertical ? 'cursor-col-resize' : 'cursor-row-resize')}
              style={style}
              title={t('quickLayout.gutterTip')}
              onPointerDown={(e) => startGutterDrag(e, g, along)}
            >
              {px(bandEnd - g.from) >= GRIP_MIN_STRIP && (
                <span
                  aria-hidden
                  data-quick-layout-grip={showGrips ? 'shown' : 'hidden'}
                  className={cn('ql-knob pointer-events-none absolute', !vertical && 'ql-knob-row', !showGrips && 'opacity-0')}
                  style={vertical ? { left: '50%', top: `${along * 100}%` } : { top: '50%', left: `${along * 100}%` }}
                >
                  <Chevrons />
                </span>
              )}
              <span
                aria-hidden
                className={cn(
                  'absolute bg-sel opacity-0 transition-opacity group-hover:opacity-40',
                  vertical ? 'inset-y-0 left-1/2 w-px -translate-x-1/2' : 'inset-x-0 top-1/2 h-px -translate-y-1/2',
                )}
              />
            </div>
          )
        })}
      {drag && <DragKnob drag={drag} px={px} panX={panX} panY={panY} />}
      {preview &&
        // 拖动中每张图都画淡蓝预览框（图本身也跟手；维护者 2026-11-20：框要在，一眼看出新格子）
        Object.entries(preview)
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

/** 拖动中那条分隔线：按下态的圆钮跟着走，旁边浅色小胶囊读出两侧的宽（高）。不吃指针（拖动由 trackPointer 接管） */
function DragKnob({
  drag,
  px,
  panX,
  panY,
}: {
  drag: NonNullable<ReturnType<typeof useQuickLayoutStore.getState>['drag']>
  px: (mm: number) => number
  panX: number
  panY: number
}) {
  const { t } = useTranslation('workspace')
  const unit = t('stage.rulerUnit')
  const vertical = drag.axis === 'col'
  const at = drag.from + (drag.to - drag.from) * drag.along
  const x = panX + px(vertical ? drag.pos : at)
  const y = panY + px(vertical ? at : drag.pos)
  return (
    <>
      <span
        aria-hidden
        className={cn('pointer-events-none absolute bg-sel', vertical ? 'w-px -translate-x-1/2' : 'h-px -translate-y-1/2')}
        style={
          vertical
            ? { left: x, top: panY + px(drag.from), height: px(drag.to - drag.from) }
            : { top: y, left: panX + px(drag.from), width: px(drag.to - drag.from) }
        }
      />
      <span
        aria-hidden
        data-quick-layout-drag-grip
        className={cn('ql-knob ql-knob-active pointer-events-none absolute', !vertical && 'ql-knob-row')}
        style={{ left: x, top: y }}
      >
        <Chevrons />
      </span>
      <span
        data-quick-layout-readout
        className={cn('ql-readout pointer-events-none absolute', vertical ? 'ql-readout-col' : 'ql-readout-row')}
        style={{ left: x, top: y }}
      >
        <Mm v={drag.sizes[0]} unit={unit} />
        <span aria-hidden className="ql-readout-sep" />
        <Mm v={drag.sizes[1]} unit={unit} />
      </span>
    </>
  )
}
