/**
 * 一张画布的缩略图 —— 页面比例里按对象落位画**真实内容**。
 *
 * 面板挂素材库同一张预览图（`/api/render`，runtime 面板用 materialized cache
 * 预览），文字画文字，标注画轮廓。不新起渲染、不冒充导出结果——它回答的是
 * 「这是哪一张版」（审计 T05）。
 *
 * 两个消费者共用这一份：
 *   - 画布列表（`left/CanvasList.tsx`）：喂的是内存里的 `CanvasObject`；
 *   - 版本列表（`VersionDialog.tsx`）：喂的是列表端点发来的**草图**
 *     （`LayoutVersionMeta.sketch`，见 `app.py` 的 `_version_sketch`）。
 *
 * 所以喂给它的是 `types/thumb.ts` 的 `ThumbObject` —— 这两种输入的**公共最小
 * 形状**，而不是文档模型的别名。`CanvasObject` 结构上满足它，两边不需要任何
 * 转换层。
 *
 * **缩略图画的是当前磁盘上的素材，不是那一版当时的图内修改**（overrides 出图
 * 是每张一次 matplotlib 往返，一行缩略图付不起）。所以它回答「哪一版」，不
 * 回答「那一版长什么样」——后者是版本详情里的 `LayoutSnapshot`，它会在出不来
 * 时明确标「近似预览」。
 */
import { renderUrl, runtimePreviewUrl } from '@/lib/api'
import { useRetryingSrc } from '@/lib/imgRetry'
import { cn } from '@/lib/utils'
import { useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { bucketFor } from '@/lib/previewBuckets'
import type { ThumbObject, ThumbPage } from '@/types/thumb'

/**
 * 一张 56×40 的缩略图里最多画几个对象 —— **这个数字只有这一处**。
 *
 * 版本列表把它随请求发给后端（`?sketch=`），后端据此裁草图：把它在 Python 里
 * 再写一遍就是同一条规则的第二份权威，而两份权威只会在有人改了其中一份的
 * 那天才被发现（那天的表现是「版本缩略图比画布缩略图少画几个对象」，没有
 * 任何东西会报错）。
 */
export const THUMB_OBJECT_LIMIT = 40
/** 缩略图里一段文字最多画几个字（同上，只有这一处）。 */
export const THUMB_TEXT_CHARS = 24

/** 默认盒子（`h-10 w-14`）：页面矩形的圆角按它折成用户单位 */
const BOX_W = 56
const BOX_H = 40
/** 页面矩形四周留的边（占长边的比例）：页面边线不贴着盒子边被裁掉一半 */
const PAD = 0.04

export function CanvasThumb({
  page,
  objects,
  className,
  transparent = false,
  flush = false,
  pxWidth,
}: {
  page: ThumbPage
  objects: readonly ThumbObject[]
  /** 尺寸由调用方给，默认与画布列表一致 */
  className?: string
  /**
   * 页面画成棋盘格（导出勾了透明背景时用）：只换页面底色，别的一概不动。
   * 默认 false 时 DOM 与不带这个 prop 之前逐字相同（画布列表 / 版本列表共用，`canvasThumb.test.tsx` 钉着）。
   */
  transparent?: boolean
  /**
   * 页面顶满盒子：不留四周的边、不画圆角与外框（导出预览的缩放舞台用，图在预览区里不留边距）。
   * 默认 false 时 DOM 与不带这个 prop 之前逐字相同。
   */
  flush?: boolean
  /**
   * 页面此刻的显示宽度（设备像素）：给了就按每张面板自己的显示宽度挑 `/api/render` 的档位
   * （放大预览时更清晰）；不给就是缩略图档 200。
   */
  pxWidth?: number
}) {
  const { w, h } = page
  const byId = useAssetStore((s) => s.byId)
  const nonce = useRuntimeAssetStore((s) => s.previewNonce)
  // 裁切 id 只由页面尺寸决定：同一份内容在画布列表与版本列表里画出的 DOM 逐字相同（同尺寸的两张共用一个也无妨，几何一样）
  const clip = `thumb-page-${w}x${h}`
  const checker = `thumb-checker-${w}x${h}`
  // 棋盘格的格子：长边切成 ~16 份，两格一个周期；只有 transparent 才用
  const cell = Math.max(w, h) / 16
  // 盒子是透明的，**画出来的是页面本身**（2026-10-07 设计审计 §10.3）：此前白底与边框画在 svg 盒上，
  // 横版与竖版的缩略图一模一样（都是一个 56×40 的白框）。现在页面矩形按真实比例居中，方向一眼可辨；
  // 圆角 4px（缩略图半径族 4 / 6 的小档），按默认盒子的缩放折成用户单位
  const pad = flush ? 0 : Math.max(w, h) * PAD
  const vw = w + 2 * pad
  const vh = h + 2 * pad
  const unitsPerPx = 1 / Math.min(BOX_W / vw, BOX_H / vh)
  const radius = flush ? 0 : 4 * unitsPerPx
  return (
    <svg
      viewBox={`${-pad} ${-pad} ${vw} ${vh}`}
      aria-hidden
      data-canvas-thumb
      /*
       * 版本列表可以有 120 行，每行一张缩略图 × 最多 40 个对象。滚出视口的
       * 那些不必参与排版与绘制。
       *
       * 挂在**这个 svg** 上而不是外面那一行上：跳过的是 svg 的子元素，而它
       * 自己的盒子尺寸来自 CSS（`h-10 w-14`），所以既不需要
       * `contain-intrinsic-size`、也不会有布局跳动；行里的时间与摘要照常参与
       * 排版、照常进可访问性树（把整行跳过就得赌浏览器怎么对待被跳过内容的
       * 可访问名）。这个 svg 本来就是 `aria-hidden` 的装饰。
       *
       * **尺寸必须来自 CSS**：调用方给的 className 不带宽高的话，盒子会塌。
       */
      style={{ contentVisibility: 'auto' }}
      // 画的是纸（两套主题同值）：纸上的记号用纸上的墨（paper-ink 一族），不用界面的 ink——暗色里 ink 变浅，落在白纸上看不见
      className={cn('shrink-0 text-paper-ink', className ?? 'h-10 w-14')}
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <clipPath id={clip}>
          <rect x={0} y={0} width={w} height={h} rx={radius} />
        </clipPath>
        {transparent && (
          // 纸白底 + 纸墨 10% 的格：纸上的记号用纸上的墨，两套主题同值，不抢画面
          <pattern id={checker} width={2 * cell} height={2 * cell} patternUnits="userSpaceOnUse">
            <rect width={2 * cell} height={2 * cell} fill="var(--color-paper)" />
            <rect width={cell} height={cell} fill="var(--color-paper-ink)" fillOpacity={0.1} />
            <rect x={cell} y={cell} width={cell} height={cell} fill="var(--color-paper-ink)" fillOpacity={0.1} />
          </pattern>
        )}
      </defs>
      {/* 页面：纸白 + 一圈 hairline（1px，不随缩放变粗变细） */}
      <rect
        data-thumb-page
        x={0}
        y={0}
        width={w}
        height={h}
        rx={radius}
        fill={transparent ? `url(#${checker})` : 'var(--color-paper)'}
        data-thumb-transparent={transparent ? '' : undefined}
        stroke={flush ? 'none' : 'var(--color-border-strong)'}
        strokeWidth={1}
        vectorEffect="non-scaling-stroke"
      />
      <g clipPath={`url(#${clip})`}>
      {objects
        .filter((o) => !o.hidden)
        .slice(0, THUMB_OBJECT_LIMIT)
        .map((o, i) => {
          const key = o.id ?? `i${i}`
          if (o.type === 'panel' && o.fileId) {
            const href =
              o.fileKind === 'runtime'
                ? runtimePreviewUrl(o.fileId, nonce[o.fileId])
                : renderUrl(o.fileId, pxWidth && w > 0 ? bucketFor((o.w / w) * pxWidth) : 200, byId[o.fileId]?.mtime)
            return (
              <ThumbImage
                key={key}
                fileId={o.fileId}
                href={href}
                x={o.x}
                y={o.y}
                width={o.w}
                height={o.h}
              />
            )
          }
          if (o.type === 'text') {
            return (
              <text
                key={key}
                x={o.x}
                y={o.y + o.h * 0.8}
                fontSize={Math.max(o.h * 0.7, h / 20)}
                fill="currentColor"
                className="text-paper-ink-2"
              >
                {(o.text ?? '').slice(0, THUMB_TEXT_CHARS)}
              </text>
            )
          }
          const common = {
            key,
            fill: 'none',
            stroke: 'currentColor',
            strokeWidth: Math.max(w, h) / 150,
            className: 'text-paper-ink-3',
          }
          if (o.type === 'shape' && o.shape === 'ellipse') {
            return <ellipse {...common} cx={o.x + o.w / 2} cy={o.y + o.h / 2} rx={o.w / 2} ry={o.h / 2} />
          }
          if (o.type === 'arrow' || (o.type === 'shape' && o.shape === 'line')) {
            return <line {...common} x1={o.x} y1={o.y} x2={o.x + o.w} y2={o.y + o.h} />
          }
          return (
            <rect
              {...common}
              x={o.x}
              y={o.y}
              width={Math.max(o.w, w / 60)}
              height={Math.max(o.h, h / 60)}
            />
          )
        })}
      </g>
    </svg>
  )
}

/** 缩略图里的一张面板图：`/api/render` 的一次失败按 `lib/imgRetry` 有界重试（候选后端的背压） */
function ThumbImage({
  fileId,
  href,
  x,
  y,
  width,
  height,
}: {
  fileId: string
  href: string
  x: number
  y: number
  width: number
  height: number
}) {
  const retry = useRetryingSrc(href)
  return (
    <image
      data-thumb-panel={fileId}
      href={retry.src}
      onError={retry.onError}
      x={x}
      y={y}
      width={width}
      height={height}
      preserveAspectRatio="none"
    />
  )
}
