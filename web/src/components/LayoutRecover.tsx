import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg } from '@/i18n'
import { FolderOpen } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { fetchLayoutOwnerEvidence, type RecoveryCandidateItem } from '@/lib/api'
import {
  hasMore,
  loadCandidates,
  recoverCopy,
  rowIssue,
  type CandidateList,
  type CandidateLoad,
  type RecoverCopyResult,
  type RowIssue,
} from '@/lib/recoveryCandidates'
import { currentProjectId } from '@/lib/session'
import { correctRecordedProject, isForeignDocument } from '@/lib/docOwnership'
import { cn } from '@/lib/utils'
import { openDocumentAs } from '@/store/actions'
import { acceptProbe, probeDocument, useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { captureSaveContext, stillCurrent } from '@/store/saveContext'
import { useUiStore } from '@/store/uiStore'
import { formatRelativeTime, listJoin } from '@/i18n/format'
import { CanvasThumb } from './CanvasThumb'
import { Button } from './ui/Button'
import { Details, Summary } from './ui/Details'
import { listRowClass, rowMetaClass } from './ui/listRow'
import { Notice } from './ui/Notice'

/**
 * 「找回本机排版」（ADR 0108 §四）——「打开排版」那一屏里两组：
 *
 * - **这个项目的本机排版**：后端有归属证据说它属于这个项目、却没人记着「上次开着」的自动保存槽位。
 *   点一行 = 按原 id 打开（走 `openRecentDocument`，与「最近文档」同一条路）。
 * - **本机上其他排版（归属待确认）**：折叠。不知道属于哪个项目——只能「恢复副本」：在这个项目里新建一份，
 *   原排版与它的归属一个字节都不动；取消零写入。
 *
 * 列表是后端只读给的：名字 / 保存时间 / 画布与对象数 / 脚本·figure 名提示，可用时一张结构化草图
 * （`CanvasThumb`）；**不为列表运行脚本、不装包、不 live render，没有草图就不画，不编缩略图**。
 * 读不了的行（坏 / 来自更新版本 / 太大）置灰并说原因。
 */

type Group =
  | { state: 'idle' }
  | { state: 'loading'; list?: CandidateList }
  | { state: 'ok'; list: CandidateList }
  | { state: 'unsupported' }
  | { state: 'unavailable' }

const asGroup = (r: CandidateLoad): Group => (r.kind === 'ok' ? { state: 'ok', list: r.list } : { state: r.kind })

const ISSUE_KEY: Record<RowIssue, string> = {
  invalid: 'dialogs:layout.recoverIssueInvalid',
  too_new: 'dialogs:layout.recoverIssueTooNew',
  too_large: 'dialogs:layout.recoverIssueTooLarge',
}

function savedAt(it: RecoveryCandidateItem): number | null {
  if (it.mtime > 0) return it.mtime
  return it.updatedAt
}

export function LayoutRecover({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation(['dialogs', 'common'])
  const currentId = useDocumentStore((s) => s.documentId)
  /** 项目换了（对话框开着时桌面交接等）：两组列表都是上一个项目的，丢掉重读 */
  const projectKey = useProjectStore((s) => s.project?.id ?? null)
  const [project, setProject] = useState<Group>({ state: 'idle' })
  const [other, setOther] = useState<Group>({ state: 'idle' })
  const [otherOpen, setOtherOpen] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  /** 代次：重开 / 换项目 / 刷新之后，旧请求的结果不许落进新的列表 */
  const seq = useRef(0)
  const inFlight = useRef(false)
  /** 每组列表是替哪个项目读的：点击时对不上此刻的项目就拒绝（界面还没来得及换也不放行） */
  const listPj = useRef<{ project: string | null; unknown: string | null }>({ project: null, unknown: null })

  const load = useCallback(
    async (scope: 'project' | 'unknown', prev?: CandidateList) => {
      const set = scope === 'project' ? setProject : setOther
      const mine = seq.current
      set(prev ? { state: 'loading', list: prev } : { state: 'loading' })
      const pj = currentProjectId()
      listPj.current[scope] = pj
      const res = await loadCandidates(scope, pj, prev, new Set([useDocumentStore.getState().documentId]))
      if (mine !== seq.current) return
      set(asGroup(res))
    },
    [],
  )

  useEffect(() => {
    if (!open) return
    seq.current += 1
    setProject({ state: 'idle' })
    setOther({ state: 'idle' })
    setOtherOpen(false)
    setSelected(null)
    setNotice(null)
    void load('project')
    return () => {
      seq.current += 1
    }
  }, [open, load, projectKey])

  /**
   * 按原 id 打开一份自动保存：**入口取一次上下文**（`saveContext.ts`），读完回来先核对项目 / 排版还是同一个，
   * 不是就不装、状态条说一句——否则读的途中切了项目，A 的排版会被装进 B 并按 B 的名义自动保存。
   */
  const openSafely = async (
    id: string,
    name: string,
    expectedRevision: string | null,
    scope: 'project' | 'unknown',
    verifiedTarget = false,
  ): Promise<boolean> => {
    const ctx = captureSaveContext()
    const probe = await probeDocument(id, ctx.pj)
    if (!stillCurrent(ctx)) {
      useUiStore.getState().setStatus(msg('layout.openSkipped', { name }, 'dialogs'), 'error')
      return false
    }
    if (!probe.doc) {
      useUiStore
        .getState()
        .setStatus(msg(probe.kind === 'too_new' ? 'status.recentTooNew' : 'status.recentMissing', undefined, 'workspace'), 'error')
      return false
    }
    // 扫描之后这个槽位的正文被改过（同一个 doc id，别的项目存了新的一版）：只有后端证明它现在仍属于本项目
    // （owners=this 且已核对）才装；否则不装、说一句、刷新列表
    const diskRevision = probe.disk.kind === 'found' ? probe.disk.revision : null
    // 扫描时有 revision 而此刻读不到（代理吃了响应头 / 槽位读不到）：没法核对，按「可能被改过」处理，不装
    if (expectedRevision !== null && diskRevision === null) {
      useUiStore.getState().setStatus(msg('layout.recoverUnverified', { name }, 'dialogs'), 'error')
      seq.current += 1
      void load(scope)
      return false
    }
    if (expectedRevision !== null && diskRevision !== null && diskRevision !== expectedRevision) {
      const ev = await fetchLayoutOwnerEvidence(id, ctx.pj)
      // 结论还要绑在刚探测到的这一版正文上：revision 对不上（下结论之后又被存了新的一版）就不算
      const mine = ev !== 'unsupported' && ev !== 'unavailable' && ev.owner === 'this' && ev.verified && ev.revision === diskRevision
      if (!stillCurrent(ctx)) {
        useUiStore.getState().setStatus(msg('layout.openSkipped', { name }, 'dialogs'), 'error')
        return false
      }
      if (!mine) {
        useUiStore.getState().setStatus(msg('layout.recoverChanged', { name }, 'dialogs'), 'error')
        seq.current += 1
        void load(scope)
        return false
      }
    }
    let notice: ReturnType<typeof acceptProbe> = null
    const beforeInstall = async () => {
      if (!stillCurrent(ctx)) return false
      if ((scope !== 'project' && !verifiedTarget) || !isForeignDocument(id, ctx.pj)) {
        notice = acceptProbe(probe, ctx.pj)
        return true
      }
      if (ctx.pj === null) return false
      // 本机索引还记着旧项目：列表行必须再有针对刚读到的正文的权威 owners 结论。
      const ev = await fetchLayoutOwnerEvidence(id, ctx.pj)
      if (!stillCurrent(ctx)) return false
      if (diskRevision && ev !== 'unsupported' && ev !== 'unavailable' &&
          ev.owner === 'this' && ev.evidence === 'owners' && ev.verified && ev.revision === diskRevision) {
        correctRecordedProject(id, ctx.pj)
        notice = acceptProbe(probe, ctx.pj)
        return true
      }
      useUiStore.getState().setStatus(msg('layout.recoverUnverified', { name }, 'dialogs'), 'error')
      seq.current += 1
      void load(scope)
      return false
    }
    if (!(await openDocumentAs(probe.doc, id, { stillCurrent: () => stillCurrent(ctx), beforeInstall }))) return false
    // 未决的恢复副本跟着这份文档走：switchDocument 刚把待裁决事项清空了
    if (notice) useDocumentStore.setState({ docNotice: notice })
    return useDocumentStore.getState().documentId === id
  }

  /** 这一行是替别的项目读的（项目换了而界面还没换）：不动，状态条说没打开，并按新项目重读 */
  const rowsStale = (scope: 'project' | 'unknown', name: string): boolean => {
    if (listPj.current[scope] === currentProjectId()) return false
    useUiStore.getState().setStatus(msg('layout.openSkipped', { name }, 'dialogs'), 'error')
    seq.current += 1
    void load(scope)
    return true
  }

  /** 点「这个项目的本机排版」里的一行：按原 id 打开 */
  const openOwn = async (it: RecoveryCandidateItem) => {
    if (inFlight.current) return
    if (rowsStale('project', it.name)) return
    inFlight.current = true
    setBusy(true)
    try {
      if (await openSafely(it.doc_id, it.name, it.revision, 'project')) onClose()
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  const resultMessage = (r: RecoverCopyResult, name: string): string | null => {
    if (r.kind === 'stale') return t('dialogs:layout.recoverStale')
    if (r.kind === 'opened') return null
    switch (r.reason) {
      case 'missing':
        return t('dialogs:layout.recoverFailMissing')
      case 'invalid':
        return t('dialogs:layout.recoverFailInvalid', { name })
      case 'too_new':
        return t('dialogs:layout.recoverIssueTooNew')
      case 'unavailable':
        return t('dialogs:layout.recoverFailUnavailable')
      case 'foreign':
        return t('dialogs:layout.recoverFailForeign')
      default:
        return null
    }
  }

  /** 「恢复副本」的确认：此刻才读源正文核对 revision；副本以固定目标 id 装进文档、走正常保存链 */
  const confirmCopy = async (it: RecoveryCandidateItem) => {
    if (inFlight.current) return
    if (rowsStale('unknown', it.name)) return
    inFlight.current = true
    setBusy(true)
    setNotice(null)
    const ctx = captureSaveContext()
    try {
      const r = await recoverCopy(it, {
        pj: ctx.pj,
        stillCurrent: () => stillCurrent(ctx),
        openExisting: (id, revision) => openSafely(id, it.name, revision, 'unknown', true),
        install: (pd, id) => openDocumentAs(pd, id, { stillCurrent: () => stillCurrent(ctx) }),
      })
      if (r.kind === 'opened') {
        useUiStore
          .getState()
          .setStatus(
            msg(r.existed ? 'layout.recoverReopened' : 'layout.recoverDone', { name: it.name }, 'dialogs'),
            'done',
          )
        onClose()
        return
      }
      setNotice(resultMessage(r, it.name))
      if (r.kind === 'stale') {
        // 预览之后源变了：刷新这一组，让用户重新看一眼再确认
        seq.current += 1
        setSelected(null)
        void load('unknown')
      }
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  const okRows = (g: Group) => (g.state === 'ok' ? g.list.rows : g.state === 'loading' ? (g.list?.rows ?? []) : [])
  const ownRows = okRows(project)
  const otherRows = okRows(other)

  const moreButton = (scope: 'project' | 'unknown', g: Group) => {
    if (g.state !== 'ok') return null
    if (hasMore(g.list)) {
      return (
        <div className="flex flex-col items-start gap-1">
          {g.list.capped && (
            <p className="text-sm text-ink-3">{t('dialogs:layout.recoverCapped', { n: g.list.rows.length })}</p>
          )}
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => void load(scope, g.list)}>
            {t('dialogs:layout.recoverMore')}
          </Button>
        </div>
      )
    }
    return null
  }

  const retry = (scope: 'project' | 'unknown') => (
    <Notice
      tone="warn"
      action={
        <Button variant="secondary" size="sm" onClick={() => void load(scope)}>
          {t('dialogs:layout.recoverRetry')}
        </Button>
      }
    >
      {t('dialogs:layout.recoverUnavailable')}
    </Notice>
  )

  if (project.state === 'unsupported') return null

  return (
    <div className="flex flex-col gap-3" data-layout-recover>
      {project.state === 'unavailable' && retry('project')}
      {project.state === 'ok' && project.list.index === 'degraded' && retry('project')}
      {(ownRows.length > 0 || (project.state === 'ok' && (hasMore(project.list) || project.list.truncated))) && (
        <section className="flex flex-col gap-1" data-recover-group="project">
          <h3 className="type-section px-1">{t('dialogs:layout.recoverProjectTitle')}</h3>
          {ownRows.length === 0 && project.state === 'ok' && hasMore(project.list) && (
            <p className="px-1 text-sm text-ink-3">{t('dialogs:layout.recoverKeepLooking')}</p>
          )}
          {project.state === 'ok' && project.list.truncated && (
            <p className="px-1 text-sm text-ink-3" data-recover-truncated>
              {t('dialogs:layout.recoverTruncated')}
            </p>
          )}
          <ul className="-mx-1 flex max-h-60 flex-col gap-0.5 overflow-y-auto">
            {ownRows.map((it) => (
              <li key={it.doc_id} className="flex">
                <Row
                  item={it}
                  currentId={currentId}
                  busy={busy}
                  onActivate={() => void openOwn(it)}
                  action={t('dialogs:layout.load')}
                />
              </li>
            ))}
          </ul>
          {moreButton('project', project)}
        </section>
      )}

      {project.state !== 'idle' && (
        <Details
          open={otherOpen}
          onToggle={(e) => {
            const isOpen = (e.currentTarget as HTMLDetailsElement).open
            setOtherOpen(isOpen)
            if (isOpen && other.state === 'idle') void load('unknown')
          }}
          data-recover-group="unknown"
        >
          <Summary className="type-section cursor-default">
            {t('dialogs:layout.recoverOtherTitle')}
          </Summary>
          <div className="mt-1 flex flex-col gap-2">
            <p className="text-sm text-ink-3">{t('dialogs:layout.recoverOtherHint')}</p>
            {other.state === 'unavailable' && retry('unknown')}
            {other.state === 'ok' && other.list.index === 'degraded' && retry('unknown')}
            {other.state === 'loading' && otherRows.length === 0 && (
              <p className="text-sm text-ink-3">{t('dialogs:layout.recoverLoading')}</p>
            )}
            {(other.state === 'ok' || other.state === 'unsupported') && otherRows.length === 0 && (
              <p className="text-sm text-ink-3">
                {other.state === 'ok' && hasMore(other.list)
                  ? t('dialogs:layout.recoverKeepLooking')
                  : other.state === 'ok' && other.list.truncated
                    ? t('dialogs:layout.recoverTruncated')
                    : t('dialogs:layout.recoverEmptyOther')}
              </p>
            )}
            {otherRows.length > 0 && (
              <ul className="-mx-1 flex max-h-72 flex-col gap-0.5 overflow-y-auto">
                {otherRows.map((it) => {
                  const issue = rowIssue(it)
                  const isSel = selected === it.doc_id
                  return (
                    <li key={it.doc_id} className="flex flex-col">
                      <Row
                        item={it}
                        currentId={currentId}
                        busy={busy}
                        selected={isSel}
                        onActivate={() => setSelected(isSel ? null : it.doc_id)}
                      />
                      {isSel && issue === null && (
                        <div className="mx-2 mb-1 flex flex-col gap-2 rounded-md bg-surface-2 px-3 py-2" data-recover-confirm>
                          <p className="text-sm text-ink-2">{t('dialogs:layout.recoverCopyHint')}</p>
                          <div className="flex gap-2">
                            <Button
                              variant="primary"
                              size="sm"
                              loading={busy}
                              data-recover-copy
                              onClick={() => confirmCopy(it)}
                            >
                              {t('dialogs:layout.recoverCopy')}
                            </Button>
                            <Button variant="secondary" size="sm" disabled={busy} onClick={() => setSelected(null)}>
                              {t('common:actions.cancel')}
                            </Button>
                          </div>
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
            {other.state === 'ok' && other.list.truncated && otherRows.length > 0 && (
              <p className="text-sm text-ink-3" data-recover-truncated>
                {t('dialogs:layout.recoverTruncated')}
              </p>
            )}
            {moreButton('unknown', other)}
          </div>
        </Details>
      )}

      {notice && <Notice tone="warn">{notice}</Notice>}
    </div>
  )
}

function Row({
  item,
  currentId,
  busy,
  selected = false,
  onActivate,
  action,
}: {
  item: RecoveryCandidateItem
  currentId: string
  busy: boolean
  selected?: boolean
  onActivate: () => void
  /** 行尾 hover 才浮出的动作词（「打开」）；折叠组里的行没有，选中后在下面确认 */
  action?: string
}) {
  const { t } = useTranslation(['dialogs'])
  const issue = rowIssue(item)
  const when = savedAt(item)
  const runtime = (item.runtime ?? []).slice(0, 2).map((r) => t('dialogs:layout.recoverRuntime', r))
  return (
    <button
      type="button"
      data-recover-row={item.doc_id}
      disabled={busy || issue !== null || item.doc_id === currentId}
      aria-expanded={action ? undefined : selected}
      onClick={onActivate}
      className={cn(
        listRowClass({ size: 'lg', selected }),
        'min-h-13 w-full gap-2.5 px-2 text-left disabled:opacity-40',
      )}
    >
      {item.sketch ? (
        <CanvasThumb page={item.sketch.page} objects={item.sketch.objects} />
      ) : (
        <span aria-hidden className="grid h-10 w-14 shrink-0 place-items-center">
          <FolderOpen size={ICON_SIZE.sm} className="text-ink-3" />
        </span>
      )}
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate">{item.name || t('dialogs:layout.recoverUnnamed')}</span>
        <span className={cn(rowMetaClass(selected), 'truncate')}>
          {issue
            ? t(ISSUE_KEY[issue] as 'dialogs:layout.recoverIssueInvalid')
            : [
                when ? formatRelativeTime(when) : null,
                t('dialogs:layout.recoverCounts', { canvases: item.canvases, objects: item.objects }),
                runtime.length ? listJoin(runtime) : null,
              ]
                .filter(Boolean)
                .join(' · ')}
        </span>
      </span>
      {action && issue === null && (
        <span className="hidden shrink-0 text-sm text-ink-2 group-hover:inline group-focus-visible:inline">{action}</span>
      )}
    </button>
  )
}
