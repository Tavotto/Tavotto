import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg } from '@/i18n'
import { FolderOpen, Save } from 'lucide-react'
import { backendErrorText, fetchLayout, fetchLayoutNames, saveLayout } from '@/lib/api'
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
  const nameRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => {
    if (!open) return
    setName(docName)
    setError(null)
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

  const doSave = async () => {
    const stem = name.trim()
    if (!stem) return
    setBusy(true)
    setError(null)
    try {
      // 保存整个项目文档（schema 3，含全部画布）；文件名即项目名
      const store = useDocumentStore.getState()
      store.renameProject(stem)
      await saveLayout(stem, useDocumentStore.getState().buildProject())
      setNames(await fetchLayoutNames())
      useUiStore.getState().setStatus(msg('layout.saved', { name: stem }, 'dialogs'))
      setOpen(false)
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  const doLoad = async (target: string) => {
    setBusy(true)
    setError(null)
    try {
      const payload = await fetchLayout(target)
      openLayoutDocument(normalizeLayout(payload, target))
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
            onClick={doSave}
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
              if (e.key === 'Enter') doSave()
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

        {error && <p className="text-xs text-danger">{error}</p>}
      </div>
    </Dialog>
  )
}
