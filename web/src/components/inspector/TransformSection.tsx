import { updateObjects } from '@/store/actions'
import type { CanvasObject } from '@/types/document'
import { Grid2, Row, Section } from '../ui/Field'
import { NumberField } from '../ui/Input'
import { MmField } from './MmField'
import { shared } from './common'

/** X / Y / W / H —— 面板与形状改宽高时按比例联动，文字高度由内容决定 */
export function TransformSection({ objs }: { objs: CanvasObject[] }) {
  const ids = objs.map((o) => o.id)
  const one = objs.length === 1 ? objs[0] : null
  const textOnly = objs.every((o) => o.type === 'text')
  const keepRatio = objs.every((o) => o.type === 'panel')
  // 任意角度旋转只对 text/arrow/shape 开放；面板走 90° 步进（PanelSection）
  const rotatable = objs.every((o) => o.type !== 'panel')

  const setEach = (label: string, fn: (o: CanvasObject, index: number) => void) =>
    updateObjects(ids, label, (o) => fn(o, ids.indexOf(o.id)))

  return (
    <Section title="变换">
      <Grid2>
        <MmField
          label="X"
          historyLabel="修改 X"
          value={shared(objs, (o) => o.x)}
          onChange={(v) => {
            const base = shared(objs, (o) => o.x)
            if (base === undefined && one == null) {
              // 多个不同值时按整体偏移，保持相对位置
              const min = Math.min(...objs.map((o) => o.x))
              setEach('修改 X', (o) => {
                o.x += v - min
              })
            } else setEach('修改 X', (o) => {
              o.x = v
            })
          }}
        />
        <MmField
          label="Y"
          historyLabel="修改 Y"
          value={shared(objs, (o) => o.y)}
          onChange={(v) => {
            const base = shared(objs, (o) => o.y)
            if (base === undefined && one == null) {
              const min = Math.min(...objs.map((o) => o.y))
              setEach('修改 Y', (o) => {
                o.y += v - min
              })
            } else setEach('修改 Y', (o) => {
              o.y = v
            })
          }}
        />
        <MmField
          label="W"
          historyLabel="修改宽度"
          min={1}
          value={shared(objs, (o) => o.w)}
          onChange={(v) =>
            setEach('修改宽度', (o) => {
              const k = v / o.w
              o.w = v
              if (o.type === 'panel' || (o.type === 'shape' && keepRatio)) o.h *= k
            })
          }
        />
        <MmField
          label="H"
          historyLabel="修改高度"
          min={1}
          disabled={textOnly}
          title={textOnly ? '文字高度由内容自动决定' : undefined}
          value={shared(objs, (o) => o.h)}
          onChange={(v) =>
            setEach('修改高度', (o) => {
              if (o.type === 'text') return
              const k = v / o.h
              o.h = v
              if (o.type === 'panel') o.w *= k
            })
          }
        />
      </Grid2>
      {rotatable && (
        <div className="mt-1.5">
          <Row label="旋转">
            <NumberField
              value={shared(objs, (o) => o.rotationDeg ?? 0) ?? 0}
              mixed={shared(objs, (o) => o.rotationDeg ?? 0) === undefined}
              step={15}
              min={-360}
              max={360}
              suffix="°"
              onChange={(v) =>
                setEach('旋转对象', (o) => {
                  const deg = ((Math.round(v * 10) / 10) % 360 + 360) % 360
                  if (deg) o.rotationDeg = deg
                  else delete o.rotationDeg
                })
              }
            />
          </Row>
        </div>
      )}
    </Section>
  )
}
