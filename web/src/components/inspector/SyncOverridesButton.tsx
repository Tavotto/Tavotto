import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftRight, TriangleAlert } from 'lucide-react'
import { msg, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import {
  syncOverrides,
  updateSourceFiles,
  type PanelInfo,
  type SyncPatch,
  type SyncResult,
} from '@/lib/api'
import { setOverrides } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { Popover } from '../ui/Popover'

/** 本组文案在 inspector:sync.* 下 */
const sy = (key: string, values?: Record<string, unknown>) =>
  translate(`sync.${key}`, { ns: 'inspector', ...(values ?? {}) })

/** 只留引擎认识的三个字段：clamped 是给用户看的提示，不该写进文档 */
const clean = (p: SyncPatch) => ({ gid: p.gid, prop: p.prop, value: p.value })

/**
 * 同一脚本产出的兄弟图，分两组：
 *  - 直系：stem 与当前图互为前缀（组图 ↔ 它的子图），这是用户真正要找的
 *  - 其它：同脚本但不同系列（一个脚本可能产出十几张不相干的图）
 * 不分组的话直系会被淹没在几十条里。
 */
function siblingGroups(panel: PanelObject, all: PanelInfo[]) {
  const selfName = panel.name ?? ''
  const sibs = all.filter((p) => p.script && p.script === panel.script && p.id !== panel.fileId)
  const isKin = (p: PanelInfo) =>
    !!selfName && (p.name.startsWith(selfName) || selfName.startsWith(p.name))
  const byName = (a: PanelInfo, b: PanelInfo) => a.name.localeCompare(b.name)
  return {
    kin: sibs.filter(isKin).sort(byName),
    others: sibs.filter((p) => !isKin(p)).sort(byName),
  }
}

export function SyncOverridesButton({ panel }: { panel: PanelObject }) {
  useTranslation('inspector')
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState<PanelInfo | null>(null)
  const [result, setResult] = useState<SyncResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const panels = useAssetStore((s) => s.panels)

  const { kin, others } = siblingGroups(panel, panels)
  const siblingCount = kin.length + others.length
  const disabled = !panel.overrides.length || !siblingCount

  const pick = async (info: PanelInfo) => {
    setOpen(false)
    setTarget(info)
    setResult(null)
    setError(null)
    setBusy(true)
    try {
      setResult(await syncOverrides(panel.fileId, info.id, panel.overrides))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Popover
        open={open}
        onOpenChange={setOpen}
        width={228}
        align="end"
        trigger={
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            disabled={disabled}
            title={
              !panel.overrides.length
                ? sy('noOverrides')
                : !siblingCount
                  ? sy('onlyOne')
                  : sy('tip')
            }
          >
            <ArrowLeftRight size={13} />
            {sy('trigger')}
          </Button>
        }
      >
        <div className="flex max-h-[50vh] flex-col gap-0.5 overflow-y-auto">
          {kin.length > 0 && (
            <>
              <p className="px-1 pb-0.5 text-xs text-ink-3">{sy('kinGroup')}</p>
              {kin.map((s) => (
                <SiblingItem key={s.id} info={s} onPick={pick} />
              ))}
            </>
          )}
          {others.length > 0 && (
            <>
              <p className="px-1 pb-0.5 pt-1 text-xs text-ink-3">
                {sy('othersGroup', { count: others.length })}
              </p>
              {others.map((s) => (
                <SiblingItem key={s.id} info={s} onPick={pick} />
              ))}
            </>
          )}
        </div>
      </Popover>

      <ResultDialog
        source={panel}
        target={target}
        result={result}
        busy={busy}
        error={error}
        onClose={() => {
          setTarget(null)
          setResult(null)
          setError(null)
        }}
      />
    </>
  )
}

function ResultDialog({
  source,
  target,
  result,
  busy,
  error,
  onClose,
}: {
  source: PanelObject
  target: PanelInfo | null
  result: SyncResult | null
  busy: boolean
  error: string | null
  onClose: () => void
}) {
  useTranslation('inspector')
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  // 目标图是否已经在画布上——决定是「合并进那个面板」还是「直接写回文件」
  const onCanvas = useDocumentStore((s) =>
    s.doc.objects.find((o) => o.type === 'panel' && o.fileId === target?.id),
  ) as PanelObject | undefined

  const mapped = result?.mapped ?? []
  const clamped = mapped.filter((p) => p.clamped).length

  const applyToCanvas = () => {
    if (!onCanvas) return
    setOverrides(
      onCanvas.id,
      msg('sync.historyLabel', { name: source.name ?? sy('sourceFallback') }, 'inspector'),
      mapped.map(clean),
    )
    useUiStore
      .getState()
      .setStatus(
        msg('sync.syncedToCanvas', { count: mapped.length, name: onCanvas.name }, 'inspector'),
      )
    onClose()
  }

  const applyToFile = async () => {
    if (!target) return
    setApplying(true)
    setApplyError(null)
    try {
      // 目标已有的基线打底，同名 gid+prop 用同步来的覆盖
      const baseline = target.baked_overrides ?? []
      const merged = [...baseline]
      for (const p of mapped.map(clean)) {
        const i = merged.findIndex((x) => x.gid === p.gid && x.prop === p.prop)
        if (i >= 0) merged[i] = p
        else merged.push(p)
      }
      // target 就是素材面板里的那条记录，mtime 直接可用（409 source_changed 的依据）
      const res = await updateSourceFiles(target.id, merged, undefined, target.mtime)
      await useAssetStore.getState().load()
      useRenderStore.getState().markStale([target.id])
      setDone(sy('updatedFiles', { files: listJoin(res.updated), dir: res.backup_dir }))
      useUiStore
        .getState()
        .setStatus(msg('sync.writtenBack', { name: target.name }, 'inspector'))
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : String(e))
    } finally {
      setApplying(false)
    }
  }

  return (
    <Dialog
      open={!!target}
      onOpenChange={(v) => !v && onClose()}
      title={sy('title')}
      description={target ? `${source.name ?? source.fileId} → ${target.name}` : ''}
      size="md"
      busy={applying}
      footer={
        done ? (
          <Button variant="outline" size="md" onClick={onClose}>
            {sy('done')}
          </Button>
        ) : (
          <>
            <Button variant="outline" size="md" disabled={applying} onClick={onClose}>
              {translate('actions.cancel')}
            </Button>
            {onCanvas ? (
              <Button variant="primary" size="md" disabled={!mapped.length} onClick={applyToCanvas}>
                {sy('mergeToCanvas')}
              </Button>
            ) : (
              <Button
                variant="primary"
                size="md"
                disabled={!mapped.length}
                loading={applying}
                loadingLabel={sy('rewriting')}
                onClick={applyToFile}
              >
                {sy('syncAndWriteBack')}
              </Button>
            )}
          </>
        )
      }
    >
      {busy && <p className="text-xs text-ink-3">{sy('computing')}</p>}
      {error && <p className="text-xs text-danger">{sy('failed', { error })}</p>}

      {result && !busy && (
        <div className="flex flex-col gap-2">
          <ul className="flex flex-col gap-1 rounded-sm border border-border bg-surface-2 p-2 text-xs">
            <li className="text-ink">
              {sy('mappedCount')} <span className="font-mono">{mapped.length}</span>{' '}
              {sy('itemsSuffix')}
              {clamped > 0 && (
                <span className="text-ink-2">{sy('clampedNote', { count: clamped })}</span>
              )}
            </li>
            {result.skipped.length > 0 && (
              <li className="text-ink-2">
                {sy('skippedPrefix')} <span className="font-mono">{result.skipped.length}</span>{' '}
                {sy('skippedSuffix')}
              </li>
            )}
            {result.unmatched.length > 0 && (
              <li className="text-ink-2">
                {sy('unmatchedPrefix')} <span className="font-mono">{result.unmatched.length}</span>{' '}
                {sy('unmatchedSuffix')}
              </li>
            )}
          </ul>

          {!mapped.length && (
            <p className="text-xs text-ink-3">{sy('nothingToSync')}</p>
          )}

          {!!mapped.length && !onCanvas && !done && (
            <div className="flex items-start gap-1.5 rounded-sm border border-border bg-surface-2 p-2">
              <TriangleAlert size={12} className="mt-0.5 shrink-0 text-danger" />
              <p className="text-xs leading-relaxed text-ink-2">{sy('notOnCanvas')}</p>
            </div>
          )}

          {done && <p className="text-xs text-ink-2">{done}</p>}
          {applyError && (
            <p className="text-xs text-danger">{sy('updateFailed', { error: applyError })}</p>
          )}
        </div>
      )}
    </Dialog>
  )
}

function SiblingItem({
  info,
  onPick,
}: {
  info: PanelInfo
  onPick: (p: PanelInfo) => void | Promise<void>
}) {
  return (
    <button
      onClick={() => void onPick(info)}
      title={info.id}
      className="flex h-6 shrink-0 items-center rounded-sm px-1.5 text-left text-xs text-ink hover:bg-ink/[.055]"
    >
      <span className="truncate">{info.name}</span>
    </button>
  )
}
