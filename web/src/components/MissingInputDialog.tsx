import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  type MissingInputItem,
  type MissingInputOffer,
  type ScriptEditPreview,
  type ScriptEditSkipped,
  VIAS_NEED_REWRITE,
} from '@/lib/api'
import { isDesktop, pickAnyFile, pickDirectory } from '@/lib/desktop'
import { useEnvStore } from '@/store/envStore'
import { Button } from './ui/Button'
import { Checkbox } from './ui/Checkbox'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'

/**
 * 数据找不到时请用户指认一次（ADR 0106）。
 *
 * 渲染以 `missing_input`（worker 说得出缺的是哪一串）或带载荷的「脚本跑完没出图」回来时弹出：说清
 * 脚本要的是哪个、为什么可能找不到，请用户**亲手**指认那个文件或它所在的文件夹——Tavotto 不按同名
 * 去搜、不预选候选（旁边可能有同名不同值的文件）。指认之后后端推一条只读改指规则、按项目记住、重跑。
 *
 * 缺的东西若是脚本用 `exists` / `glob` / `listdir` 探的、或 h5py / netCDF 这类 C++ 读取器直接打开的
 * （`via` 是 `probe` / `glob` / `native`），改指救不回——如实说，出口换成**经确认改写脚本里那串路径**
 * （ADR 0110）：用户指认数据现在的位置 → 后端生成逐行 diff（前端只渲染）→ 勾选「我已查看」→「修改脚本」。
 * 那颗按钮不是默认焦点、回车不触发（`Dialog` 打开时焦点落在内容区本身）。
 * 桌面用系统选择器；浏览器模式拿不到本机路径，让用户粘贴。
 */
export function MissingInputDialog() {
  const { t } = useTranslation('errors')
  const offer = useEnvStore((s) => s.missingInput)
  const dismiss = useEnvStore((s) => s.dismissMissingInput)
  const pointAtData = useEnvStore((s) => s.pointAtData)
  const previewRewrite = useEnvStore((s) => s.previewRewrite)
  const preview = useEnvStore((s) => s.rewritePreview)
  const skipped = useEnvStore((s) => s.rewriteSkipped)
  const rewriteError = useEnvStore((s) => s.rewriteError)
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
  if (preview) return <RewriteConfirm preview={preview} />
  const remappable = primary.via === 'open'
  // 改指救不回的几档：出口是经确认改写脚本（ADR 0110）
  const rewritable = VIAS_NEED_REWRITE.includes(primary.via)
  const pickable = remappable || rewritable
  // glob 模式没有「那个文件」可找：只给「选择所在文件夹」（`via` 是后端给的事实，前端不再按路径长相判）
  const folderOnly = primary.via === 'glob'
  const others = offer.others.filter((o) => o.path !== primary.path)
  const desktop = isDesktop()

  const submit = async (chosen: string | null, kind: 'file' | 'dir' | 'auto') => {
    if (!chosen) return // 取消选择器不是错误
    setBusy(true)
    const err = rewritable
      ? await previewRewrite(primary.path, chosen, kind)
      : await pointAtData(primary.path, chosen, kind)
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
          {pickable && desktop && (
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
              {!folderOnly && (
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
              )}
            </>
          )}
          {pickable && !desktop && (
            <Button
              variant="primary"
              size="md"
              disabled={busy || !typed.trim()}
              data-testid="missing-input-use-path"
              onClick={() => void submit(typed.trim(), 'auto')}
            >
              {rewritable ? t('engine.missingInputPreviewRewrite') : t('engine.missingInputUsePath')}
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
          : primary.via === 'native'
            ? t('engine.missingInputNative')
            : t('engine.missingInputProbe')}
      </p>
      {rewritable && (
        <p className="mt-2 text-xs leading-relaxed text-ink-2" data-missing-input-rewrite-hint>
          {t('engine.missingInputRewriteHint')}
        </p>
      )}
      {pickable && !desktop && (
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
      {(error ?? rewriteError) && <p className="mt-1 text-xs text-danger">{error ?? rewriteError}</p>}
      {skipped.length > 0 && <SkippedList items={skipped} />}
    </Dialog>
  )
}

/**
 * 确认页（ADR 0110 §八）：醒目地说「这会修改你的脚本文件」+ 完整路径；逐行 diff 只渲染后端给的行；
 * 没改的逐条原因；两处备份位置；git 状态与校验清单提示。必须勾选「我已查看」才能点「修改脚本」。
 */
function RewriteConfirm({ preview }: { preview: ScriptEditPreview }) {
  const { t } = useTranslation('errors')
  const cancel = useEnvStore((s) => s.cancelRewrite)
  const commit = useEnvStore((s) => s.commitRewrite)
  const dismiss = useEnvStore((s) => s.dismissMissingInput)
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const apply = async () => {
    setBusy(true)
    const err = await commit()
    setBusy(false)
    setError(err)
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !busy) dismiss()
      }}
      title={t('engine.rewriteTitle')}
      size="md"
      busy={busy}
      anchor="missing-input-rewrite"
      footer={
        <>
          <Button variant="secondary" size="md" disabled={busy} onClick={cancel}>
            {t('engine.rewriteBack')}
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={busy || !checked}
            data-testid="missing-input-rewrite-apply"
            onClick={() => void apply()}
          >
            {t('engine.rewriteApply')}
          </Button>
        </>
      }
    >
      <div className="rounded-md bg-warn-subtle px-2 py-1.5" data-rewrite-warning>
        <p className="text-sm font-medium text-ink">{t('engine.rewriteWarning')}</p>
        {/* 用户自己的路径，不翻译 */}
        <p className="mt-0.5 break-all font-mono text-xs text-ink-2">{preview.script_abs}</p>
      </div>
      <p className="mt-2 text-xs text-ink-2">{t('engine.rewriteCount', { n: preview.edits.length })}</p>
      <ol className="mt-1 flex flex-col gap-1" data-rewrite-rows>
        {preview.rows.map((r) => (
          <li key={r.line} className="rounded-sm border border-border px-1.5 py-1 font-mono text-xs">
            <span className="text-ink-3">{t('engine.rewriteLine', { line: r.line })}</span>
            <p className="break-all text-danger">− {r.before}</p>
            <p className="break-all text-ok">+ {r.after}</p>
          </li>
        ))}
      </ol>
      {preview.skipped.length > 0 && <SkippedList items={preview.skipped} />}
      <p className="mt-2 text-xs text-ink-3">{t('engine.rewriteBackups')}</p>
      <ul className="break-all font-mono text-xs text-ink-3">
        <li>{preview.backups.project}</li>
        <li>{preview.backups.mirror}</li>
      </ul>
      {preview.git && (
        <p className="mt-1 text-xs text-ink-3">
          {preview.git.tracked
            ? preview.git.dirty
              ? t('engine.rewriteGitDirty')
              : t('engine.rewriteGitClean')
            : t('engine.rewriteGitUntracked')}
        </p>
      )}
      {preview.checksums.length > 0 && (
        <p className="mt-1 text-xs text-warn">
          {t('engine.rewriteChecksums', { files: preview.checksums.join(', ') })}
        </p>
      )}
      <label className="mt-2 flex items-start gap-1.5 text-sm text-ink">
        <Checkbox
          className="mt-0.5"
          checked={checked}
          disabled={busy}
          data-testid="missing-input-rewrite-confirm"
          onChange={(e) => setChecked(e.target.checked)}
        />
        <span>{t('engine.rewriteConfirm')}</span>
      </label>
      {error && <p className="mt-1 text-xs text-danger">{error}</p>}
    </Dialog>
  )
}

//: 没改的原因：键写成字面量（i18n 死键门禁按「源码里出现过这个串」判活）
const SKIP_TEXT: Record<string, string> = {
  fstring: 'engine.rewriteSkip.fstring',
  concatenated: 'engine.rewriteSkip.concatenated',
  multiline: 'engine.rewriteSkip.multiline',
  context: 'engine.rewriteSkip.context',
  rule_mismatch: 'engine.rewriteSkip.rule_mismatch',
  target_missing: 'engine.rewriteSkip.target_missing',
  unrepresentable: 'engine.rewriteSkip.unrepresentable',
  encoding: 'engine.rewriteSkip.encoding',
}

function SkippedList({ items }: { items: ScriptEditSkipped[] }) {
  const { t } = useTranslation('errors')
  return (
    <div className="mt-2" data-rewrite-skipped>
      <p className="text-xs text-ink-3">{t('engine.rewriteSkippedTitle')}</p>
      <ul className="mt-0.5 flex flex-col gap-0.5">
        {items.map((s) => (
          <li key={`${s.line}:${s.value}`} className="text-xs text-ink-2">
            {t('engine.rewriteLine', { line: s.line })}{' '}
            <span className="break-all font-mono">{s.value}</span>
            {' — '}
            {SKIP_TEXT[s.reason] ? t(SKIP_TEXT[s.reason]) : s.reason}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * 以哪一条为主：worker 说出来的那串优先；否则（「没出图」路径）先挑改指救得回来的，
 * 都救不回来才挑第一条如实说明。
 */
function primaryOf(offer: MissingInputOffer): MissingInputItem | null {
  if (offer.requested) return { path: offer.requested, absolute: offer.absolute, via: offer.via }
  return offer.others.find((o) => o.via === 'open') ?? offer.others[0] ?? null
}

function baseName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean)
  return parts.at(-1) ?? path
}
