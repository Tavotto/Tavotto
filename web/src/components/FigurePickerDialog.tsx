import { useTranslation } from 'react-i18next'
import { useRef } from 'react'
import { Check, Play } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Badge } from './ui/Badge'
import { panelSrc, type PanelInfo, type RuntimeAssetInfo } from '@/lib/api'
import { stemOf } from '@/lib/openRequest'
import { formatCm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { msg, t as translate } from '@/i18n'
import { addPanelToCanvas, addRuntimePanelToCanvas } from '@/store/workspace'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { listRowClass } from './ui/listRow'

const fp = (key: string, values?: Record<string, unknown>) =>
  translate(`figurePicker.${key}`, { ns: 'project', ...(values ?? {}) })

/** 选择器里的一行：磁盘图（FileAsset）或运行时图（RuntimeFigureAsset）。 */
type Entry =
  | { kind: 'panel'; stem: string; info: PanelInfo }
  | { kind: 'runtime'; stem: string; asset: RuntimeAssetInfo }

/**
 * 多 Figure 交接的 Figure 选择器（Session 6）。
 *
 * `tavotto open script.py` 产出不止一张图时打开：**每一张都可见、各自可
 * 添加**，绝不静默选第一张（负向反证 #3 的看护对象）。条目从素材数据源
 * 现算：磁盘原件是 FileAsset（addPanelToCanvas），没有原件的是 RuntimeFigureAsset
 * （addRuntimePanelToCanvas，只走描述符）；没跑出预览的条目不给假按钮，指去素材库。
 */
export function FigurePickerDialog() {
  useTranslation('project')
  const current = useFigurePickerStore((s) => s.script)
  const close = useFigurePickerStore((s) => s.close)
  const panels = useAssetStore((s) => s.panels)
  const assets = useRuntimeAssetStore((s) => s.assets)
  const nonce = useRuntimeAssetStore((s) => s.previewNonce)
  const objects = useDocumentStore((s) => s.doc.objects)
  const setStatus = useUiStore((s) => s.setStatus)
  // 常驻挂载：关的那 90ms 里 script 已经是 null，正文按最后一个脚本画（Dialog 的常驻写法）
  const last = useRef(current)
  if (current) last.current = current
  const script = last.current

  if (!script) return null

  const entries: Entry[] = [
    ...panels
      .filter((p) => p.script === script)
      .map((p): Entry => ({ kind: 'panel', stem: stemOf(p.id), info: p })),
    ...(assets ?? [])
      .filter((a) => a.script === script)
      .map((a): Entry => ({ kind: 'runtime', stem: a.stem, asset: a })),
  ].sort((a, b) => a.stem.localeCompare(b.stem))

  const fileIdOf = (e: Entry) => (e.kind === 'panel' ? e.info.id : e.asset.id)
  const onCanvas = (e: Entry) => objects.some((o) => o.type === 'panel' && o.fileId === fileIdOf(e))
  // runtime 条目没有描述符时没有按钮（见下）
  const addable = (e: Entry) => e.kind === 'panel' || !!e.asset.descriptor
  const remaining = entries.filter((e) => addable(e) && !onCanvas(e))

  const add = (e: Entry) => {
    const existing = useDocumentStore
      .getState()
      .doc.objects.find((o) => o.type === 'panel' && o.fileId === fileIdOf(e))
    if (existing) {
      useSelectionStore.getState().set([existing.id])
      return false
    }
    if (e.kind === 'panel') addPanelToCanvas(e.info)
    // runtime 条目没有描述符时按钮根本不渲染（见下），这里必然有
    else addRuntimePanelToCanvas(e.asset.descriptor!)
    return true
  }
  // 加一张**不关框**（2026-10-07 设计审计 §10.2：此前加一张就关，多图脚本要重开 N 次）：
  // 行尾换成「✓ 已在画布上」，用户自己点「完成」
  const pickEntry = (e: Entry) => {
    add(e)
    setStatus(msg('handoff.added', { name: e.stem }, 'project'), 'done')
  }
  const addAll = () => {
    const added = remaining.filter((e) => add(e))
    if (added.length) setStatus(msg('figurePicker.addedAll', { count: added.length }, 'project'), 'done')
  }

  return (
    <Dialog
      open={!!current}
      onOpenChange={(v) => {
        if (!v) close()
      }}
      onEscape={close}
      title={fp('title', { script })}
      size="md"
      anchor="figure-picker"
      footer={
        entries.length === 0
          ? undefined
          : {
              secondary: (
                <Button
                  variant="secondary"
                  size="lg"
                  data-figure-picker-add-all
                  disabled={remaining.length === 0}
                  onClick={addAll}
                >
                  {fp('addAll', { count: remaining.length })}
                </Button>
              ),
              primary: (
                <Button variant="primary" size="lg" data-figure-picker-done onClick={close}>
                  {fp('done')}
                </Button>
              ),
            }
      }
    >
      {entries.length === 0 ? (
        <p className="text-ink-3">{fp('empty')}</p>
      ) : (
        /* 「挑一张图放上画布」全产品只有一种形态（全面打磨 D46）：缩略图 + 名字 + 尺寸 ‖ [添加到画布]，
           与项目接入状态里的那一列行同形；行是 40px 的列表行（listRowClass） */
        <ul className="-mx-1 flex flex-col gap-0.5" aria-label={fp('listAria')}>
          {entries.map((e) => {
            const placed = onCanvas(e)
            return (
              <li
                key={fileIdOf(e)}
                data-figure-picker-row={e.stem}
                data-on-canvas={placed || undefined}
                className={cn(listRowClass({ size: 'md' }), 'min-h-10 gap-3 px-2 py-1 hover:bg-transparent')}
              >
                <FigureThumb entry={e} nonce={nonce} />
                <div className="min-w-0 flex-1">
                  <p className="flex min-w-0 items-center gap-1.5">
                    <span className="min-w-0 truncate font-mono text-sm text-ink" title={e.stem}>
                      {e.stem}
                    </span>
                    {e.kind === 'runtime' && <Badge>{fp('runtimeBadge')}</Badge>}
                  </p>
                  <EntrySize entry={e} />
                </div>
                <span className="flex shrink-0 items-center">
                  {placed ? (
                    <span className="flex items-center gap-1 text-sm text-ok-content" data-figure-picker-placed>
                      <Check size={ICON_SIZE.sm} aria-hidden />
                      {fp('onCanvas')}
                    </span>
                  ) : addable(e) ? (
                    <Button variant="secondary" size="sm" data-figure-picker-add onClick={() => pickEntry(e)}>
                      {fp('addToCanvas')}
                    </Button>
                  ) : (
                    // 没跑出预览（cache 被清理/物化失败）：不渲染假按钮，
                    // 如实指去素材库「运行并发现图」
                    <span className="flex items-center gap-1 text-sm text-ink-3">
                      <Play size={ICON_SIZE.xs} />
                      {fp('needsRun')}
                    </span>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </Dialog>
  )
}

function FigureThumb({ entry, nonce }: { entry: Entry; nonce: Record<string, number> }) {
  const src =
    entry.kind === 'panel'
      ? panelSrc(entry.info.id, entry.info.kind, 320, entry.info.mtime)
      : entry.asset.cached
        ? panelSrc(entry.asset.id, 'runtime', 320, nonce[entry.asset.id])
        : null
  // 40×30 的一小格：行里的识别记号，不是看图器。底是图自己的白（纸），不是界面色
  const box = 'aspect-[4/3] w-10 shrink-0 rounded-xs ring-1 ring-inset ring-border bg-paper'
  if (!src) return <span className={cn(box, 'bg-surface-2')} aria-hidden />
  return <img src={src} alt="" className={cn(box, 'object-contain')} />
}

function EntrySize({ entry }: { entry: Entry }) {
  const size: [number, number] | null =
    entry.kind === 'panel'
      ? [entry.info.native_w_mm, entry.info.native_h_mm]
      : entry.asset.size_mm
  if (!size) return null
  return (
    <span className="type-meta mt-0.5 block tabular-nums">
      {translate('measure.cmSize', { w: formatCm(size[0]), h: formatCm(size[1]) })}
    </span>
  )
}
