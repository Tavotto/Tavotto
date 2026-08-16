import { updateObjects } from '@/store/actions'
import type { ArrowHeadType, ArrowObject, DashStyle, ShapeObject } from '@/types/document'
import { arrowHeads } from '@/types/document'
import { Button } from '../ui/Button'
import { Row, Section } from '../ui/Field'
import { ColorField, NumberField } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import { shared } from './common'

const HEAD_TYPES: { value: ArrowHeadType; label: string }[] = [
  { value: 'none', label: '无' },
  { value: 'triangle', label: '三角' },
  { value: 'open', label: '开口' },
  { value: 'bar', label: '短线' },
]

const DASHES: { value: DashStyle; label: string }[] = [
  { value: 'solid', label: '实线' },
  { value: 'dashed', label: '虚线' },
  { value: 'dotted', label: '点线' },
]

/** 写新端型时同步维护旧 head 字段（旧版本读档 / 旧后端导出仍有合理行为） */
function syncLegacyHead(o: ArrowObject): void {
  const { start, end } = arrowHeads(o)
  o.head = start !== 'none' && end !== 'none' ? 'both' : end !== 'none' ? 'end' : 'none'
}

export function ArrowSection({ objs }: { objs: ArrowObject[] }) {
  const ids = objs.map((o) => o.id)
  const patch = (label: string, fn: (o: ArrowObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'arrow') fn(o)
    })

  return (
    <Section title="箭头">
      <div className="flex flex-col gap-1.5">
        <Row label="线宽">
          <NumberField
            value={shared(objs, (o) => (o as ArrowObject).strokePt) ?? 1}
            mixed={shared(objs, (o) => (o as ArrowObject).strokePt) === undefined}
            step={0.25}
            min={0.1}
            max={20}
            precision={2}
            suffix="pt"
            onChange={(v) => patch('修改线宽', (o) => (o.strokePt = v))}
          />
        </Row>
        <Row label="颜色">
          <ColorField
            value={shared(objs, (o) => (o as ArrowObject).color) ?? '#1B1B18'}
            onChange={(v) => patch('修改箭头颜色', (o) => (o.color = v))}
          />
        </Row>
        <Row label="终点">
          <Segmented
            value={shared(objs, (o) => arrowHeads(o as ArrowObject).end) ?? null}
            onChange={(v) =>
              patch('修改终点端型', (o) => {
                const prev = arrowHeads(o) // 先取旧值：设了新字段后旧 head 不再参与推导
                o.headEnd = v
                o.headStart = prev.start
                syncLegacyHead(o)
              })
            }
            items={HEAD_TYPES}
            className="w-full"
          />
        </Row>
        <Row label="起点">
          <Segmented
            value={shared(objs, (o) => arrowHeads(o as ArrowObject).start) ?? null}
            onChange={(v) =>
              patch('修改起点端型', (o) => {
                const prev = arrowHeads(o)
                o.headStart = v
                o.headEnd = prev.end
                syncLegacyHead(o)
              })
            }
            items={HEAD_TYPES}
            className="w-full"
          />
        </Row>
        <Row label="线型">
          <Segmented
            value={shared(objs, (o) => (o as ArrowObject).dash ?? 'solid') ?? null}
            onChange={(v) => patch('修改线型', (o) => (o.dash = v === 'solid' ? undefined : v))}
            items={DASHES}
            className="w-full"
          />
        </Row>
        <Row label="">
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() =>
              patch('反转箭头方向', (o) => {
                const s = o.start
                o.start = o.end
                o.end = s
              })
            }
          >
            反转方向
          </Button>
        </Row>
      </div>
    </Section>
  )
}

export function ShapeSection({ objs }: { objs: ShapeObject[] }) {
  const ids = objs.map((o) => o.id)
  // line 只有描边；brace 只描边不填充
  const hasFillable = objs.some((o) => o.shape !== 'line' && o.shape !== 'brace')
  const allRect = objs.every((o) => o.shape === 'rect')
  const allPolygon = objs.every((o) => o.shape === 'polygon')
  const fill = shared(objs, (o) => (o as ShapeObject).fill)
  const patch = (label: string, fn: (o: ShapeObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'shape') fn(o)
    })

  return (
    <Section title="形状">
      <div className="flex flex-col gap-1.5">
        <Row label="线宽">
          <NumberField
            value={shared(objs, (o) => (o as ShapeObject).strokePt) ?? 1}
            mixed={shared(objs, (o) => (o as ShapeObject).strokePt) === undefined}
            step={0.25}
            min={0.1}
            max={20}
            precision={2}
            suffix="pt"
            onChange={(v) => patch('修改线宽', (o) => (o.strokePt = v))}
          />
        </Row>
        <Row label="描边">
          <ColorField
            value={shared(objs, (o) => (o as ShapeObject).color) ?? '#1B1B18'}
            onChange={(v) => patch('修改描边颜色', (o) => (o.color = v))}
          />
        </Row>
        <Row label="线型">
          <Segmented
            value={shared(objs, (o) => (o as ShapeObject).dash ?? 'solid') ?? null}
            onChange={(v) => patch('修改线型', (o) => (o.dash = v === 'solid' ? undefined : v))}
            items={DASHES}
            className="w-full"
          />
        </Row>
        {allRect && (
          <Row label="圆角">
            <NumberField
              value={shared(objs, (o) => (o as ShapeObject).cornerRadius ?? 0) ?? 0}
              step={0.5}
              min={0}
              max={50}
              precision={1}
              suffix="mm"
              onChange={(v) =>
                patch('修改圆角', (o) => {
                  if (v > 0) o.cornerRadius = v
                  else delete o.cornerRadius
                })
              }
            />
          </Row>
        )}
        {allPolygon && (
          <Row label="边数">
            <NumberField
              value={shared(objs, (o) => (o as ShapeObject).sides ?? 6) ?? 6}
              step={1}
              min={3}
              max={12}
              onChange={(v) => patch('修改边数', (o) => (o.sides = Math.round(v)))}
            />
          </Row>
        )}
        {hasFillable && (
          <Row label="填充">
            {fill ? (
              <>
                <ColorField value={fill} onChange={(v) => patch('修改填充', (o) => (o.fill = v))} />
                <Button
                  size="icon"
                  onClick={() => patch('清除填充', (o) => (o.fill = null))}
                  aria-label="清除填充"
                >
                  <span className="text-xs text-ink-3">无</span>
                </Button>
              </>
            ) : (
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => patch('添加填充', (o) => (o.fill = '#FFFFFF'))}
              >
                添加填充
              </Button>
            )}
          </Row>
        )}
        {hasFillable && fill && (
          <Row label="不透明度">
            <NumberField
              value={Math.round(((shared(objs, (o) => (o as ShapeObject).fillOpacity ?? 1) ?? 1) as number) * 100)}
              step={5}
              min={0}
              max={100}
              suffix="%"
              onChange={(v) =>
                patch('修改填充不透明度', (o) => {
                  const f = Math.max(0, Math.min(1, v / 100))
                  if (f < 1) o.fillOpacity = f
                  else delete o.fillOpacity
                })
              }
            />
          </Row>
        )}
      </div>
    </Section>
  )
}
