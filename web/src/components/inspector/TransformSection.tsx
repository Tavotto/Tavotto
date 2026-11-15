import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { fitBeforeRotate } from '@/canvas/textFit'
import { normalizeRotationDeg } from '@/lib/geometry'
import { formatMm } from '@/lib/units'
import { selectedUnits, updateObjects } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useTransformPrefs } from '@/store/transformPrefs'
import { reflectSelection, skewSelection, transformSelection } from '@/store/transformActions'
import { isGroupKey } from '@/lib/groupTree'
import { canSkew, refFrac, visibleBounds } from '@/lib/transform'
import { rotateAbout } from '@/lib/pathGeom'
import { FlipHorizontal2, FlipVertical2, Link2, Scaling, Unlink2 } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { IconButton } from '../ui/Button'
import { RefPointGrid } from './RefPointGrid'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import type { CanvasObject } from '@/types/document'
import { Row, Section } from '../ui/Field'
import { SummaryRow } from '../ui/SummaryRow'
import { INSPECTOR_LABEL_W } from './layout'
import { NumberField } from '../ui/Input'
import { GeometryGrid, MmField } from './MmField'
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
  // 竖排文字（ADR 0163）：框高是行长（用户定），框宽由列数撑开——与横排对称，W / H 的可编辑性对调
  const isVertical = (o: CanvasObject) => o.type === 'text' && o.writingMode === 'vertical'
  const allVertical = textOnly && objs.every(isVertical)
  const noneVertical = !objs.some(isVertical)
  // 任意角度旋转只对 text/arrow/shape 开放；面板走 90° 步进（PanelSection）
  const rotatable = objs.every((o) => o.type !== 'panel')
  // 变换面板（ADR 0185 M4 第二批）：参考点、等比锁、缩放描边和效果是个人偏好（`transformPrefs`）
  const refPoint = useTransformPrefs((s) => s.refPoint)
  const lockRatio = useTransformPrefs((s) => s.lockRatio)
  const strokes = useTransformPrefs((s) => s.scaleStrokes)
  const setPrefs = useTransformPrefs((s) => s.set)
  const remember = useTransformPrefs((s) => s.remember)
  const all = useDocumentStore((s) => s.doc.objects)
  const [fx, fy] = refFrac(refPoint)
  // 选区恰好是一整个组（含剪切组）：X / Y / W / H 说的是整组看得见的范围（剪切组 = 蒙版范围），改它 = 整组变换
  const units = selectedUnits(ids)
  const unit = objs.length > 1 && units.length === 1 && isGroupKey(units[0])
  const box = unit ? visibleBounds(objs, all) : null
  const panelsInside = objs.some((o) => o.type === 'panel')
  const ratioLocked = lockRatio || (unit && panelsInside)
  const skewable = objs.some((o) => !o.hidden && canSkew(o))

  const setEach = (label: UiMessage, fn: (o: CanvasObject, index: number) => void) =>
    updateObjects(ids, label, (o) => fn(o, ids.indexOf(o.id)))
  /** 参考点在对象（未旋转的）框上的坐标 */
  const refX = (o: CanvasObject) => o.x + fx * o.w
  const refY = (o: CanvasObject) => o.y + fy * o.h
  /** 改了宽 / 高：以参考点为不动点；开着「缩放描边和效果」时线宽等跟着 √(kx·ky) 走 */
  const resize = (o: CanvasObject, kx: number, ky: number) => {
    const rx = refX(o)
    const ry = refY(o)
    o.w *= kx
    o.h *= ky
    o.x = rx - fx * o.w
    o.y = ry - fy * o.h
    if (strokes && o.type !== 'text') {
      const k = Math.sqrt(Math.abs(kx * ky))
      const c = o as unknown as Record<string, number | undefined>
      for (const f of ['strokePt', 'cornerRadius'] as const) if (typeof c[f] === 'number') c[f] = (c[f] as number) * k
    }
  }
  const lockToggle = (
    <IconButton
      data-transform-ratio-lock=""
      label={t('transform.lockRatio')}
      tip={t(ratioLocked ? 'transform.ratioLocked' : 'transform.ratioUnlocked')}
      iconSize="sm"
      active={ratioLocked}
      aria-pressed={ratioLocked}
      disabled={unit && panelsInside}
      onClick={() => setPrefs({ lockRatio: !lockRatio })}
    >
      {ratioLocked ? <Link2 size={ICON_SIZE.sm} /> : <Unlink2 size={ICON_SIZE.sm} />}
    </IconButton>
  )

  const body = unit && box ? (
    <>
      <GeometryGrid>
        <MmField
          label="X"
          dataProp="transform.x"
          historyLabel={hist('setX')}
          value={box.x + fx * box.w}
          onChange={(v) => transformSelection({ kind: 'move', dx: v - (box.x + fx * box.w), dy: 0 })}
        />
        <RefPointGrid value={refPoint} onChange={(rp) => setPrefs({ refPoint: rp })} />
        <MmField
          label="Y"
          dataProp="transform.y"
          historyLabel={hist('setY')}
          value={box.y + fy * box.h}
          onChange={(v) => transformSelection({ kind: 'move', dx: 0, dy: v - (box.y + fy * box.h) })}
        />
        <MmField
          label="W"
          dataProp="transform.w"
          historyLabel={hist('setWidth')}
          min={0.1}
          value={box.w}
          onChange={(v) => {
            const k = v / box.w
            transformSelection({ kind: 'scale', kx: k, ky: ratioLocked ? k : 1, rp: refPoint })
          }}
        />
        {lockToggle}
        <MmField
          label="H"
          dataProp="transform.h"
          historyLabel={hist('setHeight')}
          min={0.1}
          value={box.h}
          onChange={(v) => {
            const k = v / box.h
            transformSelection({ kind: 'scale', kx: ratioLocked ? k : 1, ky: k, rp: refPoint })
          }}
        />
      </GeometryGrid>
      <RotateSkewRow
        rotation={0}
        rotationMixed={false}
        rotatable
        skewable={skewable}
        onRotate={(v) => v && transformSelection({ kind: 'rotate', deg: v, rp: refPoint })}
      />
    </>
  ) : (
    <>
      <GeometryGrid>
        <MmField
          label="X"
          dataProp="transform.x"
          historyLabel={hist('setX')}
          value={shared(objs, refX)}
          onChange={(v) => {
            const base = shared(objs, refX)
            if (base === undefined && one == null) {
              // 多个不同值时按整体偏移，保持相对位置
              const min = Math.min(...objs.map(refX))
              setEach(hist('setX'), (o) => {
                o.x += v - min
              })
            } else {
              if (one) remember({ kind: 'move', dx: v - refX(one), dy: 0 })
              setEach(hist('setX'), (o) => {
                o.x = v - fx * o.w
              })
            }
          }}
        />
        <RefPointGrid value={refPoint} onChange={(rp) => setPrefs({ refPoint: rp })} />
        <MmField
          label="Y"
          dataProp="transform.y"
          historyLabel={hist('setY')}
          value={shared(objs, refY)}
          onChange={(v) => {
            const base = shared(objs, refY)
            if (base === undefined && one == null) {
              const min = Math.min(...objs.map(refY))
              setEach(hist('setY'), (o) => {
                o.y += v - min
              })
            } else {
              if (one) remember({ kind: 'move', dx: 0, dy: v - refY(one) })
              setEach(hist('setY'), (o) => {
                o.y = v - fy * o.h
              })
            }
          }}
        />
        <MmField
          label="W"
          dataProp="transform.w"
          historyLabel={hist('setWidth')}
          min={1}
          disabled={allVertical}
          title={allVertical ? t('transform.textWidthAuto') : undefined}
          value={shared(objs, (o) => o.w)}
          onChange={(v) => {
            if (one && !isVertical(one)) {
              const k = v / one.w
              remember({ kind: 'scale', kx: k, ky: lockRatio && one.type !== 'text' ? k : 1, rp: refPoint })
            }
            setEach(hist('setWidth'), (o) => {
              if (isVertical(o)) return
              const k = v / o.w
              const keep = o.type === 'panel' || (lockRatio && o.type !== 'text')
              resize(o, k, keep ? k : 1)
            })
          }}
        />
        {lockToggle}
        <MmField
          label="H"
          dataProp="transform.h"
          historyLabel={hist('setHeight')}
          min={1}
          disabled={textOnly && noneVertical}
          title={textOnly && noneVertical ? t('transform.textHeightAuto') : undefined}
          value={shared(objs, (o) => o.h)}
          onChange={(v) => {
            if (one && !(one.type === 'text' && !isVertical(one))) {
              const k = v / one.h
              remember({ kind: 'scale', kx: lockRatio && one.type !== 'text' ? k : 1, ky: k, rp: refPoint })
            }
            setEach(hist('setHeight'), (o) => {
              if (o.type === 'text' && !isVertical(o)) return
              const k = v / o.h
              const keep = o.type === 'panel' || (lockRatio && o.type !== 'text')
              resize(o, keep ? k : 1, k)
            })
          }}
        />
      </GeometryGrid>
      <RotateSkewRow
        rotation={shared(objs, (o) => normalizeRotationDeg(o.rotationDeg ?? 0)) ?? 0}
        rotationMixed={shared(objs, (o) => normalizeRotationDeg(o.rotationDeg ?? 0)) === undefined}
        rotatable={rotatable}
        skewable={skewable}
        onRotate={(v) => {
          if (one) remember({ kind: 'rotate', deg: normalizeRotationDeg(v) - normalizeRotationDeg(one.rotationDeg ?? 0), rp: refPoint })
          setEach(hist('rotateObject'), (o) => {
            const deg = normalizeRotationDeg(v)
            // 单行文字从 0 变非 0 的那一次先收到贴字宽（量法与落位见 canvas/textFit）
            const fit = deg ? fitBeforeRotate(o) : null
            if (fit) Object.assign(o, fit)
            const cur = normalizeRotationDeg(o.rotationDeg ?? 0)
            // 绕参考点转（参考点在旋转后的框上）；参考点在中心时就是原来的「绕中心转」
            const c: [number, number] = [o.x + o.w / 2, o.y + o.h / 2]
            const ref = rotateAbout([refX(o), refY(o)], c, cur)
            // o 是 Immer 草稿：不能交给要 structuredClone 的 `rotateObjectAbout`，框中心就地绕参考点转
            const nc = rotateAbout(c, ref, deg - cur)
            o.x = nc[0] - o.w / 2
            o.y = nc[1] - o.h / 2
            if (deg) o.rotationDeg = deg
            else delete o.rotationDeg
          })
        }}
      />
    </>
  )

  const actions = (
    <div className="flex items-center" data-transform-actions="">
      <IconButton label={t('transform.reflectH')} iconSize="sm" data-transform-reflect="h" onClick={() => void reflectSelection('h')}>
        <FlipHorizontal2 size={ICON_SIZE.sm} />
      </IconButton>
      <IconButton label={t('transform.reflectV')} iconSize="sm" data-transform-reflect="v" onClick={() => void reflectSelection('v')}>
        <FlipVertical2 size={ICON_SIZE.sm} />
      </IconButton>
      <IconButton
        label={t('transform.scaleStrokes')}
        tip={t(strokes ? 'transform.scaleStrokesOn' : 'transform.scaleStrokesOff')}
        iconSize="sm"
        active={strokes}
        aria-pressed={strokes}
        data-transform-scale-strokes=""
        onClick={() => setPrefs({ scaleStrokes: !strokes })}
      >
        <Scaling size={ICON_SIZE.sm} />
      </IconButton>
    </div>
  )

  if (foldKey) {
    return (
      <FoldedTransform title={t('transform.title')} foldKey={foldKey} objs={objs}>
        <div className="mb-1 flex justify-end">{actions}</div>
        {body}
      </FoldedTransform>
    )
  }
  return (
    <Section data-transform-section title={t('transform.title')} action={actions}>
      {body}
    </Section>
  )
}

/** 旋转 + 水平倾斜一行（两个半宽框）。倾斜是一次动作：输入角度回车就斜切（烤进路径），框里回到 0 */
function RotateSkewRow({
  rotation,
  rotationMixed,
  rotatable,
  skewable,
  onRotate,
}: {
  rotation: number
  rotationMixed: boolean
  rotatable: boolean
  skewable: boolean
  onRotate: (v: number) => void
}) {
  const { t } = useTranslation('inspector')
  return (
    <div className="mt-1.5">
      <Row label={t('transform.rotationSkew')} labelWidth={INSPECTOR_LABEL_W}>
        <div className="flex min-w-0 gap-1.5">
          <NumberField
            half
            value={rotation}
            mixed={rotationMixed}
            disabled={!rotatable}
            title={rotatable ? undefined : t('transform.rotatePanelHint')}
            step={15}
            min={-360}
            max={360}
            unit="°"
            ariaLabel={t('transform.rotation')}
            dataProp="transform.rotation"
            onChange={onRotate}
          />
          <NumberField
            half
            value={0}
            step={5}
            min={-89}
            max={89}
            unit="°"
            disabled={!skewable}
            title={skewable ? t('transform.skewTip') : t('transform.skewUnsupported')}
            ariaLabel={t('transform.skew')}
            dataProp="transform.skew"
            onChange={(v) => void skewSelection(v)}
          />
        </div>
      </Row>
    </div>
  )
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
    <SummaryRow
      data-fold={foldKey}
      label={title}
      open={open}
      onToggle={() => setOpen(foldKey, !open)}
      value={summary}
    >
      <div data-transform-folded>{children}</div>
    </SummaryRow>
  )
}
