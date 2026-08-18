import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Crop,
  FlipHorizontal2,
  FlipVertical2,
  Link2,
  Maximize2,
  Minimize2,
  Pencil,
  Ratio,
  Replace,
  RotateCcw,
  Scaling,
  Unlink2,
} from 'lucide-react'
import { usePanelRender, useRenderStore } from '@/store/renderStore'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { BASE_FONT_PT, effectiveDpi, effectivePt, formatCm, formatMm, round1 } from '@/lib/units'
import { cn } from '@/lib/utils'
import type { PanelInfo } from '@/lib/api'
import {
  enterElementEdit,
  fillPanels,
  fitPanels,
  replacePanelAsset,
  resetPanelCrop,
  restorePanelAspect,
  restorePanelNativeSize,
  rotatePanels,
  setPanelAspectLocked,
  setPanelOpacity,
  updateObjects,
} from '@/store/actions'
import { folderLabel, useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject, PanelRotation } from '@/types/document'
import {
  panelAspectLocked,
  panelFullSize,
  panelRotation,
  ROTATIONS,
} from '@/types/document'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { Disclosure, Grid2, Row, Section } from '../ui/Field'
import { NumberField, TextInput } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import { Tip } from '../ui/Tooltip'
import { AlignToCanvasRow } from './ArrangeSection'
import { HistoryPanel } from './HistoryPanel'
import { MmField } from './MmField'
import { UpdateSourceButton } from './UpdateSourceButton'
import { shared } from './common'

/** 本文件的文案：inspector:panel.*，历史标签 inspector:history.* */
const pn = (key: string, values?: Record<string, unknown>) =>
  translate(`panel.${key}`, { ns: 'inspector', ...(values ?? {}) })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')

/** 多选时取值不一致返回 undefined —— 面板版，省去每处的类型断言 */
function sharedPanel<T>(objs: PanelObject[], pick: (o: PanelObject) => T): T | undefined {
  return shared(objs, (o) => pick(o as PanelObject))
}

export function PanelSection({ objs }: { objs: PanelObject[] }) {
  const one = objs.length === 1 ? objs[0] : null
  if (!objs.length) return null

  return (
    <>
      {/* 图内编辑是参数化面板的核心动作，放在最上面 */}
      {one?.script && <ScriptSection panel={one} />}
      <GeometrySection objs={objs} />
      <AppearanceSection objs={objs} />
      <ImageSection objs={objs} />
      <DiagnosticsSection objs={objs} />
      {one?.script && <SourceSection panel={one} />}
    </>
  )
}

/* -------------------------------------------------------------------------- */
/*  位置与尺寸（高频，默认展开）                                                 */
/* -------------------------------------------------------------------------- */

function GeometrySection({ objs }: { objs: PanelObject[] }) {
  useTranslation('inspector')
  const ids = objs.map((o) => o.id)
  const one = objs.length === 1 ? objs[0] : null
  const locked = objs.every(panelAspectLocked)
  // 原始大小是尺寸的锚点：W/H 是当前值，缩放 % 一律相对它，不相对上一次
  const native = sharedPanel(objs, (o) => `${formatMm(o.nativeW)} × ${formatMm(o.nativeH)}`)
  const scale = sharedPanel(objs, (o) => Math.round((panelFullSize(o).w / o.nativeW) * 100))

  const setEach = (label: UiMessage, fn: (o: PanelObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'panel') fn(o)
    })

  /** 多个不同值时按整体偏移，保持相对位置 */
  const setAxis = (axis: 'x' | 'y', v: number) => {
    const label = hist(axis === 'x' ? 'setX' : 'setY')
    if (sharedPanel(objs, (o) => o[axis]) === undefined && objs.length > 1) {
      const min = Math.min(...objs.map((o) => o[axis]))
      setEach(label, (o) => {
        o[axis] += v - min
      })
    } else {
      setEach(label, (o) => {
        o[axis] = v
      })
    }
  }

  return (
    <Section title={pn('geometry')}>
      <Grid2>
        <MmField
          label="X"
          historyLabel={hist('setX')}
          value={sharedPanel(objs, (o) => o.x)}
          onChange={(v) => setAxis('x', v)}
        />
        <MmField
          label="Y"
          historyLabel={hist('setY')}
          value={sharedPanel(objs, (o) => o.y)}
          onChange={(v) => setAxis('y', v)}
        />
      </Grid2>

      <div className="mt-1.5 flex items-center gap-1.5">
        <div className="min-w-0 flex-1">
          <MmField
            label="W"
            historyLabel={hist('setWidth')}
            min={1}
            value={sharedPanel(objs, (o) => o.w)}
            onChange={(v) =>
              setEach(hist('setWidth'), (o) => {
                const k = v / o.w
                o.w = v
                if (panelAspectLocked(o)) o.h *= k
              })
            }
          />
        </div>
        <Tip label={pn(locked ? 'aspectLocked' : 'aspectUnlocked')}>
          <Button
            size="icon-sm"
            active={locked}
            aria-pressed={locked}
            aria-label={pn('lockAspect')}
            onClick={() => setPanelAspectLocked(ids, !locked)}
          >
            {locked ? <Link2 size={12} /> : <Unlink2 size={12} />}
          </Button>
        </Tip>
        <div className="min-w-0 flex-1">
          <MmField
            label="H"
            historyLabel={hist('setHeight')}
            min={1}
            value={sharedPanel(objs, (o) => o.h)}
            onChange={(v) =>
              setEach(hist('setHeight'), (o) => {
                const k = v / o.h
                o.h = v
                if (panelAspectLocked(o)) o.w *= k
              })
            }
          />
        </div>
      </div>

      <Row className="mt-1.5" label={pn('scale')}>
        <NumberField
          value={scale ?? 100}
          mixed={scale === undefined}
          step={1}
          min={5}
          max={500}
          precision={0}
          suffix="%"
          title={pn('scaleTitle')}
          onChange={(v) =>
            updateObjects(ids, hist('setPanelScale'), (o) => {
              if (o.type !== 'panel') return
              // 裁剪后仍以未裁剪的整图为缩放基准；包围盒等比缩放
              const k = (o.nativeW * (v / 100)) / panelFullSize(o).w
              o.w *= k
              o.h *= k
            })
          }
        />
        <Tip
          label={
            one
              ? pn('nativeTip', { w: formatCm(one.nativeW), h: formatCm(one.nativeH) })
              : pn('nativeTipMulti')
          }
          side="left"
        >
          <span className="min-w-0 shrink truncate font-mono text-xs text-ink-3">
            {native ? pn('native', { size: native }) : pn('nativeMixed')}
          </span>
        </Tip>
      </Row>

      {objs.length === 1 && (
        <div className="mt-2">
          <AlignToCanvasRow />
        </div>
      )}
    </Section>
  )
}

/* -------------------------------------------------------------------------- */
/*  外观（折叠）：旋转 / 翻转 / 不透明度                                          */
/* -------------------------------------------------------------------------- */

function AppearanceSection({ objs }: { objs: PanelObject[] }) {
  useTranslation('inspector')
  const [open, setOpen] = useState(false)
  const ids = objs.map((o) => o.id)
  const rot = sharedPanel(objs, panelRotation)
  const opacity = sharedPanel(objs, (o) => Math.round((o.opacity ?? 1) * 100))
  const translucent = objs.some((o) => (o.opacity ?? 1) < 1)
  const flipped = objs.some((o) => o.flipH || o.flipV)

  const setEach = (label: UiMessage, fn: (o: PanelObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'panel') fn(o)
    })

  const summaryBits = [
    rot ? `${rot}°` : null,
    flipped ? pn('flipped') : null,
    opacity !== undefined && opacity < 100 ? `${opacity}%` : null,
  ].filter(Boolean)

  return (
    <Disclosure
      title={pn('appearance')}
      open={open}
      onToggle={() => setOpen((v) => !v)}
      summary={summaryBits.length ? summaryBits.join(' · ') : undefined}
    >
      <div className="flex flex-col gap-1.5">
        <Row label={translate('transform.rotation', { ns: 'inspector' })}>
          <Segmented
            className="w-full"
            value={rot === undefined ? null : String(rot)}
            onChange={(v) => rotatePanels(ids, Number(v) as PanelRotation)}
            items={ROTATIONS.map((r) => ({
              value: String(r),
              label: `${r}°`,
              tip: pn('rotationTip'),
            }))}
          />
        </Row>

        <Row label={pn('flip')}>
          <div className="flex min-w-0 flex-1 gap-1">
            <Button
              variant="outline"
              size="sm"
              className="flex-1"
              active={sharedPanel(objs, (o) => o.flipH === true) === true}
              onClick={() =>
                setEach(hist('flipH'), (o) => {
                  o.flipH = o.flipH ? undefined : true
                })
              }
            >
              <FlipHorizontal2 size={13} />
              {pn('flipHorizontal')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="flex-1"
              active={sharedPanel(objs, (o) => o.flipV === true) === true}
              onClick={() =>
                setEach(hist('flipV'), (o) => {
                  o.flipV = o.flipV ? undefined : true
                })
              }
            >
              <FlipVertical2 size={13} />
              {pn('flipVertical')}
            </Button>
          </div>
        </Row>

        <Row label={pn('opacity')}>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={opacity ?? 100}
            aria-label={pn('opacity')}
            style={{ accentColor: 'var(--color-accent)' }}
            className="h-4 min-w-0 flex-1 cursor-pointer"
            onPointerDown={() =>
              useDocumentStore.getState().beginTxn(msg('history.setOpacity', undefined, 'workspace'))
            }
            onPointerUp={() => useDocumentStore.getState().endTxn()}
            onChange={(e) => setPanelOpacity(ids, Number(e.target.value) / 100)}
          />
          <NumberField
            className="w-[58px] shrink-0"
            value={opacity ?? 100}
            mixed={opacity === undefined}
            min={0}
            max={100}
            step={1}
            precision={0}
            suffix="%"
            onChange={(v) => setPanelOpacity(ids, v / 100)}
          />
        </Row>

        {/* 说明只讲导出后果：翻转 / 半透明面板在 PDF 里按位图嵌入，矢量文字不再可选中 */}
        {(flipped || translucent) && (
          <p className="text-xs leading-relaxed text-ink-3">
            {pn(flipped && translucent ? 'bitmapBoth' : flipped ? 'bitmapFlip' : 'bitmapOpacity')}
          </p>
        )}
      </div>
    </Disclosure>
  )
}

/* -------------------------------------------------------------------------- */
/*  图片（折叠）：裁剪 / 适配 / 替换素材                                          */
/* -------------------------------------------------------------------------- */

function ImageSection({ objs }: { objs: PanelObject[] }) {
  useTranslation('inspector')
  const [open, setOpen] = useState(false)
  const ids = objs.map((o) => o.id)
  const cropTargetId = useUiStore((s) => s.cropTargetId)
  const one = objs.length === 1 ? objs[0] : null
  const [replacing, setReplacing] = useState(false)
  const cropped = objs.some((o) => o.crop)
  const cropping = !!one && cropTargetId === one.id

  // 正在裁剪时展开，让「完成裁剪」有处可点
  const effectiveOpen = open || cropping

  return (
    <Disclosure
      title={pn('image')}
      open={effectiveOpen}
      onToggle={() => setOpen((v) => !v)}
      summary={cropped ? pn('cropped') : undefined}
    >
      <div className="flex gap-1.5">
        <Tip label={pn('cropTip')}>
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            disabled={!one}
            active={cropping}
            onClick={() => {
              if (one) useUiStore.getState().setCropTarget(cropping ? null : one.id)
            }}
          >
            <Crop size={13} />
            {pn(cropping ? 'cropDone' : 'crop')}
          </Button>
        </Tip>
        {cropped && (
          <Tip label={pn('resetCropTip')}>
            <Button
              variant="outline"
              size="sm"
              onClick={() => resetPanelCrop(ids)}
              aria-label={pn('resetCrop')}
            >
              <RotateCcw size={13} />
            </Button>
          </Tip>
        )}
      </div>

      <Grid2 className="mt-1.5">
        <Tip label={pn('fitTip')}>
          <Button variant="outline" size="sm" className="w-full" onClick={() => fitPanels(ids)}>
            <Minimize2 size={13} />
            {pn('fit')}
          </Button>
        </Tip>
        <Tip label={pn('fillTip')}>
          <Button variant="outline" size="sm" className="w-full" onClick={() => fillPanels(ids)}>
            <Maximize2 size={13} />
            {pn('fill')}
          </Button>
        </Tip>
        <Tip label={pn('aspectTip')}>
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => restorePanelAspect(ids)}
          >
            <Ratio size={13} />
            {pn('aspect')}
          </Button>
        </Tip>
        <Tip label={pn('nativeSizeTip')}>
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => restorePanelNativeSize(ids)}
          >
            <Scaling size={13} />
            {pn('nativeSize')}
          </Button>
        </Tip>
      </Grid2>

      <Tip label={pn('replaceTip')}>
        <Button
          variant="outline"
          size="sm"
          className="mt-1.5 w-full"
          disabled={!one}
          onClick={() => setReplacing(true)}
        >
          <Replace size={13} />
          {pn('replace')}
        </Button>
      </Tip>

      {one && (
        <ReplaceAssetDialog panel={one} open={replacing} onOpenChange={setReplacing} />
      )}
    </Disclosure>
  )
}

/* -------------------------------------------------------------------------- */
/*  诊断（折叠，摘要常显）：等效字号 / 等效 DPI                                   */
/* -------------------------------------------------------------------------- */

interface Quality {
  id: string
  label: string
  value: string
  hint: string
  bad: boolean
}

function qualityOf(o: PanelObject): Quality {
  const fullW = panelFullSize(o).w
  if (o.fileKind === 'pdf') {
    const pt = effectivePt(fullW, o.nativeW)
    const bad = pt < 6
    return {
      id: o.id,
      bad,
      label: pn('effectivePt'),
      value: `${round1(pt)} pt`,
      hint: pn(bad ? 'ptHintBad' : 'ptHint', {
        base: BASE_FONT_PT,
        scale: Math.round((fullW / o.nativeW) * 100),
      }),
    }
  }
  const dpi = effectiveDpi(o.pxW ?? 0, fullW)
  const bad = dpi > 0 && dpi < 300
  return {
    id: o.id,
    bad,
    label: pn('effectiveDpi'),
    value: dpi ? `${dpi} dpi` : pn('unknown'),
    hint: pn(bad ? 'dpiHintBad' : 'dpiHint'),
  }
}

function DiagnosticsSection({ objs }: { objs: PanelObject[] }) {
  useTranslation('inspector')
  const [open, setOpen] = useState(false)
  const items = objs.slice(0, 4).map(qualityOf)
  const worst = items.find((q) => q.bad) ?? items[0]
  if (!worst) return null

  return (
    <Disclosure
      title={pn('diagnostics')}
      open={open}
      onToggle={() => setOpen((v) => !v)}
      summary={
        <span className={worst.bad ? 'text-danger' : undefined}>
          {worst.label} {worst.value}
        </span>
      }
    >
      <div className="flex flex-col gap-1">
        {items.map((q) => (
          <Tip key={q.id} label={q.hint} side="left">
            <div
              className={cn(
                'flex h-7 items-center justify-between rounded-sm px-1.5 text-xs',
                q.bad ? 'bg-danger-subtle text-danger' : 'bg-surface-2 text-ink-2',
              )}
            >
              <span>{q.label}</span>
              <span className="font-mono tabular-nums">{q.value}</span>
            </div>
          </Tip>
        ))}
        {objs.length > 4 && (
          <p className="text-xs text-ink-3">{pn('morePanels', { count: objs.length - 4 })}</p>
        )}
      </div>
    </Disclosure>
  )
}

/* -------------------------------------------------------------------------- */
/*  替换素材                                                                   */
/* -------------------------------------------------------------------------- */

function ReplaceAssetDialog({
  panel,
  open,
  onOpenChange,
}: {
  panel: PanelObject
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  useTranslation('inspector')
  const panels = useAssetStore((s) => s.panels)
  const loaded = useAssetStore((s) => s.loaded)
  const loading = useAssetStore((s) => s.loading)
  const [q, setQ] = useState('')

  useEffect(() => {
    if (open && !loaded && !loading) void useAssetStore.getState().load()
  }, [open, loaded, loading])

  const list = useMemo(() => {
    const k = q.trim().toLowerCase()
    return panels.filter((p) => !k || (p.name + p.id).toLowerCase().includes(k))
  }, [panels, q])

  const pick = async (info: PanelInfo) => {
    if (await replacePanelAsset(panel.id, info)) onOpenChange(false)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={pn('replaceTitle')}
      description={pn('replaceDescription')}
      size="md"
    >
      <TextInput
        autoFocus
        value={q}
        placeholder={pn('searchAssets')}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.stopPropagation()}
      />
      <div className="mt-2 max-h-[46vh] overflow-y-auto">
        {loading && !panels.length && (
          <p className="py-4 text-center text-xs text-ink-3">{pn('loading')}</p>
        )}
        {!loading && !list.length && (
          <p className="py-4 text-center text-xs text-ink-3">{pn('noAssetMatch')}</p>
        )}
        <ul>
          {list.map((info) => (
            <li key={info.id}>
              <button
                type="button"
                disabled={info.id === panel.fileId}
                onClick={() => void pick(info)}
                className={cn(
                  'flex w-full items-center gap-2 rounded-sm px-1.5 py-1 text-left',
                  'hover:bg-ink/[.055] disabled:cursor-default disabled:opacity-45 disabled:hover:bg-transparent',
                )}
              >
                <span className="min-w-0 flex-1 truncate text-xs text-ink" title={info.id}>
                  {info.name}
                </span>
                <span className="shrink-0 text-xs text-ink-3">{folderLabel(info.folder)}</span>
                <span className="shrink-0 font-mono text-xs tabular-nums text-ink-3">
                  {translate('measure.cmSize', {
                    w: formatCm(info.native_w_mm),
                    h: formatCm(info.native_h_mm),
                  })}
                </span>
                {info.id === panel.fileId && (
                  <span className="shrink-0 text-xs text-ink-3">{pn('currentAsset')}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </Dialog>
  )
}

/* -------------------------------------------------------------------------- */
/*  图内元素（核心动作） + 源文件（折叠，涉及磁盘写入）                            */
/* -------------------------------------------------------------------------- */

/** ⚡ 可参数化面板：进入图内编辑的入口 + 引擎状态 */
function ScriptSection({ panel }: { panel: PanelObject }) {
  useTranslation('inspector')
  const render = usePanelRender(panel)
  const editing = useUiStore((s) => s.elementPanelId === panel.id)
  // 冷启动是文件级事实（SSE 写在 building 表里），渲染中是本变体的状态
  const buildingFile = useRenderStore((s) => s.building[panel.fileId])
  const building = render?.status === 'rendering' || !!buildingFile
  const cold = !!buildingFile?.cold
  const overrides = panel.overrides.length

  return (
    <Section title={pn('elements')}>
      <div className="flex items-center gap-1.5">
        {/*
          进图内编辑是导航动作，**不能**绑渲染状态：进去本来就不依赖上一次渲染
          完成，而 renderStore 的 busy 一旦因为某次 fetch 没 settle 卡住，
          status 会永远停在 rendering，按钮就被永久 disable，用户连退路都没有。
          构建进度改用下面那行非阻塞提示表达。
        */}
        <Button
          variant="outline"
          size="sm"
          className="min-w-0 flex-1"
          active={editing}
          onClick={() =>
            editing ? useUiStore.getState().setElementPanel(null) : enterElementEdit(panel.id)
          }
        >
          <Pencil size={13} />
          {pn(editing ? 'exitElementEdit' : 'editElements')}
        </Button>
        {overrides > 0 && (
          <Tip label={pn('overrideCount', { count: overrides })}>
            <span className="flex h-7 shrink-0 items-center rounded-sm bg-surface-2 px-1.5 font-mono text-xs tabular-nums text-ink-2">
              {overrides}
            </span>
          </Tip>
        )}
      </div>

      {!editing && building && (
        <p className="mt-1.5 flex items-center gap-1.5 text-xs text-ink-2">
          <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-ink-faint" />
          {pn(cold ? 'coldBuilding' : 'building')}
        </p>
      )}

      {render?.stale && (
        <p className="mt-1.5 text-xs text-danger">{pn('staleScript')}</p>
      )}
    </Section>
  )
}

/**
 * 源文件组：唯一会触碰磁盘上原始文件的入口。
 * 写回不要求正处于图内编辑：退出编辑后选中面板，同样能把修改同步回原图。
 */
export function SourceSection({ panel }: { panel: PanelObject }) {
  useTranslation('inspector')
  const [open, setOpen] = useState(false)
  return (
    <Disclosure
      title={pn('source')}
      open={open}
      onToggle={() => setOpen((v) => !v)}
      summary={panel.script?.split('/').pop()}
    >
      <div className="flex gap-1.5">
        <UpdateSourceButton panel={panel} />
        <HistoryPanel panel={panel} />
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-ink-3">{pn('sourceHint')}</p>
    </Disclosure>
  )
}
