import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { TriangleAlert } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { msg, t as translate } from '@/i18n'
import {
  backendErrorText,
  syncOverrides,
  type PanelInfo,
  type SyncPatch,
  type SyncResult,
} from '@/lib/api'
import { setOverrides } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useSyncOverrides } from '@/store/syncOverridesStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { effectiveOverrideIndex } from '@/lib/effectiveOverride'
import { WriteBackDialog } from './UpdateSourceButton'

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
export function siblingGroups(panel: PanelObject, all: PanelInfo[]) {
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

/** 右键菜单那一项的出现条件之一：有同脚本的兄弟图（另一半是这张图有图内修改） */
export function useHasSyncSiblings(panel: PanelObject): boolean {
  const panels = useAssetStore((s) => s.panels)
  const { kin, others } = siblingGroups(panel, panels)
  return kin.length + others.length > 0
}

/** 目标图已有的基线打底，同名 gid+prop 用同步来的覆盖（引擎 last-wins，取生效的那条） */
function mergedWithBaseline(target: PanelInfo, mapped: SyncPatch[]) {
  const merged = [...(target.baked_overrides ?? [])]
  for (const p of mapped.map(clean)) {
    const i = effectiveOverrideIndex(merged, p.gid, p.prop)
    if (i >= 0) merged[i] = p
    else merged.push(p)
  }
  return merged
}

/** 窗口常驻在画布舞台上，开合在 `syncOverridesStore`；出发的面板没了（撤销 / 删除）就关掉 */
export function SyncOverridesHost() {
  const panelId = useSyncOverrides((s) => s.panelId)
  const close = useSyncOverrides((s) => s.close)
  const panel = useDocumentStore((s) =>
    panelId ? s.doc.objects.find((o) => o.id === panelId && o.type === 'panel') : undefined,
  ) as PanelObject | undefined
  if (!panelId || !panel) return null
  return <SyncOverridesDialog key={panelId} panel={panel} onClose={close} />
}

/**
 * 把这张图的图内修改按位置对应搬到同脚本的另一张图上，两步：先选目标图，再看映射结果。
 * 目标图在画布上：合并进那个面板（一条历史）；不在：交给标准写回窗口
 * （`WriteBackDialog`，与属性栏 / ⋯ 同一条写回事务，不再另有一条写回路径）。
 */
function SyncOverridesDialog({ panel, onClose }: { panel: PanelObject; onClose: () => void }) {
  useTranslation('inspector')
  const [target, setTarget] = useState<PanelInfo | null>(null)
  // 映射结果连同它是替哪张目标图算的一起存：用的时候核对身份，对不上就当没有
  const [resultOf, setResultOf] = useState<{ id: string; data: SyncResult } | null>(null)
  // 请求序号：返回 / 换目标会作废在途请求，慢回来的旧响应不许记到新目标名下
  const reqSeq = useRef(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [writing, setWriting] = useState(false)
  const wrote = useRef(false)
  const panels = useAssetStore((s) => s.panels)
  const { kin, others } = siblingGroups(panel, panels)

  // 目标图是否已经在画布上——决定是「合并进那个面板」还是「走写回窗口」
  const onCanvas = useDocumentStore((s) =>
    s.doc.objects.find((o) => o.type === 'panel' && o.fileId === target?.id),
  ) as PanelObject | undefined

  const result = target && resultOf?.id === target.id ? resultOf.data : null
  const mapped = result?.mapped ?? []
  const clamped = mapped.filter((p) => p.clamped).length

  const pick = async (info: PanelInfo) => {
    const seq = ++reqSeq.current
    setTarget(info)
    setResultOf(null)
    setError(null)
    setBusy(true)
    try {
      const data = await syncOverrides(panel.fileId, info.id, panel.overrides)
      if (seq === reqSeq.current) setResultOf({ id: info.id, data })
    } catch (e) {
      if (seq === reqSeq.current) setError(backendErrorText(e))
    } finally {
      if (seq === reqSeq.current) setBusy(false)
    }
  }

  const back = () => {
    reqSeq.current++
    setTarget(null)
    setResultOf(null)
    setError(null)
    setBusy(false)
  }

  const applyToCanvas = () => {
    if (!onCanvas) return
    setOverrides(
      onCanvas.id,
      msg('sync.historyLabel', { name: panel.name ?? sy('sourceFallback') }, 'inspector'),
      mapped.map(clean),
    )
    useUiStore
      .getState()
      .setStatus(
        msg('sync.syncedToCanvas', { count: mapped.length, name: onCanvas.name }, 'inspector'),
      )
    onClose()
  }

  // 目标图不在画布上：拼一个只在写回窗口里用的临时面板，overrides = 目标自己的基线 + 同步来的
  const detachedPanel: PanelObject | null =
    target && !onCanvas
      ? ({
          id: `sync:${target.id}`,
          type: 'panel',
          fileId: target.id,
          fileKind: target.kind,
          nativeW: target.native_w_mm,
          nativeH: target.native_h_mm,
          x: 0,
          y: 0,
          w: 0,
          h: 0,
          script: target.script ?? null,
          name: target.name,
          overrides: mergedWithBaseline(target, mapped),
        } as unknown as PanelObject)
      : null

  return (
    <>
      <Dialog
        open={!writing}
        onOpenChange={(v) => !v && onClose()}
        title={sy('title')}
        description={
          target
            ? `${panel.name ?? panel.fileId} → ${target.name}`
            : (panel.name ?? panel.fileId)
        }
        size="md"
        anchor="sync-overrides"
        footer={
          target ? (
            <>
              <Button variant="secondary" size="md" onClick={back}>
                {sy('back')}
              </Button>
              {onCanvas ? (
                <Button
                  variant="primary"
                  size="md"
                  data-sync="merge"
                  disabled={!mapped.length}
                  onClick={applyToCanvas}
                >
                  {sy('mergeToCanvas')}
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="md"
                  data-sync="write-back"
                  disabled={!mapped.length}
                  onClick={() => setWriting(true)}
                >
                  {sy('syncAndWriteBack')}
                </Button>
              )}
            </>
          ) : (
            <Button variant="secondary" size="md" onClick={onClose}>
              {translate('actions.cancel')}
            </Button>
          )
        }
      >
        {!target && (
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
        )}

        {target && busy && <p className="text-xs text-ink-3">{sy('computing')}</p>}
        {target && error && <p className="text-xs text-danger">{sy('failed', { error })}</p>}

        {target && result && !busy && (
          <div className="flex flex-col gap-2">
            <ul className="flex flex-col gap-1 rounded-sm border border-border bg-surface-2 p-2 text-xs">
              <li className="text-ink">
                {sy('mappedCount')} <span className="tabular-nums">{mapped.length}</span>{' '}
                {sy('itemsSuffix')}
                {clamped > 0 && (
                  <span className="text-ink-2">{sy('clampedNote', { count: clamped })}</span>
                )}
              </li>
              {result.skipped.length > 0 && (
                <li className="text-ink-2">
                  {sy('skippedPrefix')} <span className="tabular-nums">{result.skipped.length}</span>{' '}
                  {sy('skippedSuffix')}
                </li>
              )}
              {result.unmatched.length > 0 && (
                <li className="text-ink-2">
                  {sy('unmatchedPrefix')}{' '}
                  <span className="tabular-nums">{result.unmatched.length}</span>{' '}
                  {sy('unmatchedSuffix')}
                </li>
              )}
            </ul>

            {!mapped.length && <p className="text-xs text-ink-3">{sy('nothingToSync')}</p>}

            {!!mapped.length && !onCanvas && (
              <div className="flex items-start gap-1.5 rounded-sm border border-border bg-surface-2 p-2">
                <TriangleAlert size={ICON_SIZE.sm} className="mt-0.5 shrink-0 text-danger" />
                <p className="text-xs leading-relaxed text-ink-2">{sy('notOnCanvas')}</p>
              </div>
            )}
          </div>
        )}
      </Dialog>

      {detachedPanel && (
        <WriteBackDialog
          panels={[detachedPanel]}
          open={writing}
          detached
          // 写回成功就整条流程结束；取消 / 关掉回到映射结果那一步
          onWritten={() => {
            wrote.current = true
            useRenderStore.getState().markStale([detachedPanel.fileId])
          }}
          onOpenChange={(v) => {
            if (v) return
            setWriting(false)
            if (wrote.current) onClose()
          }}
        />
      )}
    </>
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
      type="button"
      data-sync-target={info.id}
      onClick={() => void onPick(info)}
      title={info.id}
      className="flex h-7 shrink-0 items-center rounded-sm px-1.5 text-left text-xs text-ink hover:bg-surface-hover focus-visible:focus-ring"
    >
      <span className="truncate">{info.name}</span>
    </button>
  )
}
