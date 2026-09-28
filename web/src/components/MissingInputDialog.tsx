import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { MissingInputItem, MissingInputOffer } from '@/lib/api'
import { isDesktop, pickAnyFile, pickDirectory } from '@/lib/desktop'
import { useEnvStore } from '@/store/envStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'

/**
 * 数据找不到时请用户指认一次（ADR 0106）。
 *
 * 渲染以 `missing_input`（worker 说得出缺的是哪一串）或带载荷的「脚本跑完没出图」回来时弹出：说清
 * 脚本要的是哪个、为什么可能找不到，请用户**亲手**指认那个文件或它所在的文件夹——Tavotto 不按同名
 * 去搜、不预选候选（旁边可能有同名不同值的文件）。指认之后后端推一条只读改指规则、按项目记住、重跑。
 *
 * 缺的东西若是脚本用 `exists` / `glob` / `listdir` 探的（`via` 不是 `open`），改指救不回那一问——
 * 如实说，不给一个点了也没用的按钮。桌面用系统选择器；浏览器模式拿不到本机路径，让用户粘贴。
 */
export function MissingInputDialog() {
  const { t } = useTranslation('errors')
  const offer = useEnvStore((s) => s.missingInput)
  const dismiss = useEnvStore((s) => s.dismissMissingInput)
  const pointAtData = useEnvStore((s) => s.pointAtData)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [typed, setTyped] = useState('')
  useEffect(() => {
    setError(null)
    setTyped('')
  }, [offer])
  if (!offer) return null
  const primary = primaryOf(offer)
  if (!primary) return null
  const remappable = primary.via === 'open'
  const others = offer.others.filter((o) => o.path !== primary.path)
  const desktop = isDesktop()

  const submit = async (chosen: string | null, kind: 'file' | 'dir' | 'auto') => {
    if (!chosen) return // 取消选择器不是错误
    setBusy(true)
    const err = await pointAtData(primary.path, chosen, kind)
    setBusy(false)
    setError(err)
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !busy) dismiss()
      }}
      title={t('engine.missingInputTitle')}
      description={t('engine.missingInputBody', { script: offer.script })}
      size="sm"
      busy={busy}
      anchor="missing-input"
      footer={
        <>
          <Button variant="secondary" size="md" disabled={busy} onClick={dismiss}>
            {t('engine.missingInputLater')}
          </Button>
          {remappable && desktop && (
            <>
              <Button
                variant="secondary"
                size="md"
                disabled={busy}
                data-testid="missing-input-pick-dir"
                onClick={() =>
                  void pickDirectory(t('engine.missingInputPickDirTitle')).then((d) => submit(d, 'dir'))
                }
              >
                {t('engine.missingInputPickDir')}
              </Button>
              <Button
                variant="primary"
                size="md"
                disabled={busy}
                data-testid="missing-input-pick-file"
                onClick={() =>
                  void pickAnyFile(t('engine.missingInputPickFileTitle', { name: baseName(primary.path) })).then(
                    (f) => submit(f, 'file'),
                  )
                }
              >
                {t('engine.missingInputPickFile')}
              </Button>
            </>
          )}
          {remappable && !desktop && (
            <Button
              variant="primary"
              size="md"
              disabled={busy || !typed.trim()}
              data-testid="missing-input-use-path"
              onClick={() => void submit(typed.trim(), 'auto')}
            >
              {t('engine.missingInputUsePath')}
            </Button>
          )}
        </>
      }
    >
      {/* 路径是用户自己写的，不翻译 */}
      <p className="break-all font-mono text-sm text-ink" data-missing-input-path>
        {primary.path}
      </p>
      <p className="mt-2 text-xs leading-relaxed text-ink-2">
        {remappable
          ? t(primary.absolute ? 'engine.missingInputWhyAbsolute' : 'engine.missingInputWhyRelative')
          : t('engine.missingInputProbe')}
      </p>
      {remappable && !desktop && (
        <TextInput
          className="mt-2 w-full"
          value={typed}
          disabled={busy}
          placeholder={t('engine.missingInputPathPlaceholder')}
          aria-label={t('engine.missingInputPathPlaceholder')}
          onChange={(e) => setTyped(e.target.value)}
        />
      )}
      {others.length > 0 && (
        <div className="mt-2">
          <p className="text-xs text-ink-3">{t('engine.missingInputOthers')}</p>
          <ul className="mt-0.5 flex flex-col gap-0.5" data-missing-input-others>
            {others.map((o) => (
              <li key={o.path} className="break-all font-mono text-xs text-ink-2">
                {o.path}
              </li>
            ))}
          </ul>
        </div>
      )}
      {remappable && (
        <p className="mt-2 text-xs leading-relaxed text-ink-3">{t('engine.missingInputReadOnly')}</p>
      )}
      {error && <p className="mt-1 text-xs text-danger">{error}</p>}
    </Dialog>
  )
}

/**
 * 以哪一条为主：worker 说出来的那串优先；否则（「没出图」路径）先挑改指救得回来的，
 * 都救不回来才挑第一条如实说明。
 */
export function primaryOf(offer: MissingInputOffer): MissingInputItem | null {
  if (offer.requested) return { path: offer.requested, absolute: offer.absolute, via: offer.via }
  return offer.others.find((o) => o.via === 'open') ?? offer.others[0] ?? null
}

function baseName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean)
  return parts.at(-1) ?? path
}
