import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { History, RotateCcw, TriangleAlert } from 'lucide-react'
import { msg, t as translate } from '@/i18n'
import {
  backendErrorText,
  fetchHistory,
  historyPreviewUrl,
  restoreHistory,
  type HistoryVersion,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { updateObject } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { Popover } from '../ui/Popover'
import { Tip } from '../ui/Tooltip'

/** 时间线起点：脚本原始状态，后端用 n=-1 表示，永远存在 */
const ORIGIN: HistoryVersion = { n: -1, ts: '', count: 0, patches: [] }

const shortTs = (ts: string) => ts.replace(/^\d{4}-/, '').replace(/:\d{2}$/, '')

/** 本组文案在 inspector:versionHistory.* 下 */
const vh = (key: string, values?: Record<string, unknown>) =>
  translate(`versionHistory.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function HistoryPanel({ panel }: { panel: PanelObject }) {
  useTranslation('inspector')
  const [open, setOpen] = useState(false)

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      width={252}
      align="end"
      trigger={
        <Button variant="outline" size="sm" className="flex-1">
          <History size={13} />
          {vh('trigger')}
        </Button>
      }
    >
      {open && <HistoryBody panel={panel} onDone={() => setOpen(false)} />}
    </Popover>
  )
}

function HistoryBody({ panel, onDone }: { panel: PanelObject; onDone: () => void }) {
  useTranslation('inspector')
  const [versions, setVersions] = useState<HistoryVersion[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<HistoryVersion | null>(null)

  useEffect(() => {
    let alive = true
    fetchHistory(panel.fileId)
      .then((r) => alive && setVersions(r.versions))
      .catch((e) => alive && setError(backendErrorText(e)))
    return () => {
      alive = false
    }
  }, [panel.fileId])

  if (error) return <p className="text-xs text-danger">{vh('loadFailed', { error })}</p>
  if (!versions) return <p className="text-xs text-ink-3">{vh('loading')}</p>
  if (!versions.length) {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-xs text-ink-2">{vh('emptyTitle')}</p>
        <p className="text-xs leading-relaxed text-ink-3">{vh('emptyHint')}</p>
      </div>
    )
  }

  // 起点 + 各版本，末位是当前基线
  const rows = [ORIGIN, ...versions]
  const currentN = versions[versions.length - 1].n

  return (
    <>
      <div className="flex max-h-[44vh] flex-col gap-1 overflow-y-auto">
        {rows.map((v) => (
          <VersionRow
            key={v.n}
            panel={panel}
            version={v}
            isCurrent={v.n === currentN}
            onRestore={() => setConfirming(v)}
          />
        ))}
      </div>

      <RestoreDialog
        panel={panel}
        version={confirming}
        onClose={() => setConfirming(null)}
        onDone={onDone}
      />
    </>
  )
}

function VersionRow({
  panel,
  version,
  isCurrent,
  onRestore,
}: {
  panel: PanelObject
  version: HistoryVersion
  isCurrent: boolean
  onRestore: () => void
}) {
  useTranslation('inspector')
  const isOrigin = version.n < 0
  // 横向排：缩略图窄一点，四五个版本也能一屏看完
  return (
    <div
      className={cn(
        'flex items-stretch gap-2 rounded-sm border p-1',
        isCurrent ? 'border-accent bg-accent-subtle/40' : 'border-border',
      )}
    >
      <img
        loading="lazy"
        src={historyPreviewUrl(panel.fileId, version.n, 320)}
        alt=""
        className="h-14 w-[92px] shrink-0 rounded-[3px] border border-border bg-white object-contain"
      />
      <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
        <p className={cn('truncate text-xs', isCurrent ? 'text-accent' : 'text-ink-2')}>
          {isOrigin ? vh('origin') : vh('editCount', { count: version.count })}
          {isCurrent && vh('currentSuffix')}
        </p>
        {!isOrigin && version.ts && (
          <p className="truncate font-mono text-xs text-ink-3">{shortTs(version.ts)}</p>
        )}
        {!isCurrent && (
          <Tip label={vh('restoreTip')} side="left">
            <Button size="sm" className="-ml-1 self-start text-ink-2" onClick={onRestore}>
              {vh('restore')}
            </Button>
          </Tip>
        )}
      </div>
    </div>
  )
}

function RestoreDialog({
  panel,
  version,
  onClose,
  onDone,
}: {
  panel: PanelObject
  version: HistoryVersion | null
  onClose: () => void
  onDone: () => void
}) {
  const { t } = useTranslation('inspector')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    if (!version) return
    // 写回历史恢复同样是离散动作：先收掉还开着的连续编辑，否则整份
    // overrides 替换会被并进上一条历史（issue #131 的同一条毛病）
    finishActiveGesture()
    setBusy(true)
    setError(null)
    try {
      // 与写回同一条前置校验：素材被工具之外改过就别按旧状态覆盖（409 source_changed）
      const mtime = useAssetStore.getState().byId[panel.fileId]?.mtime
      const res = await restoreHistory(panel.fileId, version.n, mtime)
      // 文件、基线、当前面板的 overrides 三者对齐，否则下次进编辑态又会打架
      updateObject<PanelObject>(panel.id, msg('history.restoreVersion', undefined, 'inspector'), (o) => {
        o.overrides = structuredClone(res.patches)
      })
      await useAssetStore.getState().load()
      useRenderStore.getState().markStale([panel.fileId])
      useUiStore
        .getState()
        .setStatus(msg('versionHistory.restored', { dir: res.backup_dir }, 'inspector'))
      onClose()
      onDone()
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={!!version}
      onOpenChange={(v) => !v && onClose()}
      title={vh('restoreTitle')}
      description={
        version?.n === -1
          ? vh('originDescription')
          : vh('versionDescription', { count: version?.count ?? 0, ts: version?.ts ?? '' })
      }
      size="md"
      busy={busy}
      footer={
        <>
          <Button variant="outline" size="md" disabled={busy} onClick={onClose}>
            {translate('actions.cancel')}
          </Button>
          <Button
            variant="primary"
            size="md"
            loading={busy}
            loadingLabel={vh('rewriting')}
            onClick={run}
          >
            <RotateCcw size={14} />
            {vh('confirmRestore')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <div className="flex items-start gap-1.5 rounded-sm border border-border bg-surface-2 p-2">
          <TriangleAlert size={12} className="mt-0.5 shrink-0 text-danger" />
          <p className="text-xs leading-relaxed text-ink-2">
            {vh('warnBody')}
            <span className="mt-1 block text-ink-3">{vh('warnForward')}</span>
          </p>
        </div>

        {version?.n === -1 && (
          <div className="flex items-start gap-1.5 rounded-sm bg-danger-subtle p-2">
            <TriangleAlert size={12} className="mt-0.5 shrink-0 text-danger" />
            <p className="text-xs leading-relaxed text-danger">
              {/* 句中有 <b> 强调，走 Trans 保留标签而不是把句子切三段 */}
              <Trans
                t={t}
                i18nKey="versionHistory.originWarn"
                components={{ b: <b className="font-medium" /> }}
              />
            </p>
          </div>
        )}
        {error && <p className="text-xs text-danger">{vh('restoreFailed', { error })}</p>}
      </div>
    </Dialog>
  )
}
