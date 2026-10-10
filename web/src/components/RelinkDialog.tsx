import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link2, TriangleAlert } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  materializePaste,
  materializeRelink,
  useClipboardStore,
  type MissingAsset,
} from '@/lib/clipboard'
import { panelSrc } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { TailPath } from './DirBrowser'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { RetryImg } from './ui/RetryImg'
import { Select } from './ui/Select'

/**
 * 缺失素材处置：粘贴（跨图库剪贴板）与项目包导入共用。
 * 要么重新链接到现有素材，要么明确跳过——绝不静默生成空面板。
 */
export function RelinkDialog() {
  const { t } = useTranslation('dialogs')
  const current = useClipboardStore((s) => s.pending)
  const setPending = useClipboardStore((s) => s.setPending)
  const assets = useAssetStore((s) => s.byId)
  const [choices, setChoices] = useState<MissingAsset[]>([])
  /** 哪几行是按名字自动配上的（用户改过就不再标）：说出口，别让人以为是自己选的 */
  const [byName, setByName] = useState<ReadonlySet<string>>(new Set())
  // 常驻挂载：关的那 90ms 里载荷已清，正文按最后一份画（Dialog 的常驻写法）
  const last = useRef(current)
  if (current) last.current = current
  const pending = last.current

  useEffect(() => {
    if (!current) return
    // 预填：同名素材自动匹配（常见于同图库不同机器的相对路径差异）
    const auto = new Set<string>()
    setChoices(
      current.missing.map((m) => {
        const match = Object.values(assets).find(
          (a) => a.name === m.name || a.id.endsWith(`/${m.name}.pdf`) || a.id.endsWith(`/${m.name}.png`),
        )
        if (match) auto.add(m.fileId)
        return { ...m, relinkTo: match?.id }
      }),
    )
    setByName(auto)
  }, [current, assets])

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
  const cancel = () => setPending(null)

  return (
    <Dialog
      open={!!current}
      onOpenChange={(v) => !v && cancel()}
      onEscape={cancel}
      title={t(isPaste ? 'relink.titlePaste' : 'relink.titleDoc')}
      description={t(isPaste ? 'relink.descPaste' : 'relink.descDoc')}
      size="lg"
      anchor="relink"
      footer={{
        secondary: (
          <Button variant="secondary" size="lg" onClick={cancel}>
            {t(isPaste ? 'relink.cancelPaste' : 'relink.cancelDoc')}
          </Button>
        ),
        primary: (
          <Button variant="primary" size="lg" data-relink-confirm onClick={confirm}>
            <Link2 size={ICON_SIZE.sm} />
            {t(isPaste ? 'relink.confirmPaste' : 'relink.confirmDoc')}
          </Button>
        ),
      }}
    >
      <ul className="flex flex-col gap-1">
        {choices.map((m, i) => {
          const target = m.relinkTo ? assets[m.relinkTo] : undefined
          const thumb = target ? panelSrc(target.id, target.kind, 160, target.mtime) : null
          return (
            <li key={m.fileId} className="flex items-center gap-3 rounded-md py-1.5" data-relink-row={m.fileId}>
              {/* 选中的替代素材的缩略图；还没选（或保持缺失）时是一块带警示图标的空格——
                  缺件是**警告**不是阻断（全面打磨 D47） */}
              <span className="flex h-9 w-12 shrink-0 items-center justify-center overflow-hidden rounded-sm bg-surface-2 ring-1 ring-inset ring-border">
                {thumb ? (
                  <RetryImg
                    src={thumb}
                    alt=""
                    className="h-full w-full bg-surface object-contain"
                    data-relink-thumb
                  />
                ) : (
                  <TriangleAlert size={ICON_SIZE.sm} className="text-warn" aria-hidden />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <p className="flex min-w-0 items-baseline gap-1.5 text-ink" title={m.fileId}>
                  <span className="truncate">{m.name}</span>
                  {m.count > 1 && (
                    <span className="type-meta shrink-0">{t('relink.refCount', { count: m.count })}</span>
                  )}
                  {byName.has(m.fileId) && m.relinkTo && (
                    <span className="type-meta shrink-0" data-relink-by-name>
                      {t('relink.matchedByName')}
                    </span>
                  )}
                </p>
                {/* 路径尾部才有区分力（文件名）：放不下时从左边裁、留尾巴（`TailPath`，与项目选择器同一份） */}
                <TailPath path={m.fileId} />
              </div>
              <div className="w-52 shrink-0">
                <Select
                  value={m.relinkTo ?? ''}
                  placeholder={t(isPaste ? 'relink.skipPanel' : 'relink.keepMissing')}
                  onChange={(v) => {
                    setChoices((cs) => cs.map((c, j) => (j === i ? { ...c, relinkTo: v || undefined } : c)))
                    setByName((prev) => {
                      const next = new Set(prev)
                      next.delete(m.fileId)
                      return next
                    })
                  }}
                  options={[
                    { value: '', label: t(isPaste ? 'relink.skipPanel' : 'relink.keepMissing') },
                    ...options,
                  ]}
                  ariaLabel={t('relink.selectAria', { name: m.name })}
                />
              </div>
            </li>
          )
        })}
      </ul>
    </Dialog>
  )
}
