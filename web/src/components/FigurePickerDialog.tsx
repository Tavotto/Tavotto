import { useTranslation } from 'react-i18next'
import { Play } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Badge } from './ui/Badge'
import { panelSrc, type PanelInfo, type RuntimeAssetInfo } from '@/lib/api'
import { stemOf } from '@/lib/openRequest'
import { formatCm } from '@/lib/units'
import { msg, t as translate } from '@/i18n'
import { addPanel, addRuntimePanel } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'

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
 * 现算：磁盘原件是 FileAsset（addPanel），没有原件的是 RuntimeFigureAsset
 * （addRuntimePanel，只走描述符）；没跑出预览的条目不给假按钮，指去素材库。
 */
export function FigurePickerDialog() {
  useTranslation('project')
  const script = useFigurePickerStore((s) => s.script)
  const close = useFigurePickerStore((s) => s.close)
  const panels = useAssetStore((s) => s.panels)
  const assets = useRuntimeAssetStore((s) => s.assets)
  const nonce = useRuntimeAssetStore((s) => s.previewNonce)
  const setStatus = useUiStore((s) => s.setStatus)

  if (!script) return null

  const entries: Entry[] = [
    ...panels
      .filter((p) => p.script === script)
      .map((p): Entry => ({ kind: 'panel', stem: stemOf(p.id), info: p })),
    ...(assets ?? [])
      .filter((a) => a.script === script)
      .map((a): Entry => ({ kind: 'runtime', stem: a.stem, asset: a })),
  ].sort((a, b) => a.stem.localeCompare(b.stem))

  const pickEntry = (e: Entry) => {
    const fileId = e.kind === 'panel' ? e.info.id : e.asset.id
    const existing = useDocumentStore
      .getState()
      .doc.objects.find((o) => o.type === 'panel' && o.fileId === fileId)
    if (existing) {
      useSelectionStore.getState().set([existing.id])
    } else if (e.kind === 'panel') {
      addPanel(e.info)
    } else {
      // runtime 条目没有描述符时按钮根本不渲染（见下），这里必然有
      addRuntimePanel(e.asset.descriptor!)
    }
    setStatus(msg('handoff.added', { name: e.stem }, 'project'))
    close()
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v) close()
      }}
      title={fp('title', { script })}
      description={fp('description', { count: entries.length })}
      size="md"
    >
      {entries.length === 0 ? (
        <p className="text-xs leading-relaxed text-ink-3">{fp('empty')}</p>
      ) : (
        <ul className="grid grid-cols-2 gap-3" aria-label={fp('listAria')}>
          {entries.map((e) => (
            <li
              key={e.kind === 'panel' ? e.info.id : e.asset.id}
              className="flex flex-col overflow-hidden rounded-md border border-border bg-surface"
            >
              <FigureThumb entry={e} nonce={nonce} />
              <div className="flex min-w-0 flex-1 flex-col gap-2 px-3 py-2">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" title={e.stem}>
                    {e.stem}
                  </span>
                  {e.kind === 'runtime' && (
                    <Badge>{fp('runtimeBadge')}</Badge>
                  )}
                </div>
                <div className="mt-auto flex flex-wrap items-center gap-2">
                  <EntrySize entry={e} />
                  <span className="ml-auto flex shrink-0 items-center">
                    {e.kind === 'panel' || e.asset.descriptor ? (
                      <Button variant="secondary" size="sm" onClick={() => pickEntry(e)}>
                        {fp('addToCanvas')}
                      </Button>
                    ) : (
                      // 没跑出预览（cache 被清理/物化失败）：不渲染假按钮，
                      // 如实指去素材库「运行并发现图」
                      <span className="flex items-center gap-1 text-xs text-ink-3">
                        <Play size={ICON_SIZE.xs} />
                        {fp('needsRun')}
                      </span>
                    )}
                  </span>
                </div>
              </div>
            </li>
          ))}
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
  if (!src) return <span className="h-32 w-full shrink-0 border-b border-border bg-surface-2" />
  return (
    <img
      src={src}
      alt=""
      className="h-32 w-full shrink-0 border-b border-border bg-white object-contain p-2"
    />
  )
}

function EntrySize({ entry }: { entry: Entry }) {
  const size: [number, number] | null =
    entry.kind === 'panel'
      ? [entry.info.native_w_mm, entry.info.native_h_mm]
      : entry.asset.size_mm
  if (!size) return null
  return (
    <span className="shrink-0 font-mono text-xs text-ink-3">
      {translate('measure.cmSize', { w: formatCm(size[0]), h: formatCm(size[1]) })}
    </span>
  )
}
