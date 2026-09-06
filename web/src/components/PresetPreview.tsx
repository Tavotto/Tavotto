import { useMemo } from 'react'
import { ObjectView } from '@/canvas/ObjectView'
import { boundsOf } from '@/lib/geometry'
import { buildPreset, type PresetId } from '@/lib/presets'
import { mmToWorld } from '@/store/viewportStore'

/** 预览四周留的空白（mm）：旋转过的界线、箭头帽会探出包围盒一点 */
const PAD_MM = 2

/**
 * 科研预设的结构预览（审计 T30）。
 *
 * **不是另画的一套图标**：对象来自 `buildPreset()`——与点击插入时落到画布上的
 * 是同一份定义；画出来的是画布自己的 `ObjectView`（箭头帽、虚线、大括号、
 * 文字都按真实样式），只是整组缩进一个小盒子里。于是「点击后的方向、端点和
 * 标记与预览一致」不是靠维护两份来保证的，而是根本只有一份。
 *
 * `inert`：预览里的对象视图带着真实的命中层，不让它们吃指针与焦点。
 */
export function PresetPreview({
  id,
  width = 112,
  height = 56,
}: {
  id: PresetId
  width?: number
  height?: number
}) {
  const objs = useMemo(() => buildPreset(id, { x: 0, y: 0 }), [id])
  const bounds = boundsOf(objs)
  if (!bounds) return null
  const boxW = mmToWorld(bounds.w + 2 * PAD_MM)
  const boxH = mmToWorld(bounds.h + 2 * PAD_MM)
  const k = Math.min(width / boxW, height / boxH)
  return (
    <div
      data-preset-preview={id}
      aria-hidden
      inert
      className="pointer-events-none relative overflow-hidden"
      style={{ width, height }}
    >
      <div
        className="absolute left-1/2 top-1/2"
        style={{
          width: boxW,
          height: boxH,
          transform: `translate(-50%, -50%) scale(${k})`,
          transformOrigin: 'center',
        }}
      >
        <div
          className="absolute"
          style={{ left: mmToWorld(PAD_MM - bounds.x), top: mmToWorld(PAD_MM - bounds.y) }}
        >
          {objs.map((o) => (
            <ObjectView key={o.id} obj={o} />
          ))}
        </div>
      </div>
    </div>
  )
}
