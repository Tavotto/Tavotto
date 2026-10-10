import { useEffect, useRef, useState } from 'react'
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
import { Details, Summary } from './ui/Details'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'
import { Notice } from './ui/Notice'

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
 * 那颗按钮不是默认焦点、回车不触发（`Dialog` 打开时焦点落在内容区本身）。两步是同一个 md 对话框换正文。
 * 桌面用系统选择器；浏览器模式拿不到本机路径，让用户粘贴。
 */
export function MissingInputDialog() {
  const { t } = useTranslation('errors')
  const current = useEnvStore((s) => s.missingInput)
  const dismiss = useEnvStore((s) => s.dismissMissingInput)
  const pointAtData = useEnvStore((s) => s.pointAtData)
  const previewRewrite = useEnvStore((s) => s.previewRewrite)
  const cancelRewrite = useEnvStore((s) => s.cancelRewrite)
  const commitRewrite = useEnvStore((s) => s.commitRewrite)
  const preview = useEnvStore((s) => s.rewritePreview)
  const skipped = useEnvStore((s) => s.rewriteSkipped)
  const rewriteError = useEnvStore((s) => s.rewriteError)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [typed, setTyped] = useState('')
  // 第 2 步（确认改写）的本地状态：换一份预览就从头来
  const [checked, setChecked] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  // 常驻挂载：关的那 90ms 里载荷已经清掉，正文按最后一份画（Dialog 的常驻写法）
  const last = useRef(current)
  if (current) last.current = current
  const offer = last.current
  const lastPreview = useRef(preview)
  if (current) lastPreview.current = preview
  const step2 = lastPreview.current
  useEffect(() => {
    setError(null)
    setTyped('')
  }, [current])
  useEffect(() => {
    setChecked(false)
    setApplyError(null)
  }, [preview])
  const primary = offer ? primaryOf(offer) : null
  if (!offer || !primary) return null
  const open = !!current && !!primaryOf(current)
  const remappable = primary.via === 'open'
  // 改指救不回的几档：出口是经确认改写脚本（ADR 0110）
  const rewritable = VIAS_NEED_REWRITE.includes(primary.via)
  const pickable = remappable || rewritable
  // 只给「选择所在文件夹」：glob 模式没有「那个文件」可找；`listdir` / `iterdir` 这类只接受文件夹的探路
  // 换成一个文件，改写后重跑就是 NotADirectoryError（Codex 评 #730 P2）。两样都是后端给的事实，
  // 前端不按路径长相判；后端提交时再判一次
  const folderOnly = primary.via === 'glob' || primary.probe_kind === 'dir'
  const others = offer.others.filter((o) => o.path !== primary.path)
  const desktop = isDesktop()
  const name = baseName(primary.path)

  const submit = async (chosen: string | null, kind: 'file' | 'dir' | 'auto') => {
    if (!chosen) return // 取消选择器不是错误
    setBusy(true)
    const err = rewritable
      ? await previewRewrite(primary.path, chosen, kind)
      : await pointAtData(primary.path, chosen, kind)
    setBusy(false)
    setError(err)
  }
  const pickFile = () =>
    void pickAnyFile(t('engine.missingInputPickFileTitle', { name })).then((f) => submit(f, 'file'))
  const pickDir = () => void pickDirectory(t('engine.missingInputPickDirTitle')).then((d) => submit(d, 'dir'))
  const apply = async () => {
    setBusy(true)
    const err = await commitRewrite()
    setBusy(false)
    setApplyError(err)
  }

  // 两步流程是**一个** md 对话框换正文（2026-10-07 设计审计 §10.2：此前缺输入 360 → 确认 420 两个 Dialog 交接）：
  // 改写那一档标题旁写「1/2」「2/2」，第 2 步「返回」在 start 槽、Esc = 返回（不是关掉整件事）
  const stepMeta = (n: 1 | 2) =>
    rewritable ? (
      <span className="type-meta ml-2 align-middle" data-missing-input-step={n}>
        {t('engine.missingInputStep', { n, total: 2 })}
      </span>
    ) : null
  const later = (
    <Button variant="secondary" size="lg" disabled={busy} onClick={dismiss}>
      {t('engine.missingInputLater')}
    </Button>
  )

  if (step2) {
    return (
      <Dialog
        open={open}
        onOpenChange={(v) => {
          if (!v && !busy) dismiss()
        }}
        onEscape={busy ? undefined : cancelRewrite}
        title={
          <>
            {t('engine.rewriteTitle')}
            {stepMeta(2)}
          </>
        }
        size="md"
        busy={busy}
        anchor="missing-input-rewrite"
        footer={{
          start: (
            <Button variant="ghost" size="lg" disabled={busy} data-missing-input-back onClick={cancelRewrite}>
              {t('engine.rewriteBack')}
            </Button>
          ),
          secondary: later,
          primary: (
            <Button
              variant="primary"
              size="lg"
              loading={busy}
              disabled={!checked}
              data-testid="missing-input-rewrite-apply"
              onClick={() => void apply()}
            >
              {t('engine.rewriteApply')}
            </Button>
          ),
        }}
      >
        <RewriteConfirm preview={step2} checked={checked} setChecked={setChecked} busy={busy} error={applyError} />
      </Dialog>
    )
  }

  // 默认只有一句话 + 一个主按钮（用户 09-29：「一定要让用户一句话就能够读懂」）。只接受文件夹的那几档，
  // 主按钮本身换成「找到这个文件夹…」——仍然是唯一的主按钮；完整路径、为什么、改写脚本的说明、
  // 一并修好的其它路径、只影响读取、改选文件夹，全收进默认折叠的「详情」
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v && !busy) dismiss()
      }}
      onEscape={busy ? undefined : dismiss}
      title={
        <>
          {t('engine.missingInputTitle')}
          {stepMeta(1)}
        </>
      }
      description={t(pickable ? 'engine.missingInputSentence' : 'engine.missingInputSentenceProbe', { name })}
      size="md"
      busy={busy}
      anchor="missing-input"
      footer={{
        secondary: later,
        primary: !pickable ? undefined : desktop ? (
          folderOnly ? (
            <Button variant="primary" size="lg" loading={busy} data-testid="missing-input-pick-dir" onClick={pickDir}>
              {t('engine.missingInputPickFolder')}
            </Button>
          ) : (
            <Button variant="primary" size="lg" loading={busy} data-testid="missing-input-pick-file" onClick={pickFile}>
              {t('engine.missingInputPickFile')}
            </Button>
          )
        ) : (
          <Button
            variant="primary"
            size="lg"
            loading={busy}
            disabled={!typed.trim()}
            data-testid="missing-input-use-path"
            onClick={() => void submit(typed.trim(), 'auto')}
          >
            {rewritable ? t('engine.missingInputPreviewRewrite') : t('engine.missingInputUsePath')}
          </Button>
        ),
      }}
    >
      <div className="flex flex-col gap-3">
        {pickable && !desktop && (
          <TextInput
            className="w-full"
            value={typed}
            disabled={busy}
            placeholder={t('engine.missingInputPathPlaceholder')}
            aria-label={t('engine.missingInputPathPlaceholder')}
            data-testid="missing-input-path-input"
            onChange={(e) => setTyped(e.target.value)}
          />
        )}
        <Details className="text-sm text-ink-3" data-missing-input-details>
          <Summary className="py-0.5 text-ink-2 hover:text-ink">{t('engine.missingInputDetails')}</Summary>
          <div className="mt-1.5 flex flex-col gap-2 pl-4">
            {/* 路径是用户自己写的，不翻译 */}
            <p className="break-all font-mono text-ink" data-missing-input-path>
              {primary.path}
            </p>
            <p className="text-ink-2">
              {remappable
                ? t(primary.absolute ? 'engine.missingInputWhyAbsolute' : 'engine.missingInputWhyRelative')
                : primary.via === 'native'
                  ? t('engine.missingInputNative')
                  : t('engine.missingInputProbe')}
            </p>
            {rewritable && (
              <p className="text-ink-2" data-missing-input-rewrite-hint>
                {t('engine.missingInputRewriteHint')}
              </p>
            )}
            {others.length > 0 && (
              <div>
                <p>{t('engine.missingInputOthers')}</p>
                <ul className="mt-0.5 flex flex-col gap-0.5" data-missing-input-others>
                  {others.map((o) => (
                    <li key={o.path} className="break-all font-mono text-ink-2">
                      {o.path}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {remappable && <p>{t('engine.missingInputReadOnly')}</p>}
            {/* 选中那个文件就推得出规则；数据整批换了文件夹、只想指一次时才用得上这个。只接受文件的探路（`isfile`
                这类，`probe_kind === 'file'`）不给：选成一个同名文件夹，改写后 `isfile()` 照样 False（后端也拒，Codex 评 #730 P2） */}
            {pickable && desktop && !folderOnly && primary.probe_kind !== 'file' && (
              <div>
                <Button variant="secondary" size="sm" disabled={busy} data-testid="missing-input-pick-dir" onClick={pickDir}>
                  {t('engine.missingInputPickDir')}
                </Button>
              </div>
            )}
          </div>
        </Details>
        {/* 回应放在页脚上方：没改成的逐条原因、错误 */}
        {skipped.length > 0 && <SkippedList items={skipped} />}
        {(error ?? rewriteError) && <Notice tone="danger">{error ?? rewriteError}</Notice>}
      </div>
    </Dialog>
  )
}

/**
 * 确认页（ADR 0110 §八）：醒目地说「这会修改你的脚本文件」+ 完整路径；逐行 diff 只渲染后端给的行；
 * 没改的逐条原因；两处备份位置；git 状态与校验清单提示。必须勾选「我已查看」才能点「修改脚本」。
 * 只是正文——页脚与对话框外壳是第 1 步那一个（同一个 Dialog 换正文）。
 */
function RewriteConfirm({
  preview,
  checked,
  setChecked,
  busy,
  error,
}: {
  preview: ScriptEditPreview
  checked: boolean
  setChecked: (v: boolean) => void
  busy: boolean
  error: string | null
}) {
  const { t } = useTranslation('errors')
  return (
    <div className="flex flex-col gap-3">
      <Notice tone="warn" title={t('engine.rewriteWarning')} data-rewrite-warning>
        {/* 用户自己的路径，不翻译 */}
        <span className="break-all font-mono text-sm">{preview.script_abs}</span>
      </Notice>
      <div className="flex flex-col gap-1.5">
        <p className="text-ink-2">{t('engine.rewriteCount', { n: preview.edits.length })}</p>
        <ol className="flex flex-col gap-1" data-rewrite-rows>
          {preview.rows.map((r) => (
            <li key={r.line} className="rounded-md bg-surface-2 px-2 py-1.5 font-mono text-sm">
              <span className="text-xs text-ink-3">{t('engine.rewriteLine', { line: r.line })}</span>
              <p className="break-all text-danger-content">− {r.before}</p>
              <p className="break-all text-ok-content">+ {r.after}</p>
            </li>
          ))}
        </ol>
      </div>
      {preview.skipped.length > 0 && <SkippedList items={preview.skipped} />}
      <div className="flex flex-col gap-0.5 text-sm text-ink-3">
        <p>{t('engine.rewriteBackups')}</p>
        <ul className="break-all font-mono">
          <li>{preview.backups.project}</li>
          <li>{preview.backups.mirror}</li>
        </ul>
        {preview.git && (
          <p className="mt-1">
            {preview.git.tracked
              ? preview.git.dirty
                ? t('engine.rewriteGitDirty')
                : t('engine.rewriteGitClean')
              : t('engine.rewriteGitUntracked')}
          </p>
        )}
      </div>
      {preview.checksums.length > 0 && (
        <Notice tone="warn">{t('engine.rewriteChecksums', { files: preview.checksums.join(', ') })}</Notice>
      )}
      <label className="flex items-start gap-2 text-ink">
        <Checkbox
          className="mt-0.5"
          checked={checked}
          disabled={busy}
          data-testid="missing-input-rewrite-confirm"
          onChange={(e) => setChecked(e.target.checked)}
        />
        <span>{t('engine.rewriteConfirm')}</span>
      </label>
      {error && <Notice tone="danger">{error}</Notice>}
    </div>
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
    <div className="text-sm" data-rewrite-skipped>
      <p className="text-ink-3">{t('engine.rewriteSkippedTitle')}</p>
      <ul className="mt-0.5 flex flex-col gap-0.5">
        {items.map((s) => (
          <li key={`${s.line}:${s.value}`} className="text-ink-2">
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
  if (offer.requested)
    return { path: offer.requested, absolute: offer.absolute, via: offer.via, probe_kind: offer.probe_kind }
  return offer.others.find((o) => o.via === 'open') ?? offer.others[0] ?? null
}

function baseName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean)
  return parts.at(-1) ?? path
}
