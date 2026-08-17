import { useEffect, useState } from 'react'
import { History, RotateCcw, TriangleAlert } from 'lucide-react'
import {
  fetchHistory,
  historyPreviewUrl,
  restoreHistory,
  type HistoryVersion,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { updateObject } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
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

export function HistoryPanel({ panel }: { panel: PanelObject }) {
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
          历史
        </Button>
      }
    >
      {open && <HistoryBody panel={panel} onDone={() => setOpen(false)} />}
    </Popover>
  )
}

function HistoryBody({ panel, onDone }: { panel: PanelObject; onDone: () => void }) {
  const [versions, setVersions] = useState<HistoryVersion[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<HistoryVersion | null>(null)

  useEffect(() => {
    let alive = true
    fetchHistory(panel.fileId)
      .then((r) => alive && setVersions(r.versions))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
  }, [panel.fileId])

  if (error) return <p className="text-xs text-danger">读取历史失败：{error}</p>
  if (!versions) return <p className="text-xs text-ink-3">正在读取历史…</p>
  if (!versions.length) {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-xs text-ink-2">还没有写回原始文件的记录</p>
        <p className="text-xs leading-relaxed text-ink-3">
          用「写回原始文件」把图内修改写回文件后，这里会留下可回溯的足迹。
        </p>
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
          {isOrigin ? '脚本原始' : `${version.count} 项修改`}
          {isCurrent && ' · 当前'}
        </p>
        {!isOrigin && version.ts && (
          <p className="truncate font-mono text-xs text-ink-3">{shortTs(version.ts)}</p>
        )}
        {!isCurrent && (
          <Tip label="用这个版本重写原图文件" side="left">
            <Button size="sm" className="-ml-1 self-start text-ink-2" onClick={onRestore}>
              恢复
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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    if (!version) return
    setBusy(true)
    setError(null)
    try {
      // 与写回同一条前置校验：素材被工具之外改过就别按旧状态覆盖（409 source_changed）
      const mtime = useAssetStore.getState().byId[panel.fileId]?.mtime
      const res = await restoreHistory(panel.fileId, version.n, mtime)
      // 文件、基线、当前面板的 overrides 三者对齐，否则下次进编辑态又会打架
      updateObject<PanelObject>(panel.id, '恢复历史版本', (o) => {
        o.overrides = structuredClone(res.patches)
      })
      await useAssetStore.getState().load()
      useRenderStore.getState().markStale([panel.fileId])
      useUiStore
        .getState()
        .setStatus(`已恢复到该版本并重写原图（备份在 ${res.backup_dir}）`)
      onClose()
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={!!version}
      onOpenChange={(v) => !v && onClose()}
      title="恢复到该版本"
      description={version?.n === -1 ? '脚本原始状态' : `${version?.count ?? 0} 项修改 · ${version?.ts ?? ''}`}
      size="md"
      busy={busy}
      footer={
        <>
          <Button variant="outline" size="md" disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            size="md"
            loading={busy}
            loadingLabel="正在重写…"
            onClick={run}
          >
            <RotateCcw size={14} />
            确认恢复
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <div className="flex items-start gap-1.5 rounded-sm border border-border bg-surface-2 p-2">
          <TriangleAlert size={12} className="mt-0.5 shrink-0 text-danger" />
          <p className="text-xs leading-relaxed text-ink-2">
            会用这个版本的修改重新写出原图文件（自动备份到 cache/original_backups）。
            <span className="mt-1 block text-ink-3">
              历史只前进不回卷：这次恢复会追加为一条新记录，随时可以再反悔。
            </span>
          </p>
        </div>

        {version?.n === -1 && (
          <div className="flex items-start gap-1.5 rounded-sm bg-danger-subtle p-2">
            <TriangleAlert size={12} className="mt-0.5 shrink-0 text-danger" />
            <p className="text-xs leading-relaxed text-danger">
              「脚本原始」是用脚本<b className="font-medium">当前</b>输出重新渲染，不是还原原始文件的字节。
              如果原图曾被手工处理过、或脚本后来改动过，恢复后就与原文件不再一致，
              只能从备份目录取回。
            </p>
          </div>
        )}
        {error && <p className="text-xs text-danger">恢复失败：{error}</p>}
      </div>
    </Dialog>
  )
}
