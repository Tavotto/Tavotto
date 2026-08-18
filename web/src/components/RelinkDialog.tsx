import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link2, TriangleAlert } from 'lucide-react'
import {
  materializePaste,
  materializeRelink,
  useClipboardStore,
  type MissingAsset,
} from '@/lib/clipboard'
import { useAssetStore } from '@/store/assetStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Select } from './ui/Select'

/**
 * 缺失素材处置：粘贴（跨图库剪贴板）与项目包导入共用。
 * 要么重新链接到现有素材，要么明确跳过——绝不静默生成空面板。
 */
export function RelinkDialog() {
  const { t } = useTranslation('dialogs')
  const pending = useClipboardStore((s) => s.pending)
  const setPending = useClipboardStore((s) => s.setPending)
  const assets = useAssetStore((s) => s.byId)
  const [choices, setChoices] = useState<MissingAsset[]>([])

  useEffect(() => {
    if (!pending) return
    // 预填：同名素材自动匹配（常见于同图库不同机器的相对路径差异）
    setChoices(
      pending.missing.map((m) => {
        const match = Object.values(assets).find(
          (a) => a.name === m.name || a.id.endsWith(`/${m.name}.pdf`) || a.id.endsWith(`/${m.name}.png`),
        )
        return { ...m, relinkTo: match?.id }
      }),
    )
  }, [pending, assets])

  if (!pending) return null
  const isPaste = pending.mode === 'paste'

  const options = Object.values(assets)
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((a) => ({ value: a.id, label: t('relink.assetOption', { name: a.name, folder: a.folder }) }))

  const confirm = () => {
    if (pending.mode === 'paste') materializePaste(pending.payload, choices)
    else materializeRelink(choices)
    setPending(null)
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && setPending(null)}
      title={t(isPaste ? 'relink.titlePaste' : 'relink.titleDoc')}
      description={t(isPaste ? 'relink.descPaste' : 'relink.descDoc')}
      size="lg"
      footer={
        <>
          <Button variant="outline" size="md" onClick={() => setPending(null)}>
            {t(isPaste ? 'relink.cancelPaste' : 'relink.cancelDoc')}
          </Button>
          <Button variant="primary" size="md" onClick={confirm}>
            <Link2 size={14} />
            {t(isPaste ? 'relink.confirmPaste' : 'relink.confirmDoc')}
          </Button>
        </>
      }
    >
      <ul className="flex flex-col gap-2">
        {choices.map((m, i) => (
          <li key={m.fileId} className="flex items-center gap-2">
            <TriangleAlert size={13} className="shrink-0 text-danger" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs text-ink" title={m.fileId}>
                {m.name}
                {m.count > 1 && (
                  <span className="text-ink-3">{t('relink.refCount', { count: m.count })}</span>
                )}
              </p>
              <p className="truncate text-xs text-ink-3">{m.fileId}</p>
            </div>
            <div className="w-56 shrink-0">
              <Select
                value={m.relinkTo ?? ''}
                placeholder={t(isPaste ? 'relink.skipPanel' : 'relink.keepMissing')}
                onChange={(v) =>
                  setChoices((cs) =>
                    cs.map((c, j) => (j === i ? { ...c, relinkTo: v || undefined } : c)),
                  )
                }
                options={[
                  { value: '', label: t(isPaste ? 'relink.skipPanel' : 'relink.keepMissing') },
                  ...options,
                ]}
                ariaLabel={t('relink.selectAria', { name: m.name })}
              />
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs leading-relaxed text-ink-3">{t('relink.footnote')}</p>
    </Dialog>
  )
}
