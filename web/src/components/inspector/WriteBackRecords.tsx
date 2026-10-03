import { refuseArtifactOperation } from '@/lib/artifactValidation'
import { useEffect, useMemo, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { RotateCcw, TriangleAlert } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { msg, t as translate } from '@/i18n'
import {
  backendErrorText,
  fetchHistory,
  historyPreviewUrl,
  restoreHistory,
  type HistoryVersion,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { restorePanelOverrides } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { useRenderStore } from '@/store/renderStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { Select } from '../ui/Select'
import { Tip } from '../ui/Tooltip'

/**
 * 「写回记录」：每次写回之后那张图的状态（带缩略图），可以把磁盘上的原图文件恢复到某一次。
 * 2026-10-01（设计稿 C11）：原来属性栏里的「历史」弹层，改成写回窗口（`WriteBackDialog`）里的一页；
 * 这张图没有记录时这一页不出现，属性栏里的入口也不出现。恢复的逻辑一字未改。
 */

/** 时间线起点：脚本原始状态，后端用 n=-1 表示，永远存在 */
const ORIGIN: HistoryVersion = { n: -1, ts: '', count: 0, patches: [] }

const shortTs = (ts: string) => ts.replace(/^\d{4}-/, '').replace(/:\d{2}$/, '')

const stemOf = (fileId: string) => fileId.split('/').pop()?.replace(/\.[^.]+$/, '') ?? fileId

/** 本组文案在 inspector:versionHistory.* 下 */
const vh = (key: string, values?: Record<string, unknown>) =>
  translate(`versionHistory.${key}`, { ns: 'inspector', ...(values ?? {}) })

/** 读取失败也算「有这一页」：入口悄悄消失会让人以为从没写回过 */
export type RecordsOf = HistoryVersion[] | { error: string }

/**
 * 这些图各自的写回记录。素材的 mtime 一变（写回完成后 `assetStore.load()`）就重取，
 * 所以刚写回完，「写回记录」页会自己冒出来。`enabled` 关着就不发请求。
 */
export function useWriteBackRecords(
  fileIds: readonly string[],
  enabled = true,
): Record<string, RecordsOf> {
  const key = fileIds.join('\n')
  const mtimes = useAssetStore((s) => fileIds.map((f) => s.byId[f]?.mtime ?? 0).join(','))
  const [loaded, setLoaded] = useState<{ key: string; byFile: Record<string, RecordsOf> }>({
    key: '',
    byFile: {},
  })
  useEffect(() => {
    if (!enabled || !key) return
    let alive = true
    const ids = key.split('\n')
    void Promise.all(
      ids.map(
        (id) =>
          fetchHistory(id).then(
            (r): [string, RecordsOf] => [id, r.versions ?? []],
            (e): [string, RecordsOf] => [id, { error: backendErrorText(e) }],
          ),
      ),
    ).then((entries) => {
      if (alive) setLoaded({ key, byFile: Object.fromEntries(entries) })
    })
    return () => {
      alive = false
    }
  }, [key, mtimes, enabled])
  return enabled && loaded.key === key ? loaded.byFile : {}
}

/** 有记录（或读不出来）的那几张图的 fileId，按传入次序 */
export function fileIdsWithRecords(
  fileIds: readonly string[],
  byFile: Record<string, RecordsOf>,
): string[] {
  return fileIds.filter((f) => {
    const r = byFile[f]
    return !!r && (!Array.isArray(r) || r.length > 0)
  })
}

export function WriteBackRecordsPage({
  panels,
  byFile,
  focusFileId,
  onDone,
}: {
  panels: PanelObject[]
  byFile: Record<string, RecordsOf>
  /** 预先选好的那张图（属性栏入口带来的） */
  focusFileId?: string
  /** 恢复成功之后（窗口随之关掉） */
  onDone: () => void
}) {
  useTranslation('inspector')
  const files = useMemo(
    () => fileIdsWithRecords([...new Set(panels.map((p) => p.fileId))], byFile),
    [panels, byFile],
  )
  const [picked, setPicked] = useState<string | null>(null)
  const fileId =
    (picked && files.includes(picked) && picked) ||
    (focusFileId && files.includes(focusFileId) && focusFileId) ||
    files[0]
  const panel = panels.find((p) => p.fileId === fileId)
  const records = fileId ? byFile[fileId] : undefined
  const [confirming, setConfirming] = useState<HistoryVersion | null>(null)
  if (!fileId || !panel || !records) return null

  if (!Array.isArray(records)) {
    return <p className="text-xs text-danger">{vh('loadFailed', { error: records.error })}</p>
  }
  // 起点 + 各版本，末位是当前基线
  const rows = [ORIGIN, ...records]
  const currentN = records[records.length - 1].n

  return (
    <div data-write-back-page="records" className="flex flex-col gap-2">
      {files.length > 1 && (
        <Select
          value={fileId}
          onChange={setPicked}
          ariaLabel={vh('pickFigure')}
          options={files.map((f) => ({ value: f, label: stemOf(f) }))}
        />
      )}
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
    </div>
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
  // 恢复会重写原图：项目关了「允许写回原始文件」时与写回同一条规矩，禁用并把原因写在项里
  // （2026-10-01 用户拍板；此前的「历史」弹层没有这道守卫）
  const readOnly = useProjectStore((s) => s.project?.settings?.allow_write_back === false)
  const isOrigin = version.n < 0
  // 横向排：缩略图窄一点，四五个版本也能一屏看完
  return (
    <div
      className={cn(
        'flex items-stretch gap-2 rounded-sm border p-1',
        isCurrent ? 'border-border-strong bg-selected' : 'border-border',
      )}
    >
      <img
        loading="lazy"
        src={historyPreviewUrl(panel.fileId, version.n, 320)}
        alt=""
        className="h-14 w-[92px] shrink-0 rounded-xs border border-border bg-white object-contain"
      />
      <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
        <p className={cn('truncate text-xs', isCurrent ? 'text-ink' : 'text-ink-2')}>
          {isOrigin ? vh('origin') : vh('editCount', { count: version.count })}
          {isCurrent && vh('currentSuffix')}
        </p>
        {!isOrigin && version.ts && (
          <p className="truncate text-xs tabular-nums text-ink-3">{shortTs(version.ts)}</p>
        )}
        {!isCurrent && readOnly && (
          <>
            <Button
              size="sm"
              data-write-back="restore"
              className="-ml-1 self-start text-ink-2"
              disabled
            >
              {vh('restore')}
            </Button>
            <p data-write-back="restore-reason" className="text-xs text-ink-3">
              {vh('readOnlyReason')}
            </p>
          </>
        )}
        {!isCurrent && !readOnly && (
          <Tip label={vh('restoreTip')} side="left">
            <Button
              size="sm"
              data-write-back="restore"
              className="-ml-1 self-start text-ink-2"
              onClick={onRestore}
            >
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
    // 写回记录恢复同样是离散动作：先收掉还开着的连续编辑，否则整份
    // overrides 替换会被并进上一条历史（issue #131 的同一条毛病）
    finishActiveGesture()
    setBusy(true)
    setError(null)
    try {
      // 与写回同一条前置校验：素材被工具之外改过就别按旧状态覆盖（409 source_changed）
      refuseArtifactOperation(panel)
      const mtime = useAssetStore.getState().byId[panel.fileId]?.mtime
      const res = await restoreHistory(panel.fileId, version.n, mtime)
      // 文件、基线、当前面板的 overrides 三者对齐，否则下次进编辑态又会打架
      // 恢复来的条目带着它们写下那一刻的身份，原样放回（ADR 0083）
      restorePanelOverrides(panel.id, msg('history.restoreVersion', undefined, 'inspector'), res.patches)
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
          <Button variant="secondary" size="md" disabled={busy} onClick={onClose}>
            {translate('actions.cancel')}
          </Button>
          <Button
            variant="primary"
            size="md"
            loading={busy}
            loadingLabel={vh('rewriting')}
            onClick={run}
          >
            <RotateCcw size={ICON_SIZE.md} />
            {vh('confirmRestore')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <div className="flex items-start gap-1.5 rounded-sm border border-border bg-surface-2 p-2">
          <TriangleAlert size={ICON_SIZE.sm} className="mt-0.5 shrink-0 text-danger" />
          <p className="text-xs leading-relaxed text-ink-2">
            {vh('warnBody')}
            <span className="mt-1 block text-ink-3">{vh('warnForward')}</span>
          </p>
        </div>

        {version?.n === -1 && (
          <div className="flex items-start gap-1.5 rounded-sm bg-danger-subtle p-2">
            <TriangleAlert size={ICON_SIZE.sm} className="mt-0.5 shrink-0 text-danger" />
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
