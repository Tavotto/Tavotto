import { useState } from 'react'
import { FileUp, TriangleAlert } from 'lucide-react'
import { updateSourceFiles } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'

const stemOf = (fileId: string) => fileId.split('/').pop()?.replace(/\.[^.]+$/, '') ?? fileId

/**
 * 把当前图内修改按全质量写回 figures 目录里的原始 PDF/PNG。
 * 这是本工具里唯一会改动磁盘原始文件的动作，所以名字直说「写回原始文件」，
 * 并在确认框里把「覆盖什么 / 备份在哪 / 怎么恢复」三件事讲全。
 */
export function UpdateSourceButton({ panel }: { panel: PanelObject }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ updated: string[]; backup_dir: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const stem = stemOf(panel.fileId)
  // 项目级只读：按钮保留但禁用，原因写在 title 里
  const readOnly = useProjectStore((s) => s.project?.settings?.allow_write_back === false)
  const backupDir = useProjectStore((s) => s.project?.backup_dir) ?? 'cache/original_backups'

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await updateSourceFiles(panel.fileId, panel.overrides)
      setResult(res)
      // 重拉面板列表拿到新 mtime；所有图片 URL 带 m 参数，缩略图与画布面板都会自动重取
      await useAssetStore.getState().load()
      useUiStore
        .getState()
        .setStatus(`已写回原始文件：${res.updated.join('、')}（备份在 ${res.backup_dir}）`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="flex-1"
        disabled={!panel.overrides.length || readOnly}
        title={
          readOnly
            ? '该项目已设为只读：不允许写回原始文件（可在项目设置中恢复可写）'
            : panel.overrides.length
              ? '用当前图内修改覆盖 figures 里的原始 PDF/PNG'
              : '还没有可写回的图内修改'
        }
        onClick={() => {
          setResult(null)
          setError(null)
          setOpen(true)
        }}
      >
        <FileUp size={13} />
        写回原始文件
      </Button>

      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="写回原始文件"
        description={`${panel.overrides.length} 处图内修改将覆盖磁盘上的原始文件`}
        size="md"
        busy={busy}
        footer={
          result ? (
            <Button variant="outline" size="md" onClick={() => setOpen(false)}>
              完成
            </Button>
          ) : (
            <>
              <Button variant="outline" size="md" disabled={busy} onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button
                variant="primary"
                size="md"
                loading={busy}
                loadingLabel="正在重出…"
                onClick={run}
              >
                <FileUp size={14} />
                确认写回
              </Button>
            </>
          )
        }
      >
        {result ? (
          <div className="flex flex-col gap-2">
            <p className="text-xs text-ink-2">已更新以下文件：</p>
            <ul className="flex flex-col gap-0.5 rounded-sm border border-border bg-surface-2 p-2">
              {result.updated.map((f) => (
                <li key={f} className="font-mono text-xs text-ink">
                  {f}
                </li>
              ))}
            </ul>
            <p className="text-xs leading-relaxed text-ink-3">
              原文件已备份到
              <span className="mx-1 font-mono text-ink-2">{result.backup_dir}</span>
              需要时可从那里取回。
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex items-start gap-1.5 rounded-sm border border-border bg-surface-2 p-2">
              <TriangleAlert size={12} className="mt-0.5 shrink-0 text-danger" />
              <div className="text-xs leading-relaxed text-ink-2">
                <p>
                  <b className="font-medium text-ink">覆盖</b>：用当前的图内修改重出
                  <span className="mx-1 font-mono text-ink">
                    {stem}.pdf / {stem}.png
                  </span>
                  并替换 figures 目录里的同名文件。
                </p>
                <p className="mt-1">
                  <b className="font-medium text-ink">备份</b>：覆盖前自动把原文件复制到
                  <span className="mx-1 break-all font-mono text-ink-2">{backupDir}</span>
                  按时间戳分目录存放。
                </p>
                <p className="mt-1">
                  <b className="font-medium text-ink">恢复</b>：本面板的「历史」里可回到任一版本；
                  也可直接从备份目录把文件拷回来。生成图的脚本不会被改动。
                </p>
              </div>
            </div>
            {error && <p className="text-xs text-danger">更新失败：{error}</p>}
          </div>
        )}
      </Dialog>
    </>
  )
}
