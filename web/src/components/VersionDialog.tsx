import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bookmark, Ellipsis, RotateCcw, RotateCcwClock, X } from '@/components/ui/icons'
import { FIELD_BOX, FIELD_FOCUS } from '@/components/ui/fieldBox'
import { ICON_SIZE } from '@/components/ui/Icon'
import { RetryImg } from '@/components/ui/RetryImg'
import {
  backendErrorText,
  deleteVersion,
  duplicateVersion,
  fetchTimeline,
  fetchVersionDoc,
  panelSrc,
  updateVersion,
  versionThumbUrl,
  type LayoutVersionMeta,
  type TimelineBudget,
} from '@/lib/api'
import { modKey, cn } from '@/lib/utils'
import { resolveRestoreTarget } from '@/lib/versionTarget'
import {
  comparableEarlier,
  versionDisplayName,
  versionSummary,
  versionSummaryText,
} from '@/lib/versionSummary'
import { groupTimeline, versionKind, type TimelineGroup } from '@/lib/timelineGroups'
import { takeCheckpoint } from '@/lib/timelineCheckpoint'
import { formatMessage, msg, t as translate } from '@/i18n'
import { formatDate, formatTime } from '@/i18n/format'
import { restoreLayoutVersion } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { useTimelineStore } from '@/store/timelineStore'
import { useVariantPng } from '@/hooks/useVariantPng'
import {
  documentDigest,
  recordDiagnosticEvent,
  versionHash,
} from '@/diagnostics'
import { askConfirm, useUiStore } from '@/store/uiStore'
import type { FigureDocument, PanelObject } from '@/types/document'
import { canvasToDoc, objectLabel } from '@/types/document'
import {
  CanvasThumb,
  THUMB_OBJECT_LIMIT,
  THUMB_TEXT_CHARS,
} from './CanvasThumb'
import { DrawerCount } from './left/DrawerCount'
import { Badge } from './ui/Badge'
import { Button, IconButton } from './ui/Button'
import { EmptyState } from './ui/EmptyState'
import { TextInput } from './ui/Input'
import { Menu, MenuItem, MenuSeparator } from './ui/Menu'
import { Toggle } from './ui/Toggle'

/**
 * 排版时间线（ADR 0101）—— 右侧抽屉形态，画布保持可见；点一个节点，画布区域
 * 盖一层**只读**的那一刻的排版（`TimelinePreview`），「恢复到这里」才真的写。
 *
 * 与两套已有机制的边界：
 * - 本机自动保存（localStorage）：浏览器里的工作副本，无版本概念 —— 保留不动。
 * - 「写回原始文件」历史（baked_overrides）：作用于单张图的源文件 —— 完全无关。
 * 这里的节点是**整份排版**的服务器快照；恢复只改排版内容（可撤销），
 * 不触碰 figures 里的任何文件。
 */
/** 本抽屉的文案在 dialogs:versions.* 下 */
const vd = (key: string, values?: Record<string, unknown>) =>
  translate(`versions.${key}`, { ns: 'dialogs', ...(values ?? {}) })

export function VersionDrawer() {
  useTranslation(['dialogs', 'common'])
  const open = useUiStore((s) => s.versionsOpen)
  const setOpen = useUiStore((s) => s.setVersionsOpen)
  const docId = useDocumentStore((s) => s.documentId)
  // 版本行第三行「来自画布 X」只在它能区分什么的时候才说（左栏审计 L21）
  const canvases = useDocumentStore((s) => s.canvases)
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  const rev = useTimelineStore((s) => s.rev)
  const preview = useTimelineStore((s) => s.preview)
  const focusNameRequest = useTimelineStore((s) => s.focusNameRequest)

  const [versions, setVersions] = useState<LayoutVersionMeta[]>([])
  const [budget, setBudget] = useState<TimelineBudget | null>(null)
  const [namedOnly, setNamedOnly] = useState(false)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saveName, setSaveName] = useState('')
  const [busy, setBusy] = useState(false)
  const asideRef = useRef<HTMLElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(null)
  const selected = preview?.docId === docId ? preview.meta.id : null

  const reload = useCallback(async () => {
    try {
      // 草图随列表一次带回来：没有位图缩略图的旧节点靠它，而列表端点为了数
      // 对象数本来就已经把整份文件解析过一遍了。尺寸取值来自缩略图组件自己。
      const res = await fetchTimeline(docId, {
        objects: THUMB_OBJECT_LIMIT,
        textChars: THUMB_TEXT_CHARS,
      })
      setVersions(res.versions.slice().reverse()) // 最新在上
      setBudget(res.budget ?? null)
      setError(null)
    } catch (e) {
      setError(backendErrorText(e))
    }
  }, [docId])

  useEffect(() => {
    if (!open) return
    setSaveName('')
    setRenaming(null)
    // 打开时记住触发点，关闭后把焦点还回去
    restoreFocus.current = document.activeElement as HTMLElement | null
    // 焦点进名字框：它是抽屉里第一件能做的事；落在关闭钮上的话，关闭钮的气泡
    // 会一直挂在抽屉头上
    const id = requestAnimationFrame(() => nameRef.current?.focus())
    return () => {
      cancelAnimationFrame(id)
      // 抽屉关了，预览跟着退出：没有抽屉的只读大图没有出口
      useTimelineStore.getState().setPreview(null)
      restoreFocus.current?.focus?.()
    }
  }, [open])

  // 打开、换排版、节点有变（新拍 / 改名 / 删除 / 挂上缩略图）时重取
  useEffect(() => {
    if (open) void reload()
  }, [open, reload, rev])

  // ⌥⌘S / 命令面板「把现在存为命名节点」：抽屉开着时把焦点送进名字框
  useEffect(() => {
    if (open && focusNameRequest) requestAnimationFrame(() => nameRef.current?.focus())
  }, [open, focusNameRequest])

  const select = useCallback(
    (meta: LayoutVersionMeta | null) => {
      const tl = useTimelineStore.getState()
      if (!meta || meta.id === selected) {
        tl.setPreview(null)
        return
      }
      tl.setPreview({ docId, meta, doc: null })
      fetchVersionDoc(docId, meta.id)
        .then((v) => {
          // 期间换了节点 / 退出了预览：这份正文作废
          const cur = useTimelineStore.getState().preview
          if (cur?.docId === docId && cur.meta.id === meta.id) {
            useTimelineStore.getState().setPreview({ docId, meta, doc: v.doc })
          }
        })
        .catch((e) => setError(backendErrorText(e)))
    },
    [docId, selected],
  )

  const saveNamed = async () => {
    const name = saveName.trim()
    if (!name) {
      nameRef.current?.focus()
      return
    }
    setBusy(true)
    try {
      await takeCheckpoint({ auto: false, name, allowEmpty: true })
      setSaveName('')
      useUiStore.getState().setStatus(msg('versions.saved', { name }, 'dialogs'))
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  const groups = useMemo(
    () => groupTimeline(versions, { namedOnly, now: Date.now() }),
    [versions, namedOnly],
  )

  if (!open) return null

  const current = versions.find((v) => v.id === selected) ?? null

  return (
    <aside
      ref={asideRef}
      role="dialog"
      aria-label={vd('drawerLabel')}
      data-timeline-drawer
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !busy) {
          e.stopPropagation()
          // 逐层退出：先退预览，再关抽屉
          if (useTimelineStore.getState().preview) useTimelineStore.getState().setPreview(null)
          else setOpen(false)
        }
      }}
      // 覆盖在画布上的浮板只留投影：`shadow-pop` 自带 1px 环，再画一条实色 border
      // 就是双描边（宪法第一节；左栏审计 L20，左抽屉 overlay 态同改）
      className="absolute inset-y-0 right-0 z-40 flex w-[400px] max-w-[92vw] flex-col bg-surface shadow-pop"
    >
      {/* 抽屉头与左抽屉同一副骨架：36 高、type-section 标题、DrawerCount 计数、IconButton
          关闭钮（左栏审计 L20 / L01 / L13 / L14） */}
      <div className="flex h-9 shrink-0 items-center gap-1.5 px-3">
        <h2 className="type-section">{vd('title')}</h2>
        {versions.length > 0 && <DrawerCount value={versions.length} />}
        <span className="flex-1" />
        <IconButton
          label={vd('close')}
          className="-mr-1.5"
          disabled={busy}
          onClick={() => setOpen(false)}
        >
          <X size={ICON_SIZE.md} className="text-ink-3" />
        </IconButton>
      </div>
      <div className="flex shrink-0 gap-1.5 px-3 pb-2">
        <TextInput
          ref={nameRef}
          value={saveName}
          data-timeline-name-input
          aria-label={vd('versionName')}
          onChange={(e) => setSaveName(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'Enter') void saveNamed()
            if (e.key === 'Escape' && saveName) setSaveName('')
          }}
          placeholder={vd('namePlaceholder')}
          className="min-w-0 flex-1"
        />
        <Button
          variant="secondary"
          size="sm"
          loading={busy}
          disabled={!saveName.trim()}
          data-timeline-save-named
          onClick={saveNamed}
        >
          <Bookmark size={ICON_SIZE.sm} />
          {vd('save')}
        </Button>
      </div>
      <div className="flex shrink-0 items-center justify-between gap-2 px-3 pb-1.5">
        <label id="timeline-named-only" htmlFor="timeline-named-only-toggle" className="text-xs text-ink-2">
          {vd('namedOnly')}
        </label>
        <Toggle
          id="timeline-named-only-toggle"
          aria-labelledby="timeline-named-only"
          checked={namedOnly}
          onChange={setNamedOnly}
        />
      </div>
      {budget?.namedOver && (
        // 命名节点超出字节上限：**不删**，照实说，请用户自己删（ADR 0101）
        <p role="alert" data-timeline-budget className="mx-3 mb-2 rounded-sm bg-warn-subtle px-2 py-1.5 text-xs leading-relaxed text-warn">
          {vd('budgetOver', {
            used: ((budget.namedBytes ?? 0) / 1048576).toFixed(1),
            limit: Math.round(budget.limit / 1048576),
          })}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {versions.length === 0 ? (
          <EmptyState icon={RotateCcwClock} title={vd('emptyTitle')} hint={vd('emptyBody')} />
        ) : groups.length === 0 ? (
          <EmptyState icon={Bookmark} title={vd('noNamed')} />
        ) : (
          <div aria-label={vd('listLabel')} role="list" data-timeline-list>
            {groups.map((g) => (
              <TimelineDay key={g.key} group={g}>
                {g.items.map((v) => (
                  <TimelineRow
                    key={v.id}
                    docId={docId}
                    meta={v}
                    all={versions}
                    selected={v.id === selected}
                    renaming={renaming === v.id}
                    showCanvas={canvases.length > 1 || v.canvasId !== activeCanvasId}
                    onSelect={() => select(v)}
                    onRename={(on) => setRenaming(on ? v.id : null)}
                    onChanged={async () => {
                      await reload()
                    }}
                    onError={setError}
                  >
                    {v.id === selected && current && (
                      <NodeDetail meta={current} versionDoc={preview?.doc ?? null} />
                    )}
                  </TimelineRow>
                ))}
              </TimelineDay>
            ))}
          </div>
        )}
        {error && <p className="px-3 py-2 text-xs text-danger">{error}</p>}
      </div>
    </aside>
  )
}

/** 一天一组：今天 / 昨天 / 具体日期 */
function TimelineDay({ group, children }: { group: TimelineGroup; children: React.ReactNode }) {
  const label =
    group.day.kind === 'today'
      ? vd('today')
      : group.day.kind === 'yesterday'
        ? vd('yesterday')
        : formatDate(group.day.ts)
  return (
    <section role="listitem" data-timeline-day={group.key}>
      <h3 className="sticky top-0 z-10 bg-surface px-3 pb-1 pt-2 text-xs text-ink-3">{label}</h3>
      <ul aria-label={label}>{children}</ul>
    </section>
  )
}

/** 节点的类型标记：命名 = accent 胶囊；关键时刻 = 中性胶囊写是什么时刻；自动 / 手动 = 元数据词 */
function KindMark({ meta }: { meta: LayoutVersionMeta }) {
  const kind = versionKind(meta)
  if (kind === 'named') {
    return (
      <Badge tone="accent" data-timeline-kind="named">
        {vd('kind.named')}
      </Badge>
    )
  }
  if (kind === 'moment' && meta.moment) {
    return (
      <Badge data-timeline-kind="moment" data-timeline-moment={meta.moment}>
        {vd(`moment.${meta.moment}`)}
      </Badge>
    )
  }
  return (
    <span className="shrink-0 type-meta" data-timeline-kind={kind}>
      {vd(`kind.${kind}`)}
    </span>
  )
}

function RowThumb({ docId, meta }: { docId: string; meta: LayoutVersionMeta }) {
  // 位图缩略图 = 拍节点那一刻带 overrides 的样子；旧节点 / 拍图失败的退回草图
  // （画的是当前磁盘素材，回答「哪一版」）；连草图都画不出来留同尺寸占位
  if (meta.thumb) {
    return (
      <img
        src={versionThumbUrl(docId, meta)}
        alt=""
        data-timeline-thumb
        className="h-10 w-14 shrink-0 rounded-xs bg-white object-contain"
      />
    )
  }
  if (meta.sketch) return <CanvasThumb page={meta.sketch.page} objects={meta.sketch.objects} />
  return (
    <span
      aria-hidden
      className="h-10 w-14 shrink-0 rounded-xs border border-dashed border-border"
    />
  )
}

function TimelineRow({
  docId,
  meta: v,
  all,
  selected,
  renaming,
  showCanvas,
  onSelect,
  onRename,
  onChanged,
  onError,
  children,
}: {
  docId: string
  meta: LayoutVersionMeta
  all: LayoutVersionMeta[]
  selected: boolean
  renaming: boolean
  showCanvas: boolean
  onSelect: () => void
  onRename: (on: boolean) => void
  onChanged: () => Promise<void>
  onError: (e: string | null) => void
  children?: React.ReactNode
}) {
  const named = versionKind(v) === 'named'
  const [draft, setDraft] = useState(named ? v.name : '')
  useEffect(() => {
    if (renaming) setDraft(named ? v.name : '')
  }, [renaming, named, v.name])

  const commitName = async () => {
    const name = draft.trim()
    onRename(false)
    if (!name || (named && name === v.name)) return
    try {
      await updateVersion(docId, v.id, { name })
      onError(null)
    } catch (e) {
      onError(backendErrorText(e))
    }
    await onChanged()
  }

  const act = (fn: () => Promise<unknown>) => async () => {
    try {
      await fn()
      onError(null)
    } catch (e) {
      onError(backendErrorText(e))
    }
    await onChanged()
  }

  const remove = async () => {
    const ok = await askConfirm({
      title: msg('versions.deleteTitle', { name: versionDisplayName(v) || formatTime(v.ts) }, 'dialogs'),
      body: msg('versions.deleteBody', undefined, 'dialogs'),
      confirmLabel: msg('actions.delete', undefined, 'common'),
      danger: true,
    })
    if (!ok) return
    if (selected) useTimelineStore.getState().setPreview(null)
    await act(() => deleteVersion(docId, v.id))()
  }

  const index = all.indexOf(v)
  return (
    <li data-timeline-node={v.id} data-timeline-named={named || undefined}>
      <div
        className={cn(
          'group flex items-start gap-1 rounded-sm pr-1.5',
          selected ? 'bg-selected' : 'hover:bg-surface-hover',
        )}
      >
        <button
          type="button"
          onClick={onSelect}
          onDoubleClick={(e) => {
            e.preventDefault()
            onRename(true)
          }}
          aria-pressed={selected}
          data-timeline-row
          className="flex min-w-0 flex-1 items-start gap-2 rounded-sm py-1.5 pl-3 text-left outline-none focus-visible:focus-ring"
        >
          <RowThumb docId={docId} meta={v} />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="flex items-center gap-1.5">
              <span className={cn('shrink-0 text-sm tabular-nums text-ink', selected && 'font-medium')}>
                {formatTime(v.ts)}
              </span>
              {named && <Bookmark size={ICON_SIZE.xs} filled className="shrink-0 text-accent" aria-hidden />}
              {/* 自动节点的名字由后端按时间生成，与这里的时间重复，所以只显示
                  **用户起的**名字（`versionDisplayName`）；命名节点的名字加重 */}
              {!renaming && (
                <span
                  className={cn(
                    'min-w-0 flex-1 truncate text-sm',
                    named ? 'font-medium text-ink' : 'text-ink-2',
                  )}
                  data-timeline-name
                >
                  {versionDisplayName(v)}
                </span>
              )}
              {renaming && <span className="flex-1" />}
              <KindMark meta={v} />
            </span>
            <span className="text-xs text-ink-3">
              {versionSummaryText(versionSummary(v, comparableEarlier(all, index)))
                .map(formatMessage)
                .join(' · ')}
            </span>
            {/* 这一版拍的是哪张画布（R-03）。旧节点没有这个字段，**照实说「不知道」**，
                不猜成当前画布（左栏审计 L21）。 */}
            {showCanvas && (
              <span className="truncate type-meta">
                {v.canvasId
                  ? vd('fromCanvas', { name: v.canvasName || v.canvasId })
                  : vd('fromUnknownCanvas')}
              </span>
            )}
          </span>
        </button>
        <Menu
          align="end"
          trigger={
            <IconButton iconSize="sm" label={vd('more')} className="mt-1.5 shrink-0" data-timeline-more>
              <Ellipsis size={ICON_SIZE.sm} className="text-ink-3" />
            </IconButton>
          }
        >
          <MenuItem onSelect={() => onRename(true)}>{vd(named ? 'rename' : 'name')}</MenuItem>
          {named && (
            <MenuItem onSelect={act(() => updateVersion(docId, v.id, { named: false }))}>
              {vd('unname')}
            </MenuItem>
          )}
          <MenuItem onSelect={act(() => duplicateVersion(docId, v.id))}>{vd('duplicate')}</MenuItem>
          <MenuSeparator />
          <MenuItem onSelect={() => void remove()}>
            <span className="text-danger">{vd('delete')}</span>
          </MenuItem>
        </Menu>
      </div>
      {renaming && (
        <div className="px-3 pb-1.5">
          <input
            autoFocus
            value={draft}
            aria-label={vd('versionName')}
            placeholder={vd('namePlaceholder')}
            data-timeline-rename
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => void commitName()}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
              if (e.key === 'Escape') {
                setDraft(named ? v.name : '')
                onRename(false)
              }
            }}
            // 行内改名框是「可编辑框」那一副（fieldBox，左栏审计 L31）
            className={cn('h-7 w-full px-1.5 outline-none', FIELD_BOX, FIELD_FOCUS)}
          />
        </div>
      )}
      {children}
    </li>
  )
}

/**
 * 恢复落点的四条岔路（R-03）。返回 false = 用户取消，一个字节都不写。
 *
 * 判据本身在 `lib/versionTarget.ts`；这里只负责"每一条该说什么话"。
 * 会覆盖当前画布的那两条（原画布已删除 / 节点没有画布身份）措辞最重，
 * 因为它们是唯一会盖掉**别的画布**内容的路径。
 */
async function confirmRestoreTarget(meta: LayoutVersionMeta): Promise<boolean> {
  const s = useDocumentStore.getState()
  const here = s.canvases.find((c) => c.id === s.activeCanvasId)?.name ?? s.doc.name
  const target = resolveRestoreTarget(meta, s)
  if (target.kind === 'same') return true
  if (target.kind === 'other') {
    return askConfirm({
      title: msg('versions.restoreOtherCanvasTitle', { name: target.name }, 'dialogs'),
      body: msg(
        'versions.restoreOtherCanvasBody',
        { from: target.name, to: here },
        'dialogs',
      ),
      confirmLabel: msg('actions.continue', undefined, 'common'),
    })
  }
  return askConfirm({
    title: msg(
      target.kind === 'missing'
        ? 'versions.restoreMissingCanvasTitle'
        : 'versions.restoreUnknownCanvasTitle',
      undefined,
      'dialogs',
    ),
    body:
      target.kind === 'missing'
        ? msg('versions.restoreMissingCanvasBody', { from: target.from, to: here }, 'dialogs')
        : msg('versions.restoreUnknownCanvasBody', { to: here }, 'dialogs'),
    confirmLabel: msg('actions.continue', undefined, 'common'),
    danger: true,
  })
}

/**
 * 恢复到一个节点（ADR 0101）。抽屉里的按钮与预览横幅上的按钮**共用这一份**。
 *
 * 1. 先收掉还开着的连续编辑（否则这次 commit 会被并进上一条历史，一次撤销同时
 *    吐出「刚才那笔编辑」和「整份恢复」；issue #131）；
 * 2. 落点由**节点自己的画布身份**决定（R-03），要切画布先切；
 * 3. **先把当前状态存成「恢复前」关键时刻节点**——存不下来就不恢复：那个节点是
 *    恢复这件事本身能被撤销的保证（ADR 0101），没有它的恢复就是一次覆盖；
 * 4. 一次 `commit`（`restoreLayoutVersion`）写入：⌘Z 一步退回恢复之前。
 *
 * 返回 true = 写进去了。
 */
export async function restoreNode(
  meta: LayoutVersionMeta,
  versionDoc: FigureDocument,
): Promise<boolean> {
  finishActiveGesture()
  if (!(await confirmRestoreTarget(meta))) return false
  // 原画布还在 → 切过去再写（「恢复前」拍的因此是**即将被覆盖的那张**）；
  // 已删除 / 没有身份 → 用户刚点头同意写进当前画布
  const target = resolveRestoreTarget(meta, useDocumentStore.getState())
  if (target.kind === 'other') useDocumentStore.getState().switchCanvas(target.canvasId)
  const hashBefore = documentDigest(useDocumentStore.getState().doc)
  recordDiagnosticEvent({
    type: 'layout_version.restore.request',
    version: versionHash(meta.id),
    document_hash: hashBefore,
    past_count: useDocumentStore.getState().past.length,
    future_count: useDocumentStore.getState().future.length,
  })
  try {
    await takeCheckpoint({
      auto: true,
      moment: 'before_restore',
      name: vd('beforeRestore', { time: formatTime(Date.now()) }),
      programName: true,
      allowEmpty: true,
    })
  } catch (e) {
    useUiStore
      .getState()
      .setStatus(msg('versions.restoreFailed', { error: backendErrorText(e) }, 'dialogs'), 'error')
    return false
  }
  const label = versionDisplayName(meta) || formatTime(meta.ts)
  // 面板 overrides 的身份原样恢复，不按此刻的 manifest 重抄（ADR 0083）
  restoreLayoutVersion(msg('versions.restoreHistory', { name: label }, 'dialogs'), versionDoc)
  recordDiagnosticEvent({
    type: 'layout_version.restore.complete',
    version: versionHash(meta.id),
    document_hash_before: hashBefore,
    document_hash_after: documentDigest(useDocumentStore.getState().doc),
    auto_backup_created: true,
    past_count: useDocumentStore.getState().past.length,
    future_count: useDocumentStore.getState().future.length,
  })
  useTimelineStore.getState().setPreview(null)
  useUiStore
    .getState()
    .setStatus(msg('versions.restored', { name: label, undo: modKey('Z') }, 'dialogs'))
  return true
}

/* ------------------------------- 节点详情 --------------------------------- */

function NodeDetail({
  meta,
  versionDoc,
}: {
  meta: LayoutVersionMeta
  versionDoc: FigureDocument | null
}) {
  useTranslation(['dialogs', 'common'])
  const activeDoc = useDocumentStore((s) => s.doc)
  const canvases = useDocumentStore((s) => s.canvases)
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  /**
   * 「当前」指的是**这个节点所属画布**的当前内容，不是恰好激活的那张。
   * 拿激活画布去和另一张画布的节点做 diff，差异栏里全是无中生有的
   * "新增 12 个对象"（R-03）。
   */
  const currentDoc = useMemo(() => {
    if (!meta.canvasId || meta.canvasId === activeCanvasId) return activeDoc
    const c = canvases.find((x) => x.id === meta.canvasId)
    return c ? canvasToDoc(c) : activeDoc
  }, [meta.canvasId, activeCanvasId, activeDoc, canvases])

  const diff = useMemo(
    () => (versionDoc ? diffDocs(versionDoc, currentDoc) : []),
    [versionDoc, currentDoc],
  )

  return (
    <div className="flex flex-col gap-2 bg-surface-2/60 px-3 py-2" data-timeline-detail>
      {!versionDoc ? (
        <p className="py-2 text-center text-xs text-ink-3">{vd('loadingSnapshot')}</p>
      ) : (
        <>
          {/* 「恢复到这里」不在这里：选中节点时画布上的预览横幅就有它，一屏两颗填色
              主按钮是同一个动作说两遍（宪法：每个上下文最多一个填色主动作）。这里
              只回答「和现在比变了什么」 */}
          {diff.length === 0 ? (
            <p className="text-xs text-ink-3">{vd('noDiff')}</p>
          ) : (
            <ul className="max-h-48 overflow-y-auto">
              {diff.map((d, i) => (
                <li
                  key={i}
                  className="flex items-start gap-1.5 py-0.5 text-xs leading-relaxed text-ink-2"
                >
                  <span
                    className={cn(
                      'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
                      // 「新增」是语义色 ok，不是 accent（宪法第一节；左栏审计 L33）
                      d.kind === 'add' && 'bg-ok',
                      d.kind === 'remove' && 'bg-danger',
                      d.kind !== 'add' && d.kind !== 'remove' && 'bg-ink-faint',
                    )}
                  />
                  <span>{d.text}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

/* ------------------------------- 只读预览 --------------------------------- */

/**
 * 画布区域上的只读预览（ADR 0101）：选中一个节点时盖在画布上，显示**那一刻**的
 * 排版（面板按那一刻自己的 overrides 出图），横幅上两个出口：恢复到这里 / 退出预览。
 *
 * 它**不是第二个画布**：不挂命中层、不进 documentStore、不 commit——画的是
 * `LayoutSnapshot` 那张静态图。盖住画布是有意的：预览期间在底下的真画布上
 * 拖一下，用户分不清改的是哪一个。
 *
 * 抽屉浮在整行右侧，会盖住画布列的右边一截；预览按抽屉此刻的左边界让出位置，
 * 否则大图的右半边藏在抽屉底下。
 */
export function TimelinePreview() {
  useTranslation(['dialogs'])
  const preview = useTimelineStore((s) => s.preview)
  const docId = useDocumentStore((s) => s.documentId)
  const open = useUiStore((s) => s.versionsOpen)
  const ref = useRef<HTMLDivElement>(null)
  const [rightInset, setRightInset] = useState(0)
  const [approximate, setApproximate] = useState(false)
  const [busy, setBusy] = useState(false)

  const active = open && preview && preview.docId === docId ? preview : null

  useLayoutEffect(() => {
    if (!active) return
    const measure = () => {
      const el = ref.current
      const drawer = document.querySelector<HTMLElement>('[data-timeline-drawer]')
      if (!el || !drawer) return setRightInset(0)
      const a = el.getBoundingClientRect()
      const b = drawer.getBoundingClientRect()
      setRightInset(Math.max(0, Math.min(a.width, a.right - b.left)))
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [active])

  useEffect(() => setApproximate(false), [active?.meta.id])

  if (!active) return null
  const { meta, doc } = active
  const restore = async () => {
    if (!doc) return
    setBusy(true)
    try {
      await restoreNode(meta, doc)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div
      ref={ref}
      role="region"
      aria-label={vd('previewLabel')}
      data-timeline-preview={meta.id}
      className="absolute inset-0 z-30 flex flex-col bg-canvas"
      style={{ paddingRight: rightInset }}
    >
      <div className="flex h-11 shrink-0 items-center gap-2 bg-surface px-3 shadow-card">
        <RotateCcwClock size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-sm text-ink">
          {vd('previewing', { time: `${formatDate(meta.ts)} ${formatTime(meta.ts)}` })}
          {versionDisplayName(meta) && (
            <span className="ml-1.5 font-medium">{versionDisplayName(meta)}</span>
          )}
        </span>
        <Button size="sm" onClick={() => useTimelineStore.getState().setPreview(null)} data-timeline-exit-preview>
          {vd('exitPreview')}
        </Button>
        <Button variant="primary" size="sm" loading={busy} disabled={!doc} onClick={restore} data-timeline-preview-restore>
          <RotateCcw size={ICON_SIZE.sm} />
          {vd('restore')}
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 overflow-auto p-6">
        {doc ? (
          <div className="w-full max-w-[min(100%,900px)]" style={{ maxHeight: '100%' }}>
            <LayoutSnapshot doc={doc} renderOverrides onApproximate={setApproximate} />
          </div>
        ) : (
          <p className="text-xs text-ink-3">{vd('loadingSnapshot')}</p>
        )}
        {approximate && (
          <p className="text-xs leading-relaxed text-ink-3">{vd('previewApproximate')}</p>
        )}
      </div>
    </div>
  )
}

/* ------------------------------ 布局缩略图 -------------------------------- */

/**
 * 真素材缩略：面板用现成的 /api/render 小档位图片按布局摆进等比页面，
 * 文字/形状画轮廓块。不是像素级导出预览，但位置、比例、内容一目了然。
 */
export function LayoutSnapshot({
  doc,
  outline = false,
  renderOverrides = false,
  onApproximate,
}: {
  doc: FigureDocument
  outline?: boolean
  /**
   * 按面板自己的 overrides 出图（而不是磁盘素材）。**只给用户当前展开的那一份
   * 版本详情用**：版本列表里每一条都渲染的话，一次展开就是几十次 matplotlib
   * 往返（heavy 脚本上是分钟级）。
   */
  renderOverrides?: boolean
  /** 有面板的图内修改渲染不出来 —— 调用方据此标注「近似预览」 */
  onApproximate?: (approximate: boolean) => void
}) {
  const assets = useAssetStore((s) => s.byId)
  const { w: pw, h: ph } = doc.page
  return (
    <div
      className={cn(
        'relative w-full overflow-hidden rounded-xs border border-border',
        outline ? 'bg-transparent' : 'bg-white',
      )}
      style={{ aspectRatio: `${pw} / ${ph}` }}
    >
      {doc.objects
        .filter((o) => !o.hidden)
        .map((o) => {
          const style: React.CSSProperties = {
            left: `${(o.x / pw) * 100}%`,
            top: `${(o.y / ph) * 100}%`,
            width: `${(o.w / pw) * 100}%`,
            height: `${(o.h / ph) * 100}%`,
          }
          if (outline) {
            return (
              <div
                key={o.id}
                className="absolute border border-accent"
                style={style}
                title={objectLabel(o)}
              />
            )
          }
          if (o.type === 'panel') {
            return (
              <SnapshotPanel
                key={o.id}
                panel={o}
                style={style}
                mtime={assets[o.fileId]?.mtime}
                renderOverrides={renderOverrides}
                onApproximate={onApproximate}
              />
            )
          }
          if (o.type === 'text') {
            return (
              <div
                key={o.id}
                className="absolute overflow-hidden whitespace-pre leading-none"
                style={{ ...style, fontSize: 6, color: o.color, fontFamily: 'var(--font-doc)' }}
              >
                {o.text}
              </div>
            )
          }
          return <div key={o.id} className="absolute border border-ink-faint" style={style} />
        })}
    </div>
  )
}

/**
 * 缩略图里的一个面板。
 *
 * `renderOverrides` 打开时按这一版**自己的 overrides** 出图——这正是布局版本
 * 时间线以前缺的东西：只画磁盘素材的话，两个图内布局完全不同的版本长得一模
 * 一样（issue #131）。出不来就退回磁盘图并把 `approximate` 报上去，由外面
 * 明确标注，绝不无提示地拿原图冒充版本视觉状态。
 */
function SnapshotPanel({
  panel,
  style,
  mtime,
  renderOverrides,
  onApproximate,
}: {
  panel: PanelObject
  style: React.CSSProperties
  mtime?: number
  renderOverrides: boolean
  onApproximate?: (approximate: boolean) => void
}) {
  const variant = useVariantPng(
    panel.fileId,
    panel.overrides,
    200,
    renderOverrides && panel.overrides.length > 0,
    mtime ?? 0,
  )
  useEffect(() => {
    if (renderOverrides) onApproximate?.(variant.approximate)
  }, [variant.approximate, renderOverrides, onApproximate])

  // panelSrc 可能给不出地址（替代传输里没有 HTTP 服务）：一个都拿不到就不画
  // <img>，绝不留一个空 src 让缩略图挂一个碎图标
  const src = variant.url || panelSrc(panel.fileId, panel.fileKind, 200, mtime)
  if (!src) return null
  return (
    <RetryImg
      src={src}
      alt=""
      className="absolute object-fill"
      style={{
        ...style,
        transform: panel.rotation ? `rotate(${panel.rotation}deg)` : undefined,
        opacity: panel.opacity,
      }}
    />
  )
}

/* -------------------------------- 差异计算 -------------------------------- */

interface DiffLine {
  kind: 'add' | 'remove' | 'geom' | 'z' | 'vis' | 'page' | 'overrides' | 'other'
  text: string
}

const near = (a: number, b: number, eps = 0.05) => Math.abs(a - b) <= eps

/** 差异描述文案；对象名是用户内容，作为插值原样带过去 */
const df = (key: string, values?: Record<string, unknown>) =>
  translate(`versions.diff.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 图内修改的差异摘要。
 *
 * 「override 从 8 条变成 8 条」等于什么都没说（issue #131：用户就是靠时间线
 * 判断恢复有没有生效的）。这里按**类别**数：位置、文字、axes 布局、其他，
 * 外加受影响的元素个数与一份 gid/prop 样本。
 *
 * **只出技术标识，绝不出值**：override 的 value 里可能是用户写的图内文字，
 * 版本对比面板不该把它显示出来。
 */
const POS_PROPS = new Set(['pos_frac', 'loc_frac', 'endpoints_frac', 'position'])
const TEXT_PROPS = new Set([
  'text', 'fontsize', 'fontfamily', 'fontweight', 'fontstyle',
  'ha', 'va', 'rotation', 'linespacing',
])
const AXES_PROPS = new Set(['position', 'size_mm', 'aspect', 'figsize'])

function summarizeOverrideDiff(
  a: readonly { gid: string; prop: string; value: unknown }[],
  b: readonly { gid: string; prop: string; value: unknown }[],
) {
  const key = (o: { gid: string; prop: string }) => `${o.gid}\u0000${o.prop}`
  const mapA = new Map(a.map((o) => [key(o), JSON.stringify(o.value)]))
  const mapB = new Map(b.map((o) => [key(o), JSON.stringify(o.value)]))
  const touched: { gid: string; prop: string }[] = []
  for (const k of new Set([...mapA.keys(), ...mapB.keys()])) {
    if (mapA.get(k) === mapB.get(k)) continue
    const [gid, prop] = k.split('\u0000')
    touched.push({ gid, prop })
  }
  let pos = 0
  let text = 0
  let axes = 0
  let other = 0
  for (const { gid, prop } of touched) {
    // position 同时是 axes 布局与位置类：axes 元素上算 axes 布局
    if (prop === 'position' || AXES_PROPS.has(prop)) axes += 1
    else if (POS_PROPS.has(prop)) pos += 1
    else if (TEXT_PROPS.has(prop)) text += 1
    else other += 1
    void gid
  }
  return {
    elements: new Set(touched.map((o) => o.gid)).size,
    pos,
    text,
    axes,
    other,
    sample: touched.slice(0, 6).map((o) => `${o.gid}.${o.prop}`),
  }
}

/** 对象级差异（a = 版本快照，b = 当前文档），文案面向用户 */
export function diffDocs(a: FigureDocument, b: FigureDocument): DiffLine[] {
  const out: DiffLine[] = []
  const byIdA = new Map(a.objects.map((o) => [o.id, o]))
  const byIdB = new Map(b.objects.map((o) => [o.id, o]))

  if (!near(a.page.w, b.page.w) || !near(a.page.h, b.page.h)) {
    out.push({
      kind: 'page',
      text: df('pageSize', { aw: a.page.w, ah: a.page.h, bw: b.page.w, bh: b.page.h }),
    })
  }
  if ((a.page.bg ?? '#FFFFFF') !== (b.page.bg ?? '#FFFFFF') ||
      !!a.page.transparent !== !!b.page.transparent) {
    out.push({ kind: 'page', text: df('pageBackground') })
  }
  if ((a.page.margin ?? 0) !== (b.page.margin ?? 0)) {
    out.push({ kind: 'page', text: df('pageMargin', { a: a.page.margin ?? 0, b: b.page.margin ?? 0 }) })
  }

  for (const o of a.objects) {
    if (!byIdB.has(o.id)) out.push({ kind: 'remove', text: df('removed', { name: objectLabel(o) }) })
  }
  for (const o of b.objects) {
    if (!byIdA.has(o.id)) out.push({ kind: 'add', text: df('added', { name: objectLabel(o) }) })
  }

  const orderA = a.objects.filter((o) => byIdB.has(o.id)).map((o) => o.id)
  const orderB = b.objects.filter((o) => byIdA.has(o.id)).map((o) => o.id)
  if (orderA.join() !== orderB.join()) {
    out.push({ kind: 'z', text: df('zorder') })
  }

  for (const oa of a.objects) {
    const ob = byIdB.get(oa.id)
    if (!ob) continue
    const name = objectLabel(ob)
    const moved = !near(oa.x, ob.x) || !near(oa.y, ob.y)
    const resized = !near(oa.w, ob.w) || !near(oa.h, ob.h)
    if (moved && resized) out.push({ kind: 'geom', text: df('movedAndResized', { name }) })
    else if (moved) {
      out.push({
        kind: 'geom',
        text: df('moved', {
          name,
          dx: (ob.x - oa.x).toFixed(1),
          dy: (ob.y - oa.y).toFixed(1),
        }),
      })
    } else if (resized) out.push({ kind: 'geom', text: df('resized', { name }) })
    if (!!oa.hidden !== !!ob.hidden) {
      out.push({ kind: 'vis', text: df(ob.hidden ? 'hidden' : 'shown', { name }) })
    }
    if (!!oa.locked !== !!ob.locked) {
      out.push({ kind: 'vis', text: df(ob.locked ? 'locked' : 'unlocked', { name }) })
    }
    if (oa.type === 'panel' && ob.type === 'panel') {
      const ca = JSON.stringify(oa.overrides)
      const cb = JSON.stringify(ob.overrides)
      if (ca !== cb) {
        const sum = summarizeOverrideDiff(oa.overrides, ob.overrides)
        out.push({
          kind: 'overrides',
          text: df('overridesDetail', {
            name,
            elements: sum.elements,
            pos: sum.pos,
            text: sum.text,
            axes: sum.axes,
            other: sum.other,
          }),
        })
        // 具体改了哪几个 gid 的哪几条属性——**只列技术标识，不列值**，
        // 用户图内文字的正文一个字都不进这里
        if (sum.sample.length) {
          out.push({ kind: 'overrides', text: df('overridesSample', { list: sum.sample.join('、') }) })
        }
      }
      if (oa.fileId !== ob.fileId) out.push({ kind: 'other', text: df('assetReplaced', { name }) })
      if (JSON.stringify(oa.crop ?? null) !== JSON.stringify(ob.crop ?? null)) {
        out.push({ kind: 'other', text: df('cropChanged', { name }) })
      }
      if ((oa.rotation ?? 0) !== (ob.rotation ?? 0)) {
        out.push({
          kind: 'other',
          text: df('rotationChanged', { name, from: oa.rotation ?? 0, to: ob.rotation ?? 0 }),
        })
      }
    }
    if (oa.type === 'text' && ob.type === 'text') {
      if (oa.text !== ob.text) out.push({ kind: 'other', text: df('textChanged', { name }) })
      else if (
        oa.sizePt !== ob.sizePt || oa.bold !== ob.bold || oa.color !== ob.color ||
        oa.align !== ob.align || (oa.italic ?? false) !== (ob.italic ?? false)
      ) {
        out.push({ kind: 'other', text: df('textStyleChanged', { name }) })
      }
    }
  }

  if (a.guides.length !== b.guides.length) {
    out.push({ kind: 'other', text: df('guides', { from: a.guides.length, to: b.guides.length }) })
  }
  return out
}
