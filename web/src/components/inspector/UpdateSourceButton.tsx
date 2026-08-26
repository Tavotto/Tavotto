import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FileUp, ShieldAlert, TriangleAlert } from 'lucide-react'
import { ApiError, updateSourceFiles, type WriteBackDiff } from '@/lib/api'
import { msg, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import {
  annotationsBlocked,
  collectPanelAnnotations,
  type PanelAnnotations,
} from '@/lib/writeBackAnnotations'
import { isJustBakedBaseline } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'

const stemOf = (fileId: string) => fileId.split('/').pop()?.replace(/\.[^.]+$/, '') ?? fileId

/** 本组文案在 inspector:writeBack.* 下 */
const wb = (key: string, values?: Record<string, unknown>) =>
  translate(`writeBack.${key}`, { ns: 'inspector', ...(values ?? {}) })

/**
 * 把图内修改按全质量写回 figures 目录里的原始 PDF/PNG。
 * 这是本工具里唯一会改动磁盘原始文件的动作，所以名字直说「写回原始文件」，
 * 并在确认框里把「覆盖什么 / 备份在哪 / 怎么恢复」三件事讲全。
 *
 * 两个入口共用同一个确认对话框：
 * - 属性页里的 UpdateSourceButton（单面板，随选中面板出现）
 * - 顶栏的 WriteBackTopBarButton（高频动作常驻在导出旁，可一次写回多个面板）
 */

interface WriteBackResult {
  updated: string[]
  backup_dir: string
  /** 与热态逐元素比对过的元素总数；null = 本次没有可对照的热态基准 */
  verified: number | null
  /** 落盘后页面尺寸与重放 manifest 对不上的文件（文件已替换，备份仍在） */
  sizeMismatch: boolean
}

/** 写回失败：把后端的结构化错误体一路带到界面，好按 code 给专属文案 */
class WriteBackFailure extends Error {
  api: ApiError | null
  constructor(message: string, api: ApiError | null) {
    super(message)
    this.api = api
  }
}

/** 多面板顺序写回；单条失败即停，把已完成的部分与失败原因都讲清楚 */
async function runWriteBack(
  panels: PanelObject[],
  mtimeOf: (fileId: string) => number | undefined,
  annotations?: Map<string, PanelAnnotations>,
): Promise<WriteBackResult> {
  const updated: string[] = []
  let backupDir = ''
  let verified: number | null = 0
  let sizeMismatch = false
  for (const p of panels) {
    try {
      const res = await updateSourceFiles(
        p.fileId,
        p.overrides,
        annotations?.get(p.id)?.objects,
        mtimeOf(p.fileId),
      )
      updated.push(...res.updated)
      backupDir = res.backup_dir
      // 一个面板没比上，整批就不能宣称「已通过干净重放校验」
      verified =
        verified === null || res.verification.replay !== 'ok'
          ? null
          : verified + res.verification.elements
      if (res.post_check === 'size_mismatch') sizeMismatch = true
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e)
      throw new WriteBackFailure(
        updated.length
          ? wb('failedPartial', {
              stem: stemOf(p.fileId),
              error: detail,
              done: listJoin(updated),
            })
          : wb('failed', { stem: stemOf(p.fileId), error: detail }),
        e instanceof ApiError ? e : null,
      )
    }
  }
  return { updated, backup_dir: backupDir, verified, sizeMismatch }
}

/**
 * 被阻断的写回：按 code 给可执行的下一步。这三条都不是「重试一次就好」的错误，
 * 文案必须说清楚「为什么被拦」和「该做什么」，否则用户只会反复点确认。
 */
function BlockedNotice({ error }: { error: WriteBackFailure }) {
  useTranslation('inspector')
  const body = (error.api?.body ?? {}) as {
    code?: string
    file?: string
    script?: string
    diffs?: WriteBackDiff[]
  }
  // 后端只给稳定 code + 参数，人话在前端按当前语言拼（见 docs/i18n.md）
  const detail =
    body.code === 'source_changed'
      ? wb('sourceChanged', { file: body.file ?? '' })
      : body.code === 'script_changed'
        ? wb('scriptChanged', { script: body.script ?? '' })
        : null

  if (body.code === 'replay_divergence') {
    const diffs = body.diffs ?? []
    return (
      <div className="flex flex-col gap-1.5 rounded-sm border border-danger/40 bg-surface-2 p-2">
        <p className="flex items-start gap-1.5 text-xs leading-relaxed text-ink">
          <ShieldAlert size={12} className="mt-0.5 shrink-0 text-danger" />
          <span>
            <b className="font-medium">{wb('divergenceTitle')}</b>
            {wb('divergenceBody')}
          </span>
        </p>
        {diffs.length > 0 && (
          <ul className="flex flex-col gap-0.5">
            {diffs.slice(0, 5).map((d, i) => (
              <li key={`${d.gid}-${d.field}-${i}`} className="font-mono text-[11px] text-ink-2">
                {d.gid || 'figure'}.{d.field}
              </li>
            ))}
            {diffs.length > 5 && (
              <li className="text-[11px] text-ink-3">{wb('moreDiffs', { count: diffs.length - 5 })}</li>
            )}
          </ul>
        )}
      </div>
    )
  }
  if (detail) {
    return (
      <p className="rounded-sm border border-border bg-surface-2 p-2 text-xs leading-relaxed text-ink-2">
        {detail}
      </p>
    )
  }
  return <p className="text-xs text-danger">{wb('updateFailed', { error: error.message })}</p>
}

export function WriteBackDialog({
  panels,
  open,
  onOpenChange,
}: {
  panels: PanelObject[]
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  useTranslation('inspector')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<WriteBackResult | null>(null)
  const [error, setError] = useState<WriteBackFailure | null>(null)
  const [withAnnotations, setWithAnnotations] = useState(false)
  const backupDir = useProjectStore((s) => s.project?.backup_dir) ?? 'cache/original_backups'
  const objects = useDocumentStore((s) => s.doc.objects)
  const assets = useAssetStore((s) => s.byId)
  const stems = panels.map((p) => stemOf(p.fileId))

  // 与写回目标重叠的画布标注（按重叠面积归属，一条只进一张图）
  const annMap = useMemo(
    () => (open ? collectPanelAnnotations(panels, objects) : new Map<string, PanelAnnotations>()),
    [open, panels, objects],
  )
  const annCount = [...annMap.values()].reduce((n, a) => n + a.objectIds.length, 0)
  // 「有标注压着面板却带不走」才值得说一句；面板上本来就没标注不用提
  const blockedReason = useMemo(() => {
    if (panels.length !== 1) return null
    const reason = annotationsBlocked(panels[0])
    if (!reason) return null
    const p = panels[0]
    const touching = objects.some(
      (o) =>
        (o.type === 'text' || o.type === 'arrow' || o.type === 'shape') &&
        !o.hidden &&
        o.x < p.x + p.w && o.x + o.w > p.x && o.y < p.y + p.h && o.y + o.h > p.y,
    )
    return touching ? reason : null
  }, [panels, objects])

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      const useAnn = withAnnotations && annCount > 0
      const res = await runWriteBack(
        panels,
        (fileId) => assets[fileId]?.mtime,
        useAnn ? annMap : undefined,
      )
      setResult(res)
      if (useAnn) {
        // 标注已经烙进原图：画布上的原件移除（可撤销），否则成图里会出现两份
        const ids = [...annMap.values()].flatMap((a) => a.objectIds)
        useDocumentStore
          .getState()
          .commit(msg('history.annotationsWrittenBack', { count: ids.length }, 'inspector'), (d) => {
            d.objects = d.objects.filter((o) => !ids.includes(o.id))
          })
        useSelectionStore.getState().clear()
      }
      // 重拉面板列表拿到新 mtime；所有图片 URL 带 m 参数，缩略图与画布面板都会自动重取
      await useAssetStore.getState().load()
      useUiStore
        .getState()
        .setStatus(
          useAnn
            ? msg(
                'writeBack.statusWithAnnotations',
                { files: listJoin(res.updated), count: annCount, dir: res.backup_dir },
                'inspector',
              )
            : msg(
                'writeBack.statusPlain',
                { files: listJoin(res.updated), dir: res.backup_dir },
                'inspector',
              ),
        )
    } catch (e) {
      setError(
        e instanceof WriteBackFailure
          ? e
          : new WriteBackFailure(e instanceof Error ? e.message : String(e), null),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        onOpenChange(v)
        if (!v) {
          setResult(null)
          setError(null)
          setWithAnnotations(false)
        }
      }}
      title={wb('title')}
      description={
        panels.length === 1
          ? wb('descOne', { count: panels[0]?.overrides.length ?? 0 })
          : wb('descMany', { count: panels.length })
      }
      size="md"
      busy={busy}
      footer={
        result ? (
          <Button variant="outline" size="md" onClick={() => onOpenChange(false)}>
            {wb('done')}
          </Button>
        ) : (
          <>
            <Button variant="outline" size="md" disabled={busy} onClick={() => onOpenChange(false)}>
              {translate('actions.cancel')}
            </Button>
            <Button
              variant="primary"
              size="md"
              loading={busy}
              loadingLabel={wb('rewriting')}
              onClick={run}
            >
              <FileUp size={14} />
              {wb('confirm')}
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-ink-2">{wb('updatedIntro')}</p>
          <ul className="flex flex-col gap-0.5 rounded-sm border border-border bg-surface-2 p-2">
            {result.updated.map((f) => (
              <li key={f} className="font-mono text-xs text-ink">
                {f}
              </li>
            ))}
          </ul>
          <p className="text-xs leading-relaxed text-ink-3">
            {wb('backupPrefix')}
            <span className="mx-1 font-mono text-ink-2">{result.backup_dir}</span>
            {wb('backupSuffix')}
          </p>
          {result.verified !== null && (
            <p className="text-xs text-ink-3">{wb('verified', { count: result.verified })}</p>
          )}
          {result.sizeMismatch && (
            <p className="text-xs leading-relaxed text-danger">{wb('sizeMismatch')}</p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex items-start gap-1.5 rounded-sm border border-border bg-surface-2 p-2">
            <TriangleAlert size={12} className="mt-0.5 shrink-0 text-danger" />
            <div className="text-xs leading-relaxed text-ink-2">
              <p>
                <b className="font-medium text-ink">{wb('overwriteLabel')}</b>
                {wb('overwriteBody')}
                <span className="mx-1 font-mono text-ink">
                  {listJoin(stems.map((stem) => `${stem}.pdf / ${stem}.png`))}
                </span>
                {wb('overwriteTail')}
              </p>
              <p className="mt-1">
                <b className="font-medium text-ink">{wb('backupLabel')}</b>
                {wb('backupBody')}
                <span className="mx-1 break-all font-mono text-ink-2">{backupDir}</span>
                {wb('backupTail')}
              </p>
              <p className="mt-1">
                <b className="font-medium text-ink">{wb('restoreLabel')}</b>
                {wb('restoreBody')}
              </p>
            </div>
          </div>
          {annCount > 0 ? (
            <label
              className="flex items-center gap-1.5 text-xs text-ink-2"
              title={wb('annotationsTitle')}
            >
              <Toggle checked={withAnnotations} onChange={setWithAnnotations} />
              {wb('withAnnotations', { count: annCount })}
            </label>
          ) : (
            blockedReason && (
              <p className="text-xs text-ink-3">{wb('annotationsBlocked', { reason: blockedReason })}</p>
            )
          )}
          {error && <BlockedNotice error={error} />}
        </div>
      )}
    </Dialog>
  )
}

/** 属性页入口：作用于当前选中的单个面板 */
export function UpdateSourceButton({ panel }: { panel: PanelObject }) {
  useTranslation('inspector')
  const [open, setOpen] = useState(false)
  // 项目级只读：按钮保留但禁用，原因写在 title 里
  const readOnly = useProjectStore((s) => s.project?.settings?.allow_write_back === false)
  // runtime 素材没有原始图文件：入口**不渲染**（不是禁用——「写回」对它
  // 无从谈起，ADR 0013 §7），后端另有 runtime_asset_has_no_original_artifact
  // 硬拒绝兜底
  if (panel.fileKind === 'runtime') return null

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="flex-1"
        disabled={!panel.overrides.length || readOnly}
        title={wb(
          readOnly
            ? 'readOnlyTitle'
            : panel.overrides.length
              ? 'hasOverridesTitle'
              : 'noOverridesTitle',
        )}
        onClick={() => setOpen(true)}
      >
        <FileUp size={13} />
        {wb('buttonLabel')}
      </Button>
      <WriteBackDialog panels={[panel]} open={open} onOpenChange={setOpen} />
    </>
  )
}

/**
 * 顶栏入口的目标解析：正在图内编辑的面板 > 选中的面板 > 当前画布上
 * 所有带未写回修改的面板。只算「有新东西可写」的——overrides 恰好等于
 * 写回基线的面板磁盘上已是那个样子，再写一遍毫无意义。
 * 同一素材被多个面板引用时只写一次（按画布次序取第一个）。
 */
export function useWriteBackTargets(): PanelObject[] {
  const objects = useDocumentStore((s) => s.doc.objects)
  const selectedIds = useSelectionStore((s) => s.ids)
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  // baked_overrides 变化（写回完成后 load()）要让候选立即重算，否则按钮不熄灭
  const assets = useAssetStore((s) => s.byId)

  return useMemo(() => {
    void assets
    const candidates = objects.filter(
      (o): o is PanelObject =>
        o.type === 'panel' &&
        // runtime 素材没有原件，写回入口整个不出现（后端另有硬拒绝兜底）
        o.fileKind !== 'runtime' &&
        o.overrides.length > 0 &&
        !isJustBakedBaseline(o),
    )
    const pick = (list: PanelObject[]) => {
      const seen = new Set<string>()
      return list.filter((p) => !seen.has(p.fileId) && (seen.add(p.fileId), true))
    }
    const editing = candidates.filter((p) => p.id === elementPanelId)
    if (editing.length) return pick(editing)
    const selected = candidates.filter((p) => selectedIds.includes(p.id))
    if (selected.length) return pick(selected)
    return pick(candidates)
  }, [objects, selectedIds, elementPanelId, assets])
}

/** 顶栏入口：高频动作常驻在「导出」左侧；无可写回内容时禁用而不消失 */
export function WriteBackTopBarButton() {
  useTranslation('inspector')
  const [open, setOpen] = useState(false)
  const targets = useWriteBackTargets()
  const readOnly = useProjectStore((s) => s.project?.settings?.allow_write_back === false)
  const disabled = !targets.length || readOnly

  const tip = readOnly
    ? wb('topBarReadOnly')
    : !targets.length
      ? wb('noOverridesTitle')
      : targets.length === 1
        ? wb('topBarOne', { stem: stemOf(targets[0].fileId) })
        : wb('topBarMany', { count: targets.length })

  return (
    <>
      <Tip label={tip}>
        <Button
          variant="outline"
          size="md"
          disabled={disabled}
          aria-label={wb('buttonLabel')}
          onClick={() => setOpen(true)}
        >
          <FileUp size={14} />
          {targets.length > 1 ? wb('topBarShortCount', { count: targets.length }) : wb('topBarShort')}
        </Button>
      </Tip>
      <WriteBackDialog panels={targets} open={open} onOpenChange={setOpen} />
    </>
  )
}
