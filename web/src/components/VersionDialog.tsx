import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bookmark, Copy, Eye, Minus, Pencil, Plus, RotateCcw, RotateCcwClock, Trash2, X } from '@/components/ui/icons'
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
  VERSION_NAME_MAX,
  versionThumbUrl,
  type LayoutVersionMeta,
  type TimelineBudget,
} from '@/lib/api'
import { modKey, cn, ALT, MOD } from '@/lib/utils'
import { currentProjectId } from '@/lib/session'
import {
  afterAwait,
  askTimelineConfirm,
  currentTimelineCtx,
  timelineCtxKey,
} from '@/lib/timelineContext'
import { resolveRestoreTarget } from '@/lib/versionTarget'
import {
  comparableEarlier,
  versionDisplayName,
  versionSummary,
  versionSummaryText,
} from '@/lib/versionSummary'
import { groupTimeline, versionKind, type TimelineGroup } from '@/lib/timelineGroups'
import { saveNamedNode, takeCheckpoint } from '@/lib/timelineCheckpoint'
import { formatMessage, msg, t as translate } from '@/i18n'
import { formatDate, formatTime } from '@/i18n/format'
import { restoreLayoutVersion } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { useTimelineStore } from '@/store/timelineStore'
import { useInFlight } from '@/hooks/useInFlight'
import { useVariantPng } from '@/hooks/useVariantPng'
import {
  documentDigest,
  recordDiagnosticEvent,
  versionHash,
} from '@/diagnostics'
import { useUiStore } from '@/store/uiStore'
import type { FigureDocument, PanelObject } from '@/types/document'
import { canvasToDoc, objectLabel } from '@/types/document'
import {
  CanvasThumb,
  THUMB_OBJECT_LIMIT,
  THUMB_TEXT_CHARS,
} from './CanvasThumb'
import { DrawerCount } from './left/DrawerCount'
import { Badge } from './ui/Badge'
import { Card } from './ui/Card'
import { Notice } from './ui/Notice'
import { RowMenu } from './ui/RowMenu'
import { useRowMenu } from './ui/useRowMenu'
import { Button, IconButton } from './ui/Button'
import { EmptyState } from './ui/EmptyState'
import { TextInput } from './ui/Input'
import { MenuItem, MenuSeparator } from './ui/Menu'
import { Dialog } from './ui/Dialog'
import { focusOrigin } from './ui/focusOrigin'
import { Segmented } from './ui/Segmented'
import { Tip } from './ui/Tooltip'

/**
 * 排版时间线（ADR 0101）—— 右侧抽屉形态，画布保持可见；点一个节点打开**模态**
 * 预览对话框（`TimelinePreviewDialog`）看那一刻，「恢复到这里」才真的写。
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
  const gen = useTimelineStore((s) => s.gen)
  const preview = useTimelineStore((s) => s.preview)
  // 命名输入是否展开：抽屉里「给现在存个名字…」点开才展开，抽屉关闭复位
  // （⌥⌘S / 命令面板走顶部小框 `NamedNodeQuickBox`，与这里无关）
  const [naming, setNaming] = useState(false)

  /**
   * 本地列表、预算与错误都**记着自己属于哪个上下文**（项目代际 + 排版 id，Codex #679）：
   * 抽屉开着直接换项目 / 换排版时，旧上下文的列表当场不再显示（显示「正在读取」），
   * 而不是等新列表回来才换——新请求慢或失败的话，B 下面会一直挂着 A 的时间线，行操作
   * 还会带着 B 的排版 id 去动 A 的节点。同一上下文里重取失败才保留旧列表。
   * 按上下文记账而不是在 effect 里清空：清空晚一帧，那一帧里旧列表照样能被点到。
   * await 之后改本地状态一律经 `afterAwait`（清单在 `lib/timelineContext.ts`）。
   */
  const ctx = timelineCtxKey(gen, docId)
  const [list, setList] = useState<{
    ctx: string
    versions: LayoutVersionMeta[]
    budget: TimelineBudget | null
  } | null>(null)
  /**
   * 错误分两槽（Codex #679）：**读列表**的错误（`reload`）与**做操作**的错误（行操作、
   * 存为命名节点、取预览正文）。只有一槽的时候，操作失败之后照常刷新列表，刷新成功的
   * `setError(null)` 当场把「命名节点已满」之类的话清掉，用户什么都看不见。现在刷新只管
   * 读列表那一槽；操作那一槽只由下一次操作的成败改写。
   *
   * 两槽都记在**发出它的那一次渲染**的上下文名下，而且**只有那个上下文还是此刻的才写**：
   * A 的旧完成写进来会把 B 的真实错误换成 A 的、或清成 null（同为 Codex #679）。
   */
  const [loadFailure, setLoadFailure] = useState<{ ctx: string; text: string } | null>(null)
  const [actionFailure, setActionFailure] = useState<{ ctx: string; text: string } | null>(null)
  // 第三槽：**取预览正文**的错误（Codex #679）。记进操作槽的话，A 取失败、B 预览成功之后
  // 关掉 B，抽屉里还挂着 A 的旧错；它只由下一次预览请求改写（发起即清）
  const [previewFailure, setPreviewFailure] = useState<{ ctx: string; text: string } | null>(null)
  const loaded = list?.ctx === ctx
  const versions = useMemo(() => (loaded ? list.versions : []), [loaded, list])
  const budget = loaded ? list.budget : null
  const loadError = loadFailure?.ctx === ctx ? loadFailure.text : null
  const actionError = actionFailure?.ctx === ctx ? actionFailure.text : null
  const previewError = previewFailure?.ctx === ctx ? previewFailure.text : null
  const setPreviewError = useCallback(
    (text: string | null) => {
      afterAwait(ctx)(() => setPreviewFailure(text == null ? null : { ctx, text }))
    },
    [ctx],
  )
  const setLoadError = useCallback(
    (text: string | null) => {
      afterAwait(ctx)(() => setLoadFailure(text == null ? null : { ctx, text }))
    },
    [ctx],
  )
  const setError = useCallback(
    (text: string | null) => {
      afterAwait(ctx)(() => setActionFailure(text == null ? null : { ctx, text }))
    },
    [ctx],
  )
  // 「全部 / 命名」两个视图（用户 2026-09-27 反馈：命名节点要有自己的视图）
  const [filter, setFilter] = useState<'all' | 'named'>('all')
  // 用户正在编辑的输入按上下文记账（Codex #679）：换了项目 / 排版，旧排版的草稿与
  // 改名框当场不再显示——提交发生在切换之后，`afterAwait` 管不到，而交上去的名字会在
  // 新排版里建出一个名不副实的命名节点。在 render 里按 ctx 派生，不等晚一帧的 effect
  const [renamingState, setRenamingState] = useState<{ ctx: string; id: string } | null>(null)
  const renaming = renamingState?.ctx === ctx ? renamingState.id : null
  const setRenaming = useCallback(
    (id: string | null) => setRenamingState(id == null ? null : { ctx, id }),
    [ctx],
  )
  const [draft, setDraft] = useState<{ ctx: string; text: string } | null>(null)
  const saveName = draft?.ctx === ctx ? draft.text : ''
  const setSaveName = useCallback((text: string) => setDraft({ ctx, text }), [ctx])
  // busy 同样记着是哪个上下文忙：A 的保存还在飞时换到 B，B 不该被「忙」锁住
  const [busyCtx, setBusyCtx] = useState<string | null>(null)
  // 恢复在飞（两个入口共用的那把锁，见 `restoreUnderLock`）时抽屉同样关不掉
  const restoringHere = useTimelineStore((s) => s.restoring) === ctx
  const busy = busyCtx === ctx
  const locked = busy || restoringHere
  const submitOnce = useInFlight()
  const asideRef = useRef<HTMLElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(null)
  const previewing = preview?.docId === docId ? preview.meta.id : null
  // 单击行 = 选中（露出这一行的「预览」「改名」钮），**不开模态**：真实的双击先发一次 click，
  // 单击就开模态的话遮罩盖住这一行，第二下落不到行上，「双击改名」永远用不了（Codex #679）。
  // 选中按上下文记账（换了排版 / 项目就不认）
  const [picked, setPicked] = useState<{ ctx: string; id: string } | null>(null)
  const selected = previewing ?? (picked?.ctx === ctx ? picked.id : null)

  /**
   * 列表的请求序号（Codex #679 P2）：拍一个节点会 bump 两次（节点建好、缩略图挂上），
   * 关键时刻连着来还会更多，几次重取常常交叠。**只认最新那一次**，且发请求那一刻的
   * 排版与项目仍是此刻的——晚到的旧响应不许把新节点或它的缩略图盖回去，A 项目的
   * 响应也不许落进 B（web/AGENTS.md「会在项目之间存活的 store 都有项目代际」）。
   */
  const reloadSeq = useRef(0)
  const reload = useCallback(async () => {
    // 旧上下文的闭包（行操作做完才调的 onChanged）不发请求、也不占序号——占了的话
    // 新上下文那一次在飞的重取会被它判成过期，新列表就永远不来
    if (currentTimelineCtx() !== ctx) return
    const seq = ++reloadSeq.current
    const pj = currentProjectId()
    const after = afterAwait(ctx)
    // 序号挡交叠的重取；上下文挡「换了项目 / 排版才回来」；pj 是项目代际的明文守卫
    const fresh = () => after(() => seq === reloadSeq.current && pj === currentProjectId()) === true
    try {
      // 草图随列表一次带回来：没有位图缩略图的旧节点靠它，而列表端点为了数
      // 对象数本来就已经把整份文件解析过一遍了。尺寸取值来自缩略图组件自己。
      const res = await fetchTimeline(docId, {
        objects: THUMB_OBJECT_LIMIT,
        textChars: THUMB_TEXT_CHARS,
      })
      if (!fresh()) return
      // 最新在上
      setList({ ctx, versions: res.versions.slice().reverse(), budget: res.budget ?? null })
      setLoadError(null) // 只清读列表那一槽：操作的错误不因为刷新成功而消失
    } catch (e) {
      // 旧请求的失败同样不落地：它说的是一份已经过期的列表
      if (fresh()) setLoadError(backendErrorText(e))
    }
  }, [ctx, docId, setLoadError])

  useEffect(() => {
    if (!open) return
    setSaveName('')
    setRenaming(null)
    // 打开时记住触发点，关闭后把焦点还回去（从命令面板打开时是面板的打开者，见 `focusOrigin`）
    restoreFocus.current = focusOrigin()
    // 焦点交进抽屉（2026-10-07 设计审计 §10.2：此前打开后焦点还留在顶栏，Tab 要走一圈才进来）。
    // 落在抽屉本身：读屏念出「排版时间线」，下一下 Tab 进第一个控件；Esc 在抽屉上才收得到
    const id = requestAnimationFrame(() => asideRef.current?.focus({ preventScroll: true }))
    return () => {
      cancelAnimationFrame(id)
      // 抽屉关了，命名输入跟着收起：下次打开是「给现在存个名字…」按钮，不是一直开着的输入框
      setNaming(false)
      // 抽屉关了，预览跟着退出：没有抽屉的只读大图没有出口
      useTimelineStore.getState().setPreview(null)
      restoreFocus.current?.focus?.()
    }
  }, [open])

  // 命名输入展开就把焦点交给它；落在别处的话，关闭钮的气泡
  // 会一直挂在抽屉头上
  useEffect(() => {
    if (!open || !naming) return
    const id = requestAnimationFrame(() => nameRef.current?.focus())
    return () => cancelAnimationFrame(id)
  }, [open, naming])

  // 打开、换排版 / 换项目、节点有变（新拍 / 改名 / 删除 / 挂上缩略图）时重取
  useEffect(() => {
    if (open) void reload()
  }, [open, reload, rev])

  // 换了上下文：别的排版的预览一并作废（列表、草稿、改名框按上下文记账，见上）
  useEffect(() => {
    const tl = useTimelineStore.getState()
    if (tl.preview && tl.preview.docId !== docId) tl.setPreview(null)
  }, [ctx, docId])

  const openPreview = useCallback(
    (meta: LayoutVersionMeta) => {
      const tl = useTimelineStore.getState()
      setPicked({ ctx, id: meta.id })
      setPreviewError(null) // 新的一次预览：上一次取正文的失败作废
      tl.setPreview({ docId, meta, doc: null })
      // 期间换了节点 / 退出了预览：这份正文作废，它的失败也一样。换上下文不必另判：
      // 换项目 `clear()`、换排版那个 effect 都会清掉预览，`current()` 已经是假
      const current = () => {
        const cur = useTimelineStore.getState().preview
        return cur?.docId === docId && cur.meta.id === meta.id
      }
      fetchVersionDoc(docId, meta.id)
        .then((v) => {
          if (current()) useTimelineStore.getState().setPreview({ docId, meta, doc: v.doc })
        })
        .catch((e) => {
          if (current()) setPreviewError(backendErrorText(e))
        })
    },
    [ctx, docId, setPreviewError],
  )

  const saveNamed = async () => {
    const name = saveName.trim()
    if (!name) {
      nameRef.current?.focus()
      return
    }
    const at = ctx
    // 在途时回车 / 再点一次都不再发（`useInFlight`：同步标记，与顶栏浮层同一份）
    await submitOnce(at, async () => {
      const after = afterAwait(at)
      setBusyCtx(at)
      try {
        await saveNamedNode(name)
        // 换走之后才回来：名字框里已经是 B 的名字了，不清
        after(() => {
          setSaveName('')
          setNaming(false)
        })
        setError(null)
      } catch (e) {
        // 错误按上下文记账（`setError` 记的是这次渲染的上下文），换走之后不显示
        setError(backendErrorText(e))
      } finally {
        // 只摘掉**自己**挂上的忙标记（不碰 B 的任何状态）；不摘的话 A → B → A 回来时
        // A 会一直显示在忙
        setBusyCtx((c) => (c === at ? null : c))
      }
    })
  }

  /**
   * 选中行上的「恢复到这里」：取这一版的正文再走同一个 `restoreUnderLock`（与预览对话框
   * 同一把锁、同一个 `restoreNode`）。锁在**取正文之前**就挂上（Codex #831 P1）：否则
   * A 的正文还在路上时再点 B，两次都会走到恢复、都会写。正文取不回来就按预览失败那一槽说。
   * 没写进去（取消、「恢复前」存不下来、取正文期间文档变了、正文取不回来）就把为这次恢复
   * 打开的预览收回去，回到点之前的样子；被锁拒掉（已有一次在飞）什么都不动。
   */
  const restoreFromRow = async (meta: LayoutVersionMeta) => {
    setPreviewError(null)
    const closeOurs = () => {
      const cur = useTimelineStore.getState().preview
      if (cur?.docId === docId && cur.meta.id === meta.id) useTimelineStore.getState().setPreview(null)
    }
    try {
      const done = await restoreUnderLock(ctx, docId, meta, async () => {
        const v = await fetchVersionDoc(docId, meta.id)
        return v.doc as FigureDocument
      })
      if (done === null) return
      if (!done) closeOurs()
    } catch (e) {
      // 正文取不回来：取正文期间开着的那张「加载中」模态预览也收回去（锁由 `restoreUnderLock` 摘）
      closeOurs()
      setPreviewError(backendErrorText(e))
    }
  }

  const groups = useMemo(
    () => groupTimeline(versions, { namedOnly: filter === 'named', now: Date.now() }),
    [versions, filter],
  )

  if (!open) return null

  return (
    <aside
      ref={asideRef}
      role="dialog"
      aria-label={vd('drawerLabel')}
      tabIndex={-1}
      data-timeline-drawer
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !locked) {
          e.stopPropagation()
          // 逐层退出：先退预览，再关抽屉
          if (useTimelineStore.getState().preview) useTimelineStore.getState().setPreview(null)
          else setOpen(false)
        }
      }}
      // 覆盖在画布上的浮板只留投影：`shadow-pop` 自带 1px 环，再画一条实色 border
      // 就是双描边（宪法第一节；左栏审计 L20，左抽屉 overlay 态同改）
      className="absolute inset-y-0 right-0 z-overlay flex w-[400px] max-w-[92vw] flex-col bg-surface shadow-pop outline-none"
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
          disabled={locked}
          data-timeline-close
          onClick={() => setOpen(false)}
        >
          <X size={ICON_SIZE.md} className="text-ink-3" />
        </IconButton>
      </div>
      <div className="flex shrink-0 gap-1.5 px-3 pb-2">
        {naming ? (
          <>
            <TextInput
              ref={nameRef}
              value={saveName}
              data-timeline-name-input
              maxLength={VERSION_NAME_MAX}
              aria-label={vd('versionName')}
              onChange={(e) => setSaveName(e.target.value)}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') void saveNamed()
                // Esc 收起输入（不关抽屉）；在途时不收，名字不丢
                if (e.key === 'Escape' && !busy) {
                  setSaveName('')
                  setNaming(false)
                }
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
              // 不把 promise 交给按钮：按钮自己的忙态会跟着 A 的请求挂到 B 上
              onClick={() => void saveNamed()}
            >
              <Bookmark size={ICON_SIZE.sm} />
              {vd('save')}
            </Button>
          </>
        ) : (
          <Tip label={vd('nameNow')} shortcut={`${ALT}${MOD}S`}>
            <Button
              variant="secondary"
              size="sm"
              className="w-full justify-start"
              data-timeline-name-open
              onClick={() => setNaming(true)}
            >
              <Bookmark size={ICON_SIZE.sm} />
              {vd('nameNow')}
            </Button>
          </Tip>
        )}
      </div>
      {naming && <p className="type-caption shrink-0 px-3 pb-2">{vd('nameHint')}</p>}
      <div className="flex shrink-0 px-3 pb-1.5">
        <Segmented
          value={filter}
          onChange={setFilter}
          ariaLabel={vd('filterLabel')}
          data-timeline-filter
          className="w-full"
          items={[
            { value: 'all', label: vd('filterAll') },
            { value: 'named', label: vd('filterNamed') },
          ]}
        />
      </div>
      {budget?.namedOver && (
        // 命名节点超出字节上限：**不删**，照实说，请用户自己删（ADR 0101）
        <Notice tone="warn" role="alert" data-timeline-budget className="mx-3 mb-2">
          {vd('budgetOver', {
            used: ((budget.namedBytes ?? 0) / 1048576).toFixed(1),
            limit: Math.round(budget.limit / 1048576),
          })}
        </Notice>
      )}
      {/* 错误紧贴筛选器下面（2026-10-07 设计审计 §10.2）：此前在列表最末尾，列表一长就在视口外 */}
      {(actionError || previewError || loadError) && (
        <Notice tone="danger" data-timeline-error className="mx-3 mb-2">
          {actionError && <p data-timeline-error-kind="action">{actionError}</p>}
          {previewError && <p data-timeline-error-kind="preview">{previewError}</p>}
          {loadError && <p data-timeline-error-kind="load">{loadError}</p>}
        </Notice>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {!loaded ? (
          // 这个上下文的列表还没到（或第一次就失败了：错误在下面）——不是「没有节点」
          !loadError && (
            <p data-timeline-loading className="text-shimmer p-6 text-center text-ink-3">
              {vd('loadingList')}
            </p>
          )
        ) : versions.length === 0 ? (
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
                    onSelect={() => setPicked({ ctx, id: v.id })}
                    onPreview={() => openPreview(v)}
                    onRestore={() => restoreFromRow(v)}
                    onRename={(on) => setRenaming(on ? v.id : null)}
                    onChanged={async () => {
                      await reload()
                    }}
                    onError={setError}
                    ctx={ctx}
                  />
                ))}
              </TimelineDay>
            ))}
          </div>
        )}
      </div>
      <TimelinePreviewDialog />
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
      <h3 className="type-section sticky top-0 z-sticky bg-surface px-3 pb-1 pt-3">{label}</h3>
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
  onPreview,
  onRestore,
  onRename,
  onChanged,
  onError,
  ctx,
  children,
}: {
  docId: string
  meta: LayoutVersionMeta
  all: LayoutVersionMeta[]
  selected: boolean
  renaming: boolean
  showCanvas: boolean
  /** 单击行：选中（不开模态） */
  onSelect: () => void
  /** 选中行内联条上的「预览」：打开模态预览 */
  onPreview: () => void
  /** 选中行内联条上的「恢复到这里」（同一个 `restoreNode`） */
  onRestore: () => Promise<void>
  onRename: (on: boolean) => void
  onChanged: () => Promise<void>
  onError: (e: string | null) => void
  /** 这一行属于哪个上下文（项目代际 + 排版 id）：不是此刻的就一个请求都不发、一个状态都不改 */
  ctx: string
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
    const after = afterAwait(ctx)
    // 失焦也可能发生在换了上下文之后（改名框随旧列表卸载）：那就不发
    if (!name || (named && name === v.name) || !after(() => true)) return
    try {
      await updateVersion(docId, v.id, { name })
      onError(null)
    } catch (e) {
      onError(backendErrorText(e))
    }
    await onChanged() // 旧上下文的 `reload` 自己不发请求
  }

  const act = (fn: () => Promise<unknown>) => async () => {
    try {
      await fn()
      onError(null)
    } catch (e) {
      onError(backendErrorText(e))
    }
    await onChanged() // 旧上下文的 `reload` 自己不发请求
  }

  const remove = async () => {
    const after = afterAwait(ctx)
    const ok = await askTimelineConfirm({
      title: msg('versions.deleteTitle', { name: versionDisplayName(v) || formatTime(v.ts) }, 'dialogs'),
      body: msg('versions.deleteBody', undefined, 'dialogs'),
      confirmLabel: msg('actions.delete', undefined, 'common'),
      danger: true,
    })
    // 确认框开着的时候也可能换了项目 / 排版：那就什么都不做
    if (!after(() => ok)) return
    if (selected) useTimelineStore.getState().setPreview(null)
    await act(() => deleteVersion(docId, v.id))()
  }

  const index = all.indexOf(v)
  // 一行的动作只有一颗 ⋯（2026-10-07 设计审计 §10.2：此前「预览」「改名」两颗图标 + ⋯，三个入口说的是同一张清单）。
  // ⋯ / 右键 / ⇧F10 是同一份菜单（`RowMenu`）；选中的那一行下面再露一条「预览 · 恢复到这里」
  const menu = useRowMenu()
  const [restoring, setRestoring] = useState(false)
  // 这个上下文有一次恢复在飞（哪一行发起的都算）：所有行的「恢复到这里」一起禁用
  const restoreLocked = useTimelineStore((s) => s.restoring) === ctx
  const renamingFromMenu = useRef(false)
  return (
    <li className="group/node" data-timeline-node={v.id} data-timeline-named={named || undefined}>
      <div
        {...menu.rowProps}
        className={cn(
          'group flex items-start gap-1 rounded-md pr-1.5',
          selected ? 'bg-selected' : 'hover:bg-surface-hover',
        )}
      >
        <button
          type="button"
          onClick={onSelect}
          // 双击改名：单击只选中、不开模态，所以双击的第二下仍落在这一行上
          onDoubleClick={(e) => {
            e.preventDefault()
            onRename(true)
          }}
          aria-pressed={selected}
          data-timeline-row
          className="flex min-w-0 flex-1 items-start gap-2 rounded-md py-1.5 pl-3 text-left outline-none focus-visible:focus-ring"
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
        <RowMenu
          state={menu}
          label={vd('more')}
          className="mt-1.5"
          data-timeline-more
          // 从菜单进改名：关菜单时不把焦点还给 ⋯（那一下就是改名框的 blur——框一出现就按「没改」提交收起了）
          onCloseAutoFocus={(e) => {
            if (!renamingFromMenu.current) return
            renamingFromMenu.current = false
            e.preventDefault()
          }}
        >
          <MenuItem icon={Eye} data-timeline-menu-preview onSelect={onPreview}>
            {vd('preview')}
          </MenuItem>
          <MenuItem
            icon={Pencil}
            data-timeline-rename-button
            onSelect={() => {
              // 两件事都要：等菜单卸掉再展开（菜单开着时它的焦点圈会把改名框的焦点拽回去），
              // 并且关菜单时不把焦点还给 ⋯（见上面的 onCloseAutoFocus）
              renamingFromMenu.current = true
              window.setTimeout(() => onRename(true), 0)
            }}
          >
            {vd(named ? 'rename' : 'name')}
          </MenuItem>
          {named && (
            <MenuItem icon={Bookmark} onSelect={act(() => updateVersion(docId, v.id, { named: false }))}>
              {vd('unname')}
            </MenuItem>
          )}
          <MenuItem icon={Copy} onSelect={act(() => duplicateVersion(docId, v.id))}>
            {vd('duplicate')}
          </MenuItem>
          <MenuSeparator />
          <MenuItem icon={Trash2} danger data-timeline-delete onSelect={() => void remove()}>
            {vd('delete')}
          </MenuItem>
        </RowMenu>
      </div>
      {/* 选中行下面的内联条：「预览」「恢复到这里」。没选中时收起，悬停 / 键盘焦点在这一行时也露出来
          （鼠标用户不必先点一下才看得见这两件最常做的事） */}
      <div
        data-timeline-row-actions
        className={cn(
          'items-center gap-1 pb-1.5 pl-[76px] pr-3',
          selected ? 'flex' : 'hidden group-hover/node:flex group-focus-within/node:flex',
        )}
      >
        <Button variant="secondary" size="sm" data-timeline-preview-button onClick={onPreview}>
          <Eye size={ICON_SIZE.sm} />
          {vd('preview')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          data-timeline-row-restore
          loading={restoring}
          disabled={restoreLocked}
          onClick={async () => {
            setRestoring(true)
            try {
              await onRestore()
            } finally {
              setRestoring(false)
            }
          }}
        >
          <RotateCcw size={ICON_SIZE.sm} />
          {vd('restore')}
        </Button>
      </div>
      {renaming && (
        <div className="px-3 pb-1.5">
          <input
            autoFocus
            value={draft}
            aria-label={vd('versionName')}
            placeholder={vd('namePlaceholder')}
            data-timeline-rename
            maxLength={VERSION_NAME_MAX}
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
  // 当前画布的名字取活文档的（`canvases` 里那一条要等下次切画布才同步改名）
  const here = s.doc.name
  const target = resolveRestoreTarget(meta, s)
  if (target.kind === 'same') return true
  if (target.kind === 'other') {
    return askTimelineConfirm({
      title: msg('versions.restoreOtherCanvasTitle', { name: target.name }, 'dialogs'),
      body: msg(
        'versions.restoreOtherCanvasBody',
        { from: target.name, to: here },
        'dialogs',
      ),
      confirmLabel: msg('actions.continue', undefined, 'common'),
    })
  }
  return askTimelineConfirm({
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
  // 确认框与「恢复前」节点之间都可能换了项目 / 排版：那时再写，A 的节点会被写进 B
  const after = afterAwait()
  const confirmed = await confirmRestoreTarget(meta)
  if (!after(() => confirmed)) return false
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
    after(() =>
      useUiStore
        .getState()
        .setStatus(msg('versions.restoreFailed', { error: backendErrorText(e) }, 'dialogs'), 'error'),
    )
    return false
  }
  // 「恢复前」节点已经存下了（服务端事实，留着）；上下文换了就不写
  if (!after(() => true)) return false
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
    .setStatus(msg('versions.restored', { name: label, undo: modKey('Z') }, 'dialogs'), 'done')
  return true
}

/**
 * 两个恢复入口（预览对话框页脚、抽屉选中行内联条）**唯一**的调用口（Codex #831 P1）：
 * 在任何 await（取正文、`restoreNode`）之前挂上 `timelineStore.restoring`，并让模态预览对话框
 * 对着这一版开着——它以 `busy` 开着就是这把锁：遮罩挡住画布（没法趁「恢复前」节点还在存
 * 时再编辑，晚到的恢复也就不会盖掉新编辑）、×/Esc/点外面关不掉，抽屉也关不掉。
 * 内联入口原来只把行上那颗按钮转圈，画布照常可改。`restoreNode` 照旧先收掉在途手势。
 *
 * 这个上下文已经有一次恢复在飞（含还在取正文的那段）→ 拒绝，返回 null、什么都不动；锁按
 * `beginRestore` 发的凭据摘，先结束的一次摘不掉后来者的锁。`doc` 可以是取正文的函数：
 * 取回来时换了项目 / 排版就不恢复（同样返回 null）。返回 true = 写进去了。
 *
 * 取正文那段（Codex #831 P1）：模态预览在 **await 之前**就以「加载中」（`doc: null`）对着
 * 这一版开起来——锁挂上的同时遮罩就挡住画布，不是等正文回来才挡（只锁抽屉时画布照常可改，
 * 那段时间的编辑不换上下文，晚到的 `restoreNode` 会把它整份盖掉）。再兜一层：开工时记下
 * 编辑历史的标记（`editMark`），正文回来时变了（有编辑 / 撤销 / 重做 / 手势收尾 / 换画布
 * 落了地）就放弃这次恢复：不写、不打「恢复前」节点，返回 false。
 */
/**
 * 文档的「编辑修订」：用户能落进文档的每一笔（commit / 撤销 / 重做 / 事务累积 / 换画布）都
 * 会换掉其中某个引用；派生同步（`applyDerivedUpdate`）不进 `past` / `future`，不算编辑。
 */
function editMark() {
  const s = useDocumentStore.getState()
  return [s.past, s.future, s.txn, s.activeCanvasId] as const
}

function sameEditMark(a: ReturnType<typeof editMark>, b: ReturnType<typeof editMark>): boolean {
  return a.every((x, i) => x === b[i])
}

async function restoreUnderLock(
  ctx: string,
  docId: string,
  meta: LayoutVersionMeta,
  doc: FigureDocument | (() => Promise<FigureDocument>),
): Promise<boolean | null> {
  const token = useTimelineStore.getState().beginRestore(ctx)
  if (token === null) return null
  try {
    let body: FigureDocument
    if (typeof doc === 'function') {
      const mark = editMark()
      const open = useTimelineStore.getState().preview
      if (!(open?.docId === docId && open.meta.id === meta.id)) {
        useTimelineStore.getState().setPreview({ docId, meta, doc: null })
      }
      body = await doc()
      // 换了项目 / 排版才取回来：这次恢复作废，旧上下文的什么都不动
      if (!afterAwait(ctx)(() => true)) return null
      // 取正文期间文档被改过：那笔编辑比这次恢复新，不盖掉它
      if (!sameEditMark(mark, editMark())) {
        useUiStore.getState().setStatus(msg('versions.restoreAbortedEdited', undefined, 'dialogs'), 'error')
        return false
      }
    } else body = doc
    const tl = useTimelineStore.getState()
    const cur = tl.preview
    if (!(cur?.docId === docId && cur.meta.id === meta.id && cur.doc)) {
      tl.setPreview({ docId, meta, doc: body })
    }
    return await restoreNode(meta, body)
  } finally {
    useTimelineStore.getState().endRestore(token)
  }
}

/* ------------------------------- 与当前的差异 ------------------------------- */

/**
 * 「这一刻与现在比变了什么」。「现在」指的是**这个节点所属画布**的当前内容，不是
 * 恰好激活的那张：拿激活画布去和另一张画布的节点做 diff，差异栏里全是无中生有的
 * 「新增 12 个对象」（R-03）。
 */
function useCurrentDocFor(meta: LayoutVersionMeta): FigureDocument {
  const activeDoc = useDocumentStore((s) => s.doc)
  const canvases = useDocumentStore((s) => s.canvases)
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  return useMemo(() => {
    if (!meta.canvasId || meta.canvasId === activeCanvasId) return activeDoc
    const c = canvases.find((x) => x.id === meta.canvasId)
    return c ? canvasToDoc(c) : activeDoc
  }, [meta.canvasId, activeCanvasId, activeDoc, canvases])
}

function NodeDiff({ versionDoc, currentDoc }: { versionDoc: FigureDocument; currentDoc: FigureDocument }) {
  const diff = useMemo(() => diffDocs(versionDoc, currentDoc), [versionDoc, currentDoc])
  if (diff.length === 0) return <p className="type-caption">{vd('noDiff')}</p>
  return (
    <ul data-timeline-diff>
      {diff.map((d, i) => (
        <li key={i} className="flex items-start gap-1.5 py-0.5 text-sm text-ink-2">
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
  )
}

/* ------------------------------- 预览对话框 --------------------------------- */

type PreviewView = 'moment' | 'side' | 'overlay'
const ZOOM_STEPS = [0.5, 0.75, 1, 1.5, 2, 3]

/**
 * 节点预览对话框（ADR 0101；用户 2026-09-27 反馈后从「盖在画布上」改成模态对话框）。
 *
 * 盖在画布上的只读层让人分不清「我是不是在改真东西」；模态对话框把这件事说死：
 * 画布在遮罩后面一动不动，对话框里的一切都是只读的，唯一会写的是「恢复到这里」。
 *
 * - 左边是那一刻的排版大图（面板按那一刻自己的 overrides 出图），可以缩放 / 适配；
 *   「与当前对比」两种：并排（那一刻 | 当前）与叠加（底图 = 那一刻，描边 = 当前）。
 * - 右边是差异列表。
 * - 默认焦点在「关闭」上：回车不会误触恢复。
 * - 恢复在飞时整个对话框锁住（`busy`：右上角关闭 / Esc / 点外面都不关）：「恢复前」
 *   节点还没存完就让人关掉回去编辑，晚到的恢复会把这些新编辑整份盖掉（Codex #679）。
 *   忙按上下文记账：A 的恢复还在飞时换到 B，B 的预览不该被锁住。
 */
function TimelinePreviewDialog() {
  useTranslation(['dialogs', 'common'])
  const preview = useTimelineStore((s) => s.preview)
  const docId = useDocumentStore((s) => s.documentId)
  const gen = useTimelineStore((s) => s.gen)
  const ctx = timelineCtxKey(gen, docId)
  const active = preview && preview.docId === docId ? preview : null
  // 锁在 timelineStore 里，抽屉内联条的恢复也挂这同一把（`restoreUnderLock`）
  const restoring = useTimelineStore((s) => s.restoring) === ctx
  const close = () => useTimelineStore.getState().setPreview(null)
  const restore = async (meta: LayoutVersionMeta, doc: FigureDocument) => {
    await restoreUnderLock(ctx, docId, meta, doc)
  }
  return (
    <Dialog
      busy={restoring}
      // 默认焦点落在对话框容器上（Dialog 的默认）：回车什么都不触发，不会误触恢复。
      // 页脚不再另摆一颗「关闭」——右上角 × 与 Esc 就是关闭（2026-10-07 设计审计 §10.2：两个关闭重复）
      open={!!active}
      onOpenChange={(v) => {
        if (!v) close()
      }}
      title={active ? previewTitle(active.meta) : ''}
      anchor="timeline-preview"
      chrome="shell"
      // 宽度走 CSS（窗口缩放时跟着变），不在打开那一刻按 innerWidth 算死一个像素数
      width="min(1200px, 80vw)"
      height="80vh"
    >
      {active && (
        <PreviewBody
          meta={active.meta}
          doc={active.doc}
          restoring={restoring}
          onRestore={restore}
        />
      )}
    </Dialog>
  )
}

function previewTitle(meta: LayoutVersionMeta): string {
  const name = versionDisplayName(meta)
  const time = `${formatDate(meta.ts)} ${formatTime(meta.ts)}`
  return vd('previewTitle', { time, name: name ?? '' }).trim()
}

function PreviewBody({
  meta,
  doc,
  restoring,
  onRestore,
}: {
  meta: LayoutVersionMeta
  doc: FigureDocument | null
  /** 恢复在飞：状态在外层对话框上（它要据此锁住关闭），这里只管按钮 */
  restoring: boolean
  onRestore: (meta: LayoutVersionMeta, doc: FigureDocument) => Promise<void>
}) {
  const currentDoc = useCurrentDocFor(meta)
  const [view, setView] = useState<PreviewView>('moment')
  const [zoom, setZoom] = useState<number | 'fit'>('fit')
  const [approximate, setApproximate] = useState(false)
  const stageRef = useRef<HTMLDivElement>(null)
  const [stage, setStage] = useState({ w: 0, h: 0 })

  useLayoutEffect(() => {
    const el = stageRef.current
    if (!el) return
    const measure = () => setStage({ w: el.clientWidth, h: el.clientHeight })
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const page = doc?.page ?? currentDoc.page
  const panes = view === 'side' ? 2 : 1
  const PAD = 32
  // 适配：整张页面放进舞台（并排时每一格各占一半）；数字档是相对适配的倍数
  const fitPx =
    stage.w > 0 && page.w > 0
      ? Math.max(0.1, Math.min((stage.w - PAD * (panes + 1)) / panes / page.w, (stage.h - PAD * 2 - 20) / page.h))
      : 1
  const factor = zoom === 'fit' ? 1 : zoom
  const width = Math.max(40, page.w * fitPx * factor)
  const stepZoom = (dir: 1 | -1) => {
    const cur = zoom === 'fit' ? 1 : zoom
    const next =
      dir > 0 ? ZOOM_STEPS.find((z) => z > cur + 1e-6) : [...ZOOM_STEPS].reverse().find((z) => z < cur - 1e-6)
    if (next != null) setZoom(next === 1 ? 'fit' : next)
  }

  const restore = () => {
    if (doc) void onRestore(meta, doc)
  }

  const frame = (d: FigureDocument, caption: string | null, opts?: { outlineOver?: FigureDocument }) => (
    <figure
      className="flex shrink-0 flex-col items-center gap-1.5"
      style={{ width }}
      data-timeline-frame={d === doc ? 'moment' : 'current'}
    >
      <Card appearance="raised" padding="none" className="w-full overflow-hidden rounded-xs">
        <LayoutSnapshot doc={d} renderOverrides onApproximate={d === doc ? setApproximate : undefined} />
        {opts?.outlineOver && (
          <div className="absolute inset-0 opacity-55" data-timeline-overlay-current>
            <LayoutSnapshot doc={opts.outlineOver} outline />
          </div>
        )}
      </Card>
      {caption && <figcaption className="type-caption">{caption}</figcaption>}
    </figure>
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-timeline-preview={meta.id}>
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-10 shrink-0 items-center gap-2 px-3">
            <Segmented
              value={view}
              onChange={setView}
              ariaLabel={vd('viewLabel')}
              data-timeline-view
              className="w-80"
              items={[
                { value: 'moment', label: vd('viewMoment') },
                { value: 'side', label: vd('viewSide') },
                { value: 'overlay', label: vd('viewOverlay') },
              ]}
            />
            <span className="flex-1" />
            <IconButton iconSize="sm" label={vd('zoomOut')} onClick={() => stepZoom(-1)} disabled={factor <= ZOOM_STEPS[0]}>
              <Minus size={ICON_SIZE.sm} className="text-ink-2" />
            </IconButton>
            <span className="w-11 text-center text-xs tabular-nums text-ink-2" data-timeline-zoom>
              {/* 相对「适配」的倍数：适配 = 100% */}
              {`${Math.round(factor * 100)}%`}
            </span>
            <IconButton
              iconSize="sm"
              label={vd('zoomIn')}
              onClick={() => stepZoom(1)}
              disabled={factor >= ZOOM_STEPS[ZOOM_STEPS.length - 1]}
            >
              <Plus size={ICON_SIZE.sm} className="text-ink-2" />
            </IconButton>
            <Button size="sm" disabled={zoom === 'fit'} onClick={() => setZoom('fit')}>
              {vd('zoomFit')}
            </Button>
          </div>
          <div ref={stageRef} className="min-h-0 flex-1 overflow-auto bg-canvas">
            {!doc ? (
              <p className="text-shimmer p-6 text-center text-ink-3">{vd('loadingSnapshot')}</p>
            ) : (
              <div className="flex min-h-full min-w-max items-center justify-center gap-8 p-8">
                {view === 'side' ? (
                  <>
                    {frame(doc, vd('sideMoment'))}
                    {frame(currentDoc, vd('sideCurrent'))}
                  </>
                ) : view === 'overlay' ? (
                  frame(doc, vd('overlayHint'), { outlineOver: currentDoc })
                ) : (
                  frame(doc, null)
                )}
              </div>
            )}
          </div>
          {approximate && (
            <p className="type-caption shrink-0 px-3 py-1.5">{vd('previewApproximate')}</p>
          )}
        </div>
        <aside className="w-64 shrink-0 overflow-y-auto border-l border-border px-3 py-2">
          <h3 className="type-section pb-1.5">{vd('diffTitle')}</h3>
          {doc ? <NodeDiff versionDoc={doc} currentDoc={currentDoc} /> : null}
        </aside>
      </div>
      <div data-dialog-footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-5 pb-4 pt-3">
        <Button
          variant="primary"
          size="lg"
          loading={restoring}
          disabled={!doc}
          onClick={restore}
          data-timeline-preview-restore
        >
          <RotateCcw size={ICON_SIZE.sm} />
          {vd('restore')}
        </Button>
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
    renderOverrides && (panel.overrides.length > 0 || Object.hasOwn(panel, 'artifactValidation')),
    mtime ?? 0,
    Object.hasOwn(panel, 'artifactValidation') ? panel.artifactValidation ?? null : undefined,
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
