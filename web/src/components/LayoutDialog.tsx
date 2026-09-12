import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg } from '@/i18n'
import { FolderOpen, Save } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
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
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'

/**
 * 「另存为」与「打开」（审计 T04）。
 *
 * 改造前是**一个**弹窗同时承担两件事：上半截是保存表单，下半截是一列可载入
 * 的文件，底部一颗「保存为画布文件」。用户从「载入」进来时正对着的却是保存
 * 表单，而那颗主按钮会把当前文档写到框里的名字下。两件事的后果相反（一个
 * 写盘、一个丢弃当前工作换一份进来），不该共用一屏。
 *
 * 现在按 `uiStore.layoutIntent` 分成两种形态，各自只做一件事：
 * - `save`：只有名字和位置，主按钮是「另存为」；
 * - `load`：只有文档列表，一个能写盘的控件都没有。
 *
 * 词汇统一到**项目 > 文档 > 画布**：这里存取的是一份文档（schema 3，含它
 * 全部画布），所以不再叫「画布文件」，字段也不再叫「布局名称」。
 */
export function LayoutDialog() {
  const { t } = useTranslation(['dialogs', 'common'])
  const open = useUiStore((s) => s.layoutOpen)
  const setOpen = useUiStore((s) => s.setLayoutOpen)
  const docName = useDocumentStore((s) => s.doc.name)
  /**
   * 文档落在哪个目录——**后端说了算**（`project_status.document_dir`）。
   * 「项目内 tavottofile/」这条规则的出处只有 `app.project_layout_dir()`，
   * 界面自己拼一个路径就是把它抄成了第二份，而「旧位置只读兼容」「没打开
   * 项目时退回数据目录」这两条分支抄不过去。
   */
  const documentDir = useProjectStore((s) => s.project?.document_dir)

  const intent = useUiStore((s) => s.layoutIntent)
  const saving = intent === 'save'
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
    // 另存那一屏也要这份清单：撞名的裁决在后端，但「这个名字已经有了」
    // 要在用户按下按钮之前就说
    fetchLayoutNames()
      .then(setNames)
      .catch((e) => setError(backendErrorText(e)))
  }, [open, docName])

  // 从菜单进来时焦点直接落在用户选的那件事上。
  // 要等一帧：弹窗自己的焦点陷阱在挂载后也会抢焦点，抢早了会被它覆盖。
  useEffect(() => {
    if (!open) return
    const id = requestAnimationFrame(() => {
      if (saving) {
        nameRef.current?.focus()
        nameRef.current?.select()
      } else {
        listRef.current?.querySelector('button')?.focus()
      }
    })
    return () => cancelAnimationFrame(id)
  }, [open, saving, names.length])

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
      // 保存整份文档（schema 3，含全部画布）；文件名即文档名
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
      title={t(saving ? 'dialogs:layout.saveTitle' : 'dialogs:layout.openTitle')}
      size="md"
      busy={busy}
      footer={
        <>
          <Button variant="secondary" size="md" disabled={busy} onClick={() => setOpen(false)}>
            {t('common:actions.close')}
          </Button>
          {/* 「打开」那一屏一个能写盘的控件都没有：主按钮只在另存时出现 */}
          {saving && (
            <Button
              variant="primary"
              size="md"
              disabled={!name.trim()}
              loading={busy}
              loadingLabel={t('dialogs:layout.saving')}
              onClick={() => doSave()}
            >
              <Save size={ICON_SIZE.md} />
              {t('dialogs:layout.saveAs')}
            </Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {saving ? (
          <div>
            <label
              className="mb-1.5 block type-section"
              htmlFor="layout-save-name"
            >
              {t('dialogs:layout.nameLabel')}
            </label>
            <TextInput
              id="layout-save-name"
              ref={nameRef}
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void doSave()
              }}
              placeholder={t('dialogs:layout.namePlaceholder')}
              className="h-7"
            />
            {/* 位置：另存要回答的第二件事。后端没给就不编一个出来 */}
            {documentDir && (
              <p className="mt-1.5 truncate font-mono text-xs text-ink-3" title={documentDir}>
                {t('dialogs:layout.savesInto', { dir: documentDir })}
              </p>
            )}
            {names.includes(name.trim()) && (
              <p className="mt-1.5 text-xs text-ink-2">{t('dialogs:layout.nameTaken')}</p>
            )}
          </div>
        ) : names.length === 0 ? (
          <p className="py-2 text-xs text-ink-3">{t('dialogs:layout.empty')}</p>
        ) : (
          <ul ref={listRef} className="max-h-72 overflow-y-auto rounded-sm border border-border">
            {names.map((n, i) => (
              <li key={n}>
                <button
                  disabled={busy}
                  onClick={() => doLoad(n)}
                  className={cn(
                    'flex h-7 w-full items-center gap-2 px-2 text-left text-xs text-ink',
                    'hover:bg-surface-hover disabled:opacity-40',
                    i > 0 && 'border-t border-border',
                  )}
                >
                  <FolderOpen size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />
                  <span className="min-w-0 flex-1 truncate">{n}</span>
                  <span className="shrink-0 text-xs text-ink-3">{t('dialogs:layout.load')}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

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
