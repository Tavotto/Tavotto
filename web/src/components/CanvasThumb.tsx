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
import { cn } from '@/lib/utils'
import { useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
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

export function CanvasThumb({
  page,
  objects,
  className,
}: {
  page: ThumbPage
  objects: readonly ThumbObject[]
  /** 尺寸由调用方给，默认与画布列表一致 */
  className?: string
}) {
  const { w, h } = page
  const byId = useAssetStore((s) => s.byId)
  const nonce = useRuntimeAssetStore((s) => s.previewNonce)
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
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
      className={cn(
        'shrink-0 rounded-xs border border-border bg-white text-ink',
        className ?? 'h-10 w-14',
      )}
      preserveAspectRatio="xMidYMid meet"
    >
      {objects
        .filter((o) => !o.hidden)
        .slice(0, THUMB_OBJECT_LIMIT)
        .map((o, i) => {
          const key = o.id ?? `i${i}`
          if (o.type === 'panel' && o.fileId) {
            const href =
              o.fileKind === 'runtime'
                ? runtimePreviewUrl(o.fileId, nonce[o.fileId])
                : renderUrl(o.fileId, 200, byId[o.fileId]?.mtime)
            return (
              <image
                key={key}
                data-thumb-panel={o.fileId}
                href={href}
                x={o.x}
                y={o.y}
                width={o.w}
                height={o.h}
                preserveAspectRatio="none"
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
                className="text-ink-2"
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
            className: 'text-ink-3',
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
    </svg>
  )
}
