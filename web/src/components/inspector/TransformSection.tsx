import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { formatMm } from '@/lib/units'
import { updateObjects } from '@/store/actions'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import type { CanvasObject } from '@/types/document'
import { Disclosure, Row, Section } from '../ui/Field'
import { NumberField } from '../ui/Input'
import { GeometryGrid, GeometrySpacer, MmField } from './MmField'
import { shared } from './common'

/** X / Y / W / H —— 面板与形状改宽高时按比例联动，文字高度由内容决定 */
/** 本组的历史标签都在 inspector:history.* 下 */
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')

/**
 * 位置与尺寸。
 *
 * `foldKey` 给了就折叠成一行 + 现状摘要（`X 43.3 · Y 20.6 mm`），展开状态按这个
 * 键记忆。**只有文字对象用它**：文字的高频编辑是内容与排版，位置多半是在画布上
 * 拖出来的，把一整段变换摆在内容前面等于让人每次都往下找（审计 T27）。
 * 折叠不减能力——展开后还是同一批字段。
 */
export function TransformSection({ objs, foldKey }: { objs: CanvasObject[]; foldKey?: string }) {
  const { t } = useTranslation('inspector')
  const ids = objs.map((o) => o.id)
  const one = objs.length === 1 ? objs[0] : null
  const textOnly = objs.every((o) => o.type === 'text')
  const keepRatio = objs.every((o) => o.type === 'panel')
  // 任意角度旋转只对 text/arrow/shape 开放；面板走 90° 步进（PanelSection）
  const rotatable = objs.every((o) => o.type !== 'panel')

  const setEach = (label: UiMessage, fn: (o: CanvasObject, index: number) => void) =>
    updateObjects(ids, label, (o) => fn(o, ids.indexOf(o.id)))

  const body = (
    <>
      <GeometryGrid>
        <MmField
          label="X"
          historyLabel={hist('setX')}
          value={shared(objs, (o) => o.x)}
          onChange={(v) => {
            const base = shared(objs, (o) => o.x)
            if (base === undefined && one == null) {
              // 多个不同值时按整体偏移，保持相对位置
              const min = Math.min(...objs.map((o) => o.x))
              setEach(hist('setX'), (o) => {
                o.x += v - min
              })
            } else setEach(hist('setX'), (o) => {
              o.x = v
            })
          }}
        />
        <GeometrySpacer />
        <MmField
          label="Y"
          historyLabel={hist('setY')}
          value={shared(objs, (o) => o.y)}
          onChange={(v) => {
            const base = shared(objs, (o) => o.y)
            if (base === undefined && one == null) {
              const min = Math.min(...objs.map((o) => o.y))
              setEach(hist('setY'), (o) => {
                o.y += v - min
              })
            } else setEach(hist('setY'), (o) => {
              o.y = v
            })
          }}
        />
        <MmField
          label="W"
          historyLabel={hist('setWidth')}
          min={1}
          value={shared(objs, (o) => o.w)}
          onChange={(v) =>
            setEach(hist('setWidth'), (o) => {
              const k = v / o.w
              o.w = v
              if (o.type === 'panel' || (o.type === 'shape' && keepRatio)) o.h *= k
            })
          }
        />
        <GeometrySpacer />
        <MmField
          label="H"
          historyLabel={hist('setHeight')}
          min={1}
          disabled={textOnly}
          title={textOnly ? t('transform.textHeightAuto') : undefined}
          value={shared(objs, (o) => o.h)}
          onChange={(v) =>
            setEach(hist('setHeight'), (o) => {
              if (o.type === 'text') return
              const k = v / o.h
              o.h = v
              if (o.type === 'panel') o.w *= k
            })
          }
        />
      </GeometryGrid>
      {rotatable && (
        <div className="mt-1.5">
          <Row label={t('transform.rotation')} labelWidth={72}>
            <NumberField
              value={shared(objs, (o) => o.rotationDeg ?? 0) ?? 0}
              mixed={shared(objs, (o) => o.rotationDeg ?? 0) === undefined}
              step={15}
              min={-360}
              max={360}
              unit="°"
              ariaLabel={t('transform.rotation')}
              onChange={(v) =>
                setEach(hist('rotateObject'), (o) => {
                  const deg = ((Math.round(v * 10) / 10) % 360 + 360) % 360
                  if (deg) o.rotationDeg = deg
                  else delete o.rotationDeg
                })
              }
            />
          </Row>
        </div>
      )}
    </>
  )

  if (foldKey) {
    return (
      <FoldedTransform title={t('transform.title')} foldKey={foldKey} objs={objs}>
        {body}
      </FoldedTransform>
    )
  }
  return <Section title={t('transform.title')}>{body}</Section>
}

/**
 * 折叠壳：收起时报出当前 X / Y（多个值时报「多个值」），与画布设置的折叠摘要
 * 同一种模型。展开状态存 `inspectorPrefs`，与「更多」共用那份持久化偏好。
 */
function FoldedTransform({
  title,
  foldKey,
  objs,
  children,
}: {
  title: string
  foldKey: string
  objs: CanvasObject[]
  children: ReactNode
}) {
  const open = useInspectorPrefs((s) => s.moreOpen[foldKey] ?? false)
  const setOpen = useInspectorPrefs((s) => s.setMoreOpen)
  const mixed = translate('mixed')
  const x = shared(objs, (o) => o.x)
  const y = shared(objs, (o) => o.y)
  const summary = translate('transform.summary', {
    ns: 'inspector',
    x: x === undefined ? mixed : formatMm(x),
    y: y === undefined ? mixed : formatMm(y),
  })

  return (
    <Disclosure
      title={title}
      open={open}
      onToggle={() => setOpen(foldKey, !open)}
      summary={summary}
    >
      <div data-transform-folded>{children}</div>
    </Disclosure>
  )
}
