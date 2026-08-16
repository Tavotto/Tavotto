import { useEffect, useMemo, useState } from 'react'
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
import { useRenderStore } from '@/store/renderStore'
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
  const ids = objs.map((o) => o.id)
  const one = objs.length === 1 ? objs[0] : null
  const locked = objs.every(panelAspectLocked)
  // 原始大小是尺寸的锚点：W/H 是当前值，缩放 % 一律相对它，不相对上一次
  const native = sharedPanel(objs, (o) => `${formatMm(o.nativeW)} × ${formatMm(o.nativeH)}`)
  const scale = sharedPanel(objs, (o) => Math.round((panelFullSize(o).w / o.nativeW) * 100))

  const setEach = (label: string, fn: (o: PanelObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'panel') fn(o)
    })

  /** 多个不同值时按整体偏移，保持相对位置 */
  const setAxis = (axis: 'x' | 'y', v: number) => {
    const label = axis === 'x' ? '修改 X' : '修改 Y'
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
    <Section title="位置与尺寸">
      <Grid2>
        <MmField
          label="X"
          historyLabel="修改 X"
          value={sharedPanel(objs, (o) => o.x)}
          onChange={(v) => setAxis('x', v)}
        />
        <MmField
          label="Y"
          historyLabel="修改 Y"
          value={sharedPanel(objs, (o) => o.y)}
          onChange={(v) => setAxis('y', v)}
        />
      </Grid2>

      <div className="mt-1.5 flex items-center gap-1.5">
        <div className="min-w-0 flex-1">
          <MmField
            label="W"
            historyLabel="修改宽度"
            min={1}
            value={sharedPanel(objs, (o) => o.w)}
            onChange={(v) =>
              setEach('修改宽度', (o) => {
                const k = v / o.w
                o.w = v
                if (panelAspectLocked(o)) o.h *= k
              })
            }
          />
        </div>
        <Tip label={locked ? '宽高比已锁定，改一边另一边跟着变' : '宽高比已解锁，W / H 各改各的'}>
          <Button
            size="icon-sm"
            active={locked}
            aria-pressed={locked}
            aria-label="锁定宽高比"
            onClick={() => setPanelAspectLocked(ids, !locked)}
          >
            {locked ? <Link2 size={12} /> : <Unlink2 size={12} />}
          </Button>
        </Tip>
        <div className="min-w-0 flex-1">
          <MmField
            label="H"
            historyLabel="修改高度"
            min={1}
            value={sharedPanel(objs, (o) => o.h)}
            onChange={(v) =>
              setEach('修改高度', (o) => {
                const k = v / o.h
                o.h = v
                if (panelAspectLocked(o)) o.w *= k
              })
            }
          />
        </div>
      </div>

      <Row className="mt-1.5" label="缩放">
        <NumberField
          value={scale ?? 100}
          mixed={scale === undefined}
          step={1}
          min={5}
          max={500}
          precision={0}
          suffix="%"
          title="相对原始大小的绝对百分比：100% = 原始大小。裁剪不改变缩放基准"
          onChange={(v) =>
            updateObjects(ids, '修改面板缩放', (o) => {
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
              ? `素材自身尺寸 ${formatCm(one.nativeW)}×${formatCm(one.nativeH)} cm；缩放 % 一律相对它计算`
              : '素材自身的尺寸；缩放 % 一律相对它计算'
          }
          side="left"
        >
          <span className="min-w-0 shrink truncate font-mono text-xs text-ink-3">
            {native ? `原始 ${native}` : '多个原始尺寸'}
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
  const [open, setOpen] = useState(false)
  const ids = objs.map((o) => o.id)
  const rot = sharedPanel(objs, panelRotation)
  const opacity = sharedPanel(objs, (o) => Math.round((o.opacity ?? 1) * 100))
  const translucent = objs.some((o) => (o.opacity ?? 1) < 1)
  const flipped = objs.some((o) => o.flipH || o.flipV)

  const setEach = (label: string, fn: (o: PanelObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'panel') fn(o)
    })

  const summaryBits = [
    rot ? `${rot}°` : null,
    flipped ? '已翻转' : null,
    opacity !== undefined && opacity < 100 ? `${opacity}%` : null,
  ].filter(Boolean)

  return (
    <Disclosure
      title="外观"
      open={open}
      onToggle={() => setOpen((v) => !v)}
      summary={summaryBits.length ? summaryBits.join(' · ') : undefined}
    >
      <div className="flex flex-col gap-1.5">
        <Row label="旋转">
          <Segmented
            className="w-full"
            value={rot === undefined ? null : String(rot)}
            onChange={(v) => rotatePanels(ids, Number(v) as PanelRotation)}
            items={ROTATIONS.map((r) => ({
              value: String(r),
              label: `${r}°`,
              tip: '只提供 90° 步进：合成引擎在非 90° 倍数下无法填满目标框',
            }))}
          />
        </Row>

        <Row label="翻转">
          <div className="flex min-w-0 flex-1 gap-1">
            <Button
              variant="outline"
              size="sm"
              className="flex-1"
              active={sharedPanel(objs, (o) => o.flipH === true) === true}
              onClick={() =>
                setEach('水平翻转', (o) => {
                  o.flipH = o.flipH ? undefined : true
                })
              }
            >
              <FlipHorizontal2 size={13} />
              水平
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="flex-1"
              active={sharedPanel(objs, (o) => o.flipV === true) === true}
              onClick={() =>
                setEach('垂直翻转', (o) => {
                  o.flipV = o.flipV ? undefined : true
                })
              }
            >
              <FlipVertical2 size={13} />
              垂直
            </Button>
          </div>
        </Row>

        <Row label="不透明度">
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={opacity ?? 100}
            aria-label="不透明度"
            style={{ accentColor: 'var(--color-accent)' }}
            className="h-4 min-w-0 flex-1 cursor-pointer"
            onPointerDown={() => useDocumentStore.getState().beginTxn('修改不透明度')}
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
            {flipped && translucent
              ? '翻转与不透明度都会让该面板在导出 PDF 里按位图嵌入（矢量文字不再可选中）。'
              : flipped
                ? '翻转的面板在导出 PDF 里按导出 DPI 位图嵌入，矢量文字不再可选中。'
                : '不透明度小于 100% 时，导出 PDF 中该面板以高分辨率位图嵌入，矢量文字不再可选中。'}
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
      title="图片"
      open={effectiveOpen}
      onToggle={() => setOpen((v) => !v)}
      summary={cropped ? '已裁剪' : undefined}
    >
      <div className="flex gap-1.5">
        <Tip label="在画布上拖裁剪框取景">
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
            {cropping ? '完成裁剪' : '裁剪'}
          </Button>
        </Tip>
        {cropped && (
          <Tip label="恢复完整画面">
            <Button
              variant="outline"
              size="sm"
              onClick={() => resetPanelCrop(ids)}
              aria-label="重置裁剪"
            >
              <RotateCcw size={13} />
            </Button>
          </Tip>
        )}
      </div>

      <Grid2 className="mt-1.5">
        <Tip label="整图等比缩进当前框内，框跟着收成图的比例">
          <Button variant="outline" size="sm" className="w-full" onClick={() => fitPanels(ids)}>
            <Minimize2 size={13} />
            完整放入
          </Button>
        </Tip>
        <Tip label="框一点不动，用居中裁剪切掉溢出的部分">
          <Button variant="outline" size="sm" className="w-full" onClick={() => fillPanels(ids)}>
            <Maximize2 size={13} />
            填满框
          </Button>
        </Tip>
        <Tip label="保持宽度，按原始长宽比修正高度（消除拉伸）">
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => restorePanelAspect(ids)}
          >
            <Ratio size={13} />
            原始比例
          </Button>
        </Tip>
        <Tip label="回到素材自身的 mm 尺寸（100% 缩放）">
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => restorePanelNativeSize(ids)}
          >
            <Scaling size={13} />
            原始尺寸
          </Button>
        </Tip>
      </Grid2>

      <Tip label="保留位置、尺寸、裁剪与层级，只换图源">
        <Button
          variant="outline"
          size="sm"
          className="mt-1.5 w-full"
          disabled={!one}
          onClick={() => setReplacing(true)}
        >
          <Replace size={13} />
          替换素材
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
      label: '等效字号',
      value: `${round1(pt)} pt`,
      hint: `原图 ${BASE_FONT_PT}pt × 缩放 ${Math.round((fullW / o.nativeW) * 100)}%${bad ? '，低于 6pt 出版会看不清' : ''}`,
    }
  }
  const dpi = effectiveDpi(o.pxW ?? 0, fullW)
  const bad = dpi > 0 && dpi < 300
  return {
    id: o.id,
    bad,
    label: '等效 DPI',
    value: dpi ? `${dpi} dpi` : '未知',
    hint: bad ? '低于 300dpi，印刷会发虚' : '位图在当前尺寸下的实际分辨率',
  }
}

function DiagnosticsSection({ objs }: { objs: PanelObject[] }) {
  const [open, setOpen] = useState(false)
  const items = objs.slice(0, 4).map(qualityOf)
  const worst = items.find((q) => q.bad) ?? items[0]
  if (!worst) return null

  return (
    <Disclosure
      title="诊断"
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
          <p className="text-xs text-ink-3">另有 {objs.length - 4} 个面板未显示</p>
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
      title="替换素材"
      description="位置、尺寸、裁剪、旋转与层级都会保留；图内修改无法跨脚本搬运，会先征求同意再清空。"
      size="md"
    >
      <TextInput
        autoFocus
        value={q}
        placeholder="搜索文件名…"
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.stopPropagation()}
      />
      <div className="mt-2 max-h-[46vh] overflow-y-auto">
        {loading && !panels.length && <p className="py-4 text-center text-xs text-ink-3">加载中…</p>}
        {!loading && !list.length && (
          <p className="py-4 text-center text-xs text-ink-3">没有匹配的素材</p>
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
                  {formatCm(info.native_w_mm)}×{formatCm(info.native_h_mm)}cm
                </span>
                {info.id === panel.fileId && (
                  <span className="shrink-0 text-xs text-ink-3">当前</span>
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
  const render = useRenderStore((s) => s.byFile[panel.fileId])
  const editing = useUiStore((s) => s.elementPanelId === panel.id)
  const building = render?.status === 'rendering'
  const cold = !!render?.cold
  const overrides = panel.overrides.length

  return (
    <Section title="图内元素">
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
          {editing ? '退出图内编辑' : '编辑图内元素'}
        </Button>
        {overrides > 0 && (
          <Tip label={`该面板有 ${overrides} 项图内修改`}>
            <span className="flex h-7 shrink-0 items-center rounded-sm bg-surface-2 px-1.5 font-mono text-xs tabular-nums text-ink-2">
              {overrides}
            </span>
          </Tip>
        )}
      </div>

      {!editing && building && (
        <p className="mt-1.5 flex items-center gap-1.5 text-xs text-ink-2">
          <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-ink-faint" />
          {cold ? '正在冷启动，可能需要几分钟…' : '正在构建…'}
        </p>
      )}

      {render?.stale && (
        <p className="mt-1.5 text-xs text-danger">脚本已更新，进入编辑会自动重建</p>
      )}
    </Section>
  )
}

/**
 * 源文件组：唯一会触碰磁盘上原始文件的入口。
 * 写回不要求正处于图内编辑：退出编辑后选中面板，同样能把修改同步回原图。
 */
export function SourceSection({ panel }: { panel: PanelObject }) {
  const [open, setOpen] = useState(false)
  return (
    <Disclosure
      title="源文件"
      open={open}
      onToggle={() => setOpen((v) => !v)}
      summary={panel.script?.split('/').pop()}
    >
      <div className="flex gap-1.5">
        <UpdateSourceButton panel={panel} />
        <HistoryPanel panel={panel} />
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-ink-3">
        写回会用当前图内修改覆盖 figures 里的原始 PDF/PNG（自动备份，可从历史恢复）。
      </p>
    </Disclosure>
  )
}
