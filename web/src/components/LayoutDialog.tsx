import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg } from '@/i18n'
import { FolderOpen, Save } from 'lucide-react'
import {
  ApiError,
  REVISION_ABSENT,
  backendErrorText,
  fetchLayout,
  fetchLayoutNames,
  saveLayout,
  type DiskDocumentSummary,
} from '@/lib/api'
import { knownLayoutRevision, rememberLayoutRevision } from '@/lib/layoutRevision'
import { normalizeLayout } from '@/lib/migrate'
import { cn } from '@/lib/utils'
import { openLayoutDocument } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'

export function LayoutDialog() {
  const { t } = useTranslation(['dialogs', 'common'])
  const open = useUiStore((s) => s.layoutOpen)
  const setOpen = useUiStore((s) => s.setLayoutOpen)
  const docName = useDocumentStore((s) => s.doc.name)

  const intent = useUiStore((s) => s.layoutIntent)
  const [names, setNames] = useState<string[]>([])
  const [name, setName] = useState(docName)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /**
   * 磁盘上那个名字下已经有一份**不是本窗口写的**内容（后端 409）。
   * 这不是错误，是一个待用户裁决的岔口：出口只有「覆盖」一条，而覆盖要
   * 拿 409 里回的 hash 当基线（ADR 0024 §3c——**不是清空基线**：清空等于
   * 用户按一次覆盖就把这个名字的外部修改检测永久关掉了）。
   */
  const [conflict, setConflict] = useState<{
    name: string
    revision: string
    summary: DiskDocumentSummary | null
  } | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => {
    if (!open) return
    setName(docName)
    setError(null)
    setConflict(null)
    fetchLayoutNames()
      .then(setNames)
      .catch((e) => setError(backendErrorText(e)))
  }, [open, docName])

  // 从菜单进来时焦点直接落在用户选的那件事上。
  // 要等一帧：弹窗自己的焦点陷阱在挂载后也会抢焦点，抢早了会被它覆盖。
  useEffect(() => {
    if (!open) return
    const id = requestAnimationFrame(() => {
      if (intent === 'save') {
        nameRef.current?.focus()
        nameRef.current?.select()
      } else {
        listRef.current?.querySelector('button')?.focus()
      }
    })
    return () => cancelAnimationFrame(id)
  }, [open, intent, names.length])

  /**
   * `overwrite` = 用户在冲突提示上按了「覆盖」，带上 409 里回的那份 hash。
   * 没有它时基线是本窗口读到 / 写成功过的那一份；一次都没确认过就发
   * `REVISION_ABSENT`——后端于是把「磁盘上有一份我从没读过的内容」判成冲突。
   */
  const doSave = async (overwrite?: string) => {
    const stem = name.trim()
    if (!stem) return
    setBusy(true)
    setError(null)
    setConflict(null)
    try {
      // 保存整个项目文档（schema 3，含全部画布）；文件名即项目名
      const store = useDocumentStore.getState()
      store.renameProject(stem)
      const baseRevision = overwrite ?? knownLayoutRevision(stem) ?? REVISION_ABSENT
      const res = await saveLayout(stem, useDocumentStore.getState().buildProject(), baseRevision)
      rememberLayoutRevision(stem, res.revision)
      setNames(await fetchLayoutNames())
      useUiStore.getState().setStatus(msg('layout.saved', { name: stem }, 'dialogs'))
      setOpen(false)
    } catch (e) {
      const revision =
        e instanceof ApiError && e.status === 409 && e.body.code === 'external_change'
          ? e.body.revision
          : null
      if (typeof revision === 'string') {
        setConflict({
          name: stem,
          revision,
          summary: (e as ApiError).body.summary as DiskDocumentSummary | null,
        })
      } else {
        setError(backendErrorText(e))
      }
    } finally {
      setBusy(false)
    }
  }

  const doLoad = async (target: string) => {
    setBusy(true)
    setError(null)
    setConflict(null)
    try {
      const { doc, revision } = await fetchLayout(target)
      // 读到了就记下基线：之后覆盖这个名字不必再打扰用户一次
      rememberLayoutRevision(target, revision)
      openLayoutDocument(normalizeLayout(doc, target))
      setOpen(false)
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={t('dialogs:layout.title')}
      description={t('dialogs:layout.description')}
      size="md"
      busy={busy}
      footer={
        <>
          <Button variant="outline" size="md" disabled={busy} onClick={() => setOpen(false)}>
            {t('common:actions.close')}
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={!name.trim()}
            loading={busy}
            loadingLabel={t('dialogs:layout.saving')}
            onClick={() => doSave()}
          >
            <Save size={14} />
            {t('dialogs:layout.saveAs')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-[.06em] text-ink-3">
            {t('dialogs:layout.saveHeading')}
          </h3>
          <TextInput
            ref={nameRef}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void doSave()
            }}
            placeholder={t('dialogs:layout.namePlaceholder')}
            className="h-7"
          />
        </div>

        <div>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-[.06em] text-ink-3">
            {t('dialogs:layout.loadHeading')}
          </h3>
          {names.length === 0 ? (
            <p className="py-2 text-xs text-ink-3">{t('dialogs:layout.empty')}</p>
          ) : (
            <ul ref={listRef} className="max-h-56 overflow-y-auto rounded-sm border border-border">
              {names.map((n, i) => (
                <li key={n}>
                  <button
                    disabled={busy}
                    onClick={() => doLoad(n)}
                    className={cn(
                      'flex h-7 w-full items-center gap-2 px-2 text-left text-xs text-ink',
                      'hover:bg-ink/[.04] disabled:opacity-40',
                      i > 0 && 'border-t border-border',
                    )}
                  >
                    <FolderOpen size={12} className="shrink-0 text-ink-3" />
                    <span className="min-w-0 flex-1 truncate">{n}</span>
                    <span className="shrink-0 text-xs text-ink-3">{t('dialogs:layout.load')}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {conflict && (
          <div className="flex flex-col gap-1.5 rounded-sm border border-warn/40 bg-warn-subtle p-2">
            <p className="text-xs text-ink">
              {t('dialogs:layout.conflict', { name: conflict.name })}
            </p>
            {conflict.summary && (
              <p className="text-xs text-ink-3">
                {t('dialogs:layout.conflictDisk', {
                  objects: conflict.summary.objects,
                  canvases: conflict.summary.canvases,
                })}
              </p>
            )}
            <div>
              <Button
                variant="danger"
                size="sm"
                disabled={busy}
                onClick={() => doSave(conflict.revision)}
              >
                {t('dialogs:layout.overwrite')}
              </Button>
            </div>
          </div>
        )}

        {error && <p className="text-xs text-danger">{error}</p>}
      </div>
    </Dialog>
  )
}
