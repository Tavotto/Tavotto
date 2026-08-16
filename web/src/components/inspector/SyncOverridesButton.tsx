import { useState } from 'react'
import { ArrowLeftRight, TriangleAlert } from 'lucide-react'
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
                ? '当前面板还没有图内修改'
                : !siblingCount
                  ? '这个脚本只产出这一张图'
                  : '把当前图内修改映射到同一脚本的其它图（组图↔子图双向都可以，从哪张发起就是哪个方向）'
            }
          >
            <ArrowLeftRight size={13} />
            同步修改到…
          </Button>
        }
      >
        <div className="flex max-h-[50vh] flex-col gap-0.5 overflow-y-auto">
          {kin.length > 0 && (
            <>
              <p className="px-1 pb-0.5 text-xs text-ink-3">同一组图</p>
              {kin.map((s) => (
                <SiblingItem key={s.id} info={s} onPick={pick} />
              ))}
            </>
          )}
          {others.length > 0 && (
            <>
              <p className="px-1 pb-0.5 pt-1 text-xs text-ink-3">
                同脚本的其它图（{others.length}）
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
    setOverrides(onCanvas.id, `同步自 ${source.name ?? '源图'}`, mapped.map(clean))
    useUiStore.getState().setStatus(`已把 ${mapped.length} 项修改同步到画布上的 ${onCanvas.name}`)
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
      const res = await updateSourceFiles(target.id, merged)
      await useAssetStore.getState().load()
      useRenderStore.getState().markStale([target.id])
      setDone(`已更新 ${res.updated.join('、')}（备份在 ${res.backup_dir}）`)
      useUiStore.getState().setStatus(`已同步并写回原始文件：${target.name}`)
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
      title="同步修改"
      description={target ? `${source.name ?? source.fileId} → ${target.name}` : ''}
      size="md"
      busy={applying}
      footer={
        done ? (
          <Button variant="outline" size="md" onClick={onClose}>
            完成
          </Button>
        ) : (
          <>
            <Button variant="outline" size="md" disabled={applying} onClick={onClose}>
              取消
            </Button>
            {onCanvas ? (
              <Button variant="primary" size="md" disabled={!mapped.length} onClick={applyToCanvas}>
                合并到画布上的面板
              </Button>
            ) : (
              <Button
                variant="primary"
                size="md"
                disabled={!mapped.length}
                loading={applying}
                loadingLabel="正在重出…"
                onClick={applyToFile}
              >
                同步并写回原始文件
              </Button>
            )}
          </>
        )
      }
    >
      {busy && <p className="text-xs text-ink-3">正在计算映射…</p>}
      {error && <p className="text-xs text-danger">同步失败：{error}</p>}

      {result && !busy && (
        <div className="flex flex-col gap-2">
          <ul className="flex flex-col gap-1 rounded-sm border border-border bg-surface-2 p-2 text-xs">
            <li className="text-ink">
              可映射 <span className="font-mono">{mapped.length}</span> 项
              {clamped > 0 && (
                <span className="text-ink-2">（其中 {clamped} 项位置已按目标版面折算，可能需微调）</span>
              )}
            </li>
            {result.skipped.length > 0 && (
              <li className="text-ink-2">
                跳过 <span className="font-mono">{result.skipped.length}</span> 项：版面几何（子图占比 / 图幅）不可跨图搬运
              </li>
            )}
            {result.unmatched.length > 0 && (
              <li className="text-ink-2">
                无对应 <span className="font-mono">{result.unmatched.length}</span> 项：目标图里找不到这些元素
              </li>
            )}
          </ul>

          {!mapped.length && (
            <p className="text-xs text-ink-3">没有可同步的项，取消即可。</p>
          )}

          {!!mapped.length && !onCanvas && !done && (
            <div className="flex items-start gap-1.5 rounded-sm border border-border bg-surface-2 p-2">
              <TriangleAlert size={12} className="mt-0.5 shrink-0 text-danger" />
              <p className="text-xs leading-relaxed text-ink-2">
                目标图不在画布上，只能直接写回它的原图文件（会与它已有的基线合并，自动备份）。
                想先看效果的话，把它拖进画布再同步。
              </p>
            </div>
          )}

          {done && <p className="text-xs text-ink-2">{done}</p>}
          {applyError && <p className="text-xs text-danger">更新失败：{applyError}</p>}
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
