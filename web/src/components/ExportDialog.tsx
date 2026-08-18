import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Archive, Check, ChevronRight, Download, ExternalLink, MoreHorizontal, TriangleAlert } from 'lucide-react'
import { createPackage, exportFigure, type ExportResponse } from '@/lib/api'
import { readExportDefaults } from '@/lib/exportDefaults'
import { toExportObjects } from '@/lib/exportPayload'
import { buildProofPayload, issueText, runPreflight, type PreflightIssue } from '@/lib/preflight'
import { msg } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { apiUrl } from '@/lib/session'
import { cn } from '@/lib/utils'
import { isDesktop, revealExportedFile } from '@/lib/desktop'
import { revealObjects } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useProjectStore } from '@/store/projectStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject, TextObject } from '@/types/document'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Menu, MenuItem } from './ui/Menu'
import { Row } from './ui/Field'
import { TextInput } from './ui/Input'
import { Select } from './ui/Select'
import { Toggle } from './ui/Toggle'

const DPI_VALUES = ['300', '600', '900', '1200'] as const

/**
 * 导出预设：dpi + 格式的组合，并按期刊常见版式校对页宽（只提示，不擅自改页面）。
 * 文案按 id 查 `dialogs:export.presets.<id>`，这里只留数值。
 */
const PRESETS: { id: string; dpi: string; formats: string[]; pageW?: number }[] = [
  { id: 'single', dpi: '600', formats: ['pdf'], pageW: 85 },
  { id: 'double', dpi: '600', formats: ['pdf'], pageW: 150 },
  { id: 'full', dpi: '600', formats: ['pdf', 'png'], pageW: 180 },
  { id: 'screen', dpi: '300', formats: ['png'] },
]

export function ExportDialog() {
  const { t } = useTranslation(['dialogs', 'common'])
  const open = useUiStore((s) => s.exportOpen)
  const setOpen = useUiStore((s) => s.setExportOpen)
  const doc = useDocumentStore((s) => s.doc)
  const byKey = useRenderStore((s) => s.byKey)
  const latest = useRenderStore((s) => s.latest)
  const assets = useAssetStore((s) => s.byId)

  // 初始值来自「设置 → 导出默认值」；对话框内的改动只影响本次
  const [formats, setFormats] = useState<string[]>(() => readExportDefaults().formats)
  const [dpi, setDpi] = useState(() => readExportDefaults().dpi)
  const [stem, setStem] = useState(doc.name)
  const [preset, setPreset] = useState<string | null>(null)
  const [withProof, setWithProof] = useState(() => readExportDefaults().withProof)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ExportResponse | null>(null)
  const [packResult, setPackResult] = useState<{ name: string; url: string; assets: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setStem(doc.name)
    setResult(null)
    setPackResult(null)
    setError(null)
  }, [open, doc.name])

  const visible = doc.objects.filter((o) => !o.hidden)
  const panels = visible.filter((o): o is PanelObject => o.type === 'panel')
  const texts = visible.filter((o): o is TextObject => o.type === 'text')
  const marks = visible.filter((o) => o.type === 'arrow' || o.type === 'shape')

  const dpiOptions = DPI_VALUES.map((v) => ({
    value: v,
    label: `${v} dpi`,
    hint: t(`dialogs:export.dpiHint.${v}`),
  }))

  const pxW = Math.round((doc.page.w / 25.4) * Number(dpi))
  const pxH = Math.round((doc.page.h / 25.4) * Number(dpi))

  const issues = useMemo(
    () => (open ? runPreflight(doc, assets, { byKey, latest }) : []),
    [open, doc, assets, byKey, latest],
  )
  const errors = issues.filter((i) => i.severity === 'error')

  const activePreset = PRESETS.find((p) => p.id === preset)
  const pageMismatch =
    activePreset?.pageW != null && Math.abs(doc.page.w - activePreset.pageW) > 0.5

  const applyPreset = (p: (typeof PRESETS)[number]) => {
    setPreset(p.id)
    setDpi(p.dpi)
    setFormats([...p.formats])
  }

  const toggleFormat = (f: string) => {
    setPreset(null)
    setFormats((prev) => (prev.includes(f) ? prev.filter((v) => v !== f) : [...prev, f]))
  }

  const locate = (ids: string[]) => {
    setOpen(false)
    revealObjects(ids)
  }

  const run = async () => {
    if (!formats.length) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const settings = { dpi: Number(dpi), formats, stem: stem.trim() || 'composed' }
      const res = await exportFigure({
        page_w_mm: doc.page.w,
        page_h_mm: doc.page.h,
        dpi: settings.dpi,
        formats,
        stem: settings.stem,
        objects: toExportObjects(doc.objects),
        proof: withProof ? buildProofPayload(doc, assets, issues, settings) : undefined,
      })
      setResult(res)
      useUiStore
        .getState()
        .setStatus(
          msg('export.exported', { files: listJoin(res.files.map((f) => f.name)) }, 'dialogs'),
        )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const pack = async () => {
    setBusy(true)
    setError(null)
    setPackResult(null)
    try {
      // 项目包带上整个项目文档（schema 3，含全部画布）
      const res = await createPackage(
        stem.trim() || doc.name,
        useDocumentStore.getState().buildProject(),
        { dpi: Number(dpi), formats },
      )
      setPackResult(res)
      useUiStore
        .getState()
        .setStatus(msg('export.packaged', { name: res.name, count: res.assets }, 'dialogs'))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={t('dialogs:export.title')}
      description={t('dialogs:export.summary', {
        w: doc.page.w,
        h: doc.page.h,
        panels: panels.length,
        texts: texts.length,
        marks: marks.length,
      })}
      width={500}
      busy={busy}
      footer={
        <>
          <Menu
            width={200}
            align="start"
            trigger={
              <Button size="icon" disabled={busy} aria-label={t('dialogs:export.moreOptions')}>
                <MoreHorizontal size={14} className="text-ink-2" />
              </Button>
            }
          >
            <MenuItem disabled={!doc.objects.length} onSelect={() => void pack()}>
              <span className="flex items-center gap-2">
                <Archive size={13} className="text-ink-3" />
                {t('dialogs:export.package')}
              </span>
            </MenuItem>
          </Menu>
          <span className="flex-1" />
          <Button variant="outline" size="md" disabled={busy} onClick={() => setOpen(false)}>
            {t('common:actions.close')}
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={!formats.length}
            loading={busy}
            loadingLabel={t('dialogs:export.composing')}
            title={errors.length ? t('dialogs:export.blockingTitle') : undefined}
            onClick={run}
          >
            <Download size={14} />
            {t('dialogs:export.start')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2.5">
        <Row label={t('dialogs:export.presetLabel')} labelWidth={52}>
          <div className="flex min-w-0 flex-1 flex-wrap gap-1">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                onClick={() => applyPreset(p)}
                title={t(`dialogs:export.presets.${p.id}.hint`)}
                className={cn(
                  'h-6 rounded-sm border px-2 text-xs transition-colors',
                  preset === p.id
                    ? 'border-accent bg-accent-subtle text-accent'
                    : 'border-border bg-surface text-ink-2 hover:border-border-strong',
                )}
              >
                {t(`dialogs:export.presets.${p.id}.label`)}
              </button>
            ))}
          </div>
        </Row>
        {pageMismatch && activePreset?.pageW != null && (
          <p className="pl-[60px] text-xs leading-relaxed text-ink-3">
            {t('dialogs:export.pageMismatch', {
              current: doc.page.w,
              preset: t(`dialogs:export.presets.${activePreset.id}.label`),
              want: activePreset.pageW,
            })}
          </p>
        )}

        <PreflightBlock issues={issues} errors={errors.length} onLocate={locate} />

        <Row label={t('dialogs:export.formatLabel')} labelWidth={52}>
          <FormatToggle
            checked={formats.includes('pdf')}
            onClick={() => toggleFormat('pdf')}
            title="PDF"
            hint={t('dialogs:export.pdfHint')}
          />
          <FormatToggle
            checked={formats.includes('png')}
            onClick={() => toggleFormat('png')}
            title="PNG"
            hint={t('dialogs:export.pngHint')}
          />
        </Row>

        <Row label={t('dialogs:export.dpiLabel')} labelWidth={52}>
          <Select
            value={dpi}
            onChange={(v) => {
              setPreset(null)
              setDpi(v)
            }}
            options={dpiOptions}
            disabled={!formats.includes('png')}
            ariaLabel={t('dialogs:export.dpiSelectLabel')}
            className="w-28"
          />
          <span className="shrink-0 font-mono text-xs text-ink-3">
            {formats.includes('png') ? `${pxW} × ${pxH} px` : t('dialogs:export.pdfDpiIrrelevant')}
          </span>
        </Row>

        <Row label={t('dialogs:export.stemLabel')} labelWidth={52}>
          <TextInput value={stem} onChange={(e) => setStem(e.target.value)} placeholder="composed" />
          <span className="shrink-0 font-mono text-xs text-ink-3">
            {t('dialogs:export.timestampSuffix')}
          </span>
        </Row>

        <Row label={t('dialogs:export.proofLabel')} labelWidth={52}>
          <label
            className="flex items-center gap-1.5 text-xs text-ink-2"
            title={t('dialogs:export.proofTitle')}
          >
            <Toggle checked={withProof} onChange={setWithProof} />
            {t('dialogs:export.proofToggle')}
          </label>
        </Row>

        {error && (
          <p className="text-xs text-danger">
            {t('dialogs:export.operationFailed', { error })}
          </p>
        )}

        {(result || packResult) && (
          <div className="flex flex-col gap-1 rounded-sm border border-border bg-surface-2 p-2">
            <p className="break-all text-xs text-ink-3">
              {t('dialogs:export.savedTo', {
                dir:
                  result?.export_dir ??
                  useProjectStore.getState().project?.export_dir ??
                  'exports/',
              })}
            </p>
            {[...(result?.files ?? []), ...(packResult ? [packResult] : [])].map((f) =>
              isDesktop() ? (
                // 桌面里不开浏览器式文件标签页：在系统文件管理器中显示。
                // reveal 失败绝不静默——把完整路径告诉用户（「点了没反应、
                // 也不知道输出到哪」比失败本身更伤）
                <button
                  key={f.name}
                  type="button"
                  onClick={() => {
                    const dir = result?.export_dir ?? useProjectStore.getState().project?.export_dir
                    if (!dir) return
                    void revealExportedFile(dir, f.name).then((ok) => {
                      if (!ok) {
                        setError(
                          t('dialogs:export.revealFailed', { path: `${dir}/${f.name}` }),
                        )
                      }
                    })
                  }}
                  className="flex items-center gap-1.5 font-mono text-xs text-accent hover:underline"
                >
                  <ExternalLink size={12} />
                  {f.name}
                </button>
              ) : (
                // 后端回的是裸路径 /exports/<name>，必须过 apiUrl() 补 pj：`<a>` 加不了
                // 请求头，不带 pj 时后端落到**默认项目**的导出目录——非默认项目的标签页
                // 点下载不是 404 就是下到别的图库的同名文件
                <a
                  key={f.name}
                  href={apiUrl(f.url)}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 font-mono text-xs text-accent hover:underline"
                >
                  <ExternalLink size={12} />
                  {f.name}
                </a>
              ),
            )}
            {/* 引擎重渲染的警告：图已经出来了，但可能与画布不完全一致
                （元素不存在 = 脚本改过了）。不吞——用户投出去之前得知道 */}
            {!!result?.warnings?.length && (
              <div className="mt-1 flex flex-col gap-0.5 border-t border-border pt-1">
                <p className="text-xs text-ink-2">{t('dialogs:export.warningsIntro')}</p>
                {result.warnings.map((w) => (
                  <p key={w} className="break-all text-xs text-ink-3">
                    {w}
                  </p>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </Dialog>
  )
}

/** 预检：先一句摘要，问题明细按需展开；有问题时默认展开错误 */
function PreflightBlock({
  issues,
  errors,
  onLocate,
}: {
  issues: PreflightIssue[]
  errors: number
  onLocate: (ids: string[]) => void
}) {
  const { t } = useTranslation('dialogs')
  const [expanded, setExpanded] = useState(false)
  if (issues.length === 0) {
    return (
      <p className="flex items-center gap-1.5 rounded-sm bg-surface-2 px-2 py-1.5 text-xs text-ink-2">
        <Check size={12} className="shrink-0 text-accent" />
        {t('export.preflightOk')}
      </p>
    )
  }
  return (
    <div className="rounded-sm bg-surface-2 px-2 py-1.5">
      <button
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-1.5 rounded-sm text-left text-xs outline-none focus-visible:focus-ring"
      >
        <TriangleAlert size={12} className={errors ? 'shrink-0 text-danger' : 'shrink-0 text-ink-3'} />
        <span className={cn('min-w-0 flex-1', errors ? 'text-danger' : 'text-ink-2')}>
          {errors
            ? t('export.preflightSummaryBlocking', { count: issues.length, errors })
            : t('export.preflightSummary', { count: issues.length })}
        </span>
        <ChevronRight
          size={11}
          className={cn('shrink-0 text-ink-3 transition-transform', expanded && 'rotate-90')}
        />
      </button>
      {expanded && (
        <ul className="mt-1.5 flex flex-col gap-1.5 border-t border-border pt-1.5">
          {issues.map((it) => (
            <Warning key={it.id} ids={it.objectIds} error={it.severity === 'error'} onLocate={onLocate}>
              {issueText(it)}
              {t('export.issueCount', { count: it.objectIds.length })}
            </Warning>
          ))}
        </ul>
      )}
    </div>
  )
}

function FormatToggle({
  checked,
  onClick,
  title,
  hint,
}: {
  checked: boolean
  onClick: () => void
  title: string
  hint: string
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={checked}
      className={cn(
        'flex flex-1 items-center gap-1.5 rounded-sm border px-2 py-1 text-left transition-colors',
        checked
          ? 'border-accent bg-accent-subtle'
          : 'border-border bg-surface hover:border-border-strong',
      )}
    >
      <span
        className={cn(
          'flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[3px] border',
          checked ? 'border-accent bg-accent text-white' : 'border-border-strong',
        )}
      >
        {checked && <Check size={10} strokeWidth={3} />}
      </span>
      <span className="min-w-0">
        <span className={cn('block text-xs', checked ? 'text-accent' : 'text-ink')}>{title}</span>
        <span className="block text-xs text-ink-3">{hint}</span>
      </span>
    </button>
  )
}

/** 预检警告点得动：点一下就关掉弹窗、选中并把问题对象挪进视野 */
function Warning({
  ids,
  error = false,
  onLocate,
  children,
}: {
  ids: string[]
  error?: boolean
  onLocate: (ids: string[]) => void
  children: ReactNode
}) {
  const { t } = useTranslation('dialogs')
  return (
    <li>
      <button
        onClick={() => onLocate(ids)}
        className="group flex w-full items-start gap-1.5 text-left text-xs leading-relaxed text-ink-2 hover:text-ink"
      >
        <TriangleAlert
          size={12}
          className={cn('mt-px shrink-0', error ? 'text-danger' : 'text-ink-3')}
        />
        <span className="min-w-0 flex-1">{children}</span>
        <span className="shrink-0 text-ink-3 group-hover:text-accent">{t('export.locate')}</span>
      </button>
    </li>
  )
}
