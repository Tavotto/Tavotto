import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive,
  Check,
  ChevronRight,
  Download,
  ExternalLink,
  Lightbulb,
  MoreHorizontal,
  ShieldQuestion,
  TriangleAlert,
} from 'lucide-react'
import { backendErrorText, createPackage, exportFigure, type ExportResponse } from '@/lib/api'
import { msg, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { readExportDefaults, writeExportDefaults } from '@/lib/exportDefaults'
import { toExportObjects } from '@/lib/exportPayload'
import {
  buildProofPayload,
  issueText,
  runPreflight,
  summarize,
  type PreflightIssue,
} from '@/lib/preflight'
import { columnOf, type JournalOverride, type PublicationProfile, type Severity } from '@/lib/profile'
import { profileName, profileTechnicalDetail } from '@/lib/profileText'
import { bindingFor, resolveDocumentSpec, type SpecCatalogEntry } from '@/lib/specBinding'
import { apiUrl } from '@/lib/session'
import { boundedCount, captureTelemetry } from '@/lib/telemetry'
import { cn } from '@/lib/utils'
import { isDesktop, revealExportedFile } from '@/lib/desktop'
import { revealObjects } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useProfileStore } from '@/store/profileStore'
import { useProjectStore } from '@/store/projectStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject, TextObject } from '@/types/document'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Menu, MenuItem } from './ui/Menu'
import { Row } from './ui/Field'
import { NumberField, TextInput } from './ui/Input'
import { Select } from './ui/Select'
import { Toggle } from './ui/Toggle'

/** 本对话框的文案都在 `dialogs:export.*` 下 */
const ex = (key: string, values?: Record<string, unknown>) =>
  translate(`export.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const DPI_VALUES = ['300', '600', '900', '1200'] as const

/**
 * 导出预设：**页宽全部来自 profile**（以前是写死的 85/150/180mm——规范一改，
 * 那三个数字就开始撒谎）。预设只设 dpi + 格式并校对页宽，绝不擅自改页面。
 */
function buildPresets(profile: PublicationProfile) {
  const dpi = String(profile.preferred_formats.export_dpi_default)
  const vector = profile.preferred_formats.vector.includes('pdf') ? 'pdf' : 'pdf'
  return [
    {
      id: 'single',
      label: ex('presets.single.label'),
      dpi,
      formats: [vector],
      pageW: profile.widths_mm.single,
      hint: ex('presets.single.hint', { mm: profile.widths_mm.single }),
    },
    {
      id: 'double',
      label: ex('presets.double.label'),
      dpi,
      formats: [vector],
      pageW: profile.widths_mm.double,
      hint: ex('presets.double.hint', { mm: profile.widths_mm.double }),
    },
    {
      id: 'both',
      label: ex('presets.both.label'),
      dpi,
      formats: ['pdf', 'png'],
      pageW: null as number | null,
      hint: ex('presets.both.hint', { dpi }),
    },
    {
      id: 'screen',
      label: ex('presets.screen.label'),
      dpi: String(profile.min_raster_dpi),
      formats: ['png'],
      pageW: null as number | null,
      hint: ex('presets.screen.hint', { dpi: profile.min_raster_dpi }),
    },
  ]
}

/** 等级标签按 severity 查 `dialogs:export.severity.<等级>` */
const severityLabel = (s: Severity): string =>
  ex(`severity.${s === 'not_verifiable' ? 'notVerifiable' : s}`)

const SEVERITY_ICON: Record<Severity, typeof TriangleAlert> = {
  error: TriangleAlert,
  warn: TriangleAlert,
  not_verifiable: ShieldQuestion,
  suggestion: Lightbulb,
}

export function ExportDialog() {
  // 订阅语言变化：预设、体检数字这些都是模块级 ex() 拼出来的，
  // 没有这一句切语言后这个对话框会停在旧语言上
  useTranslation(['dialogs', 'common'])
  const open = useUiStore((s) => s.exportOpen)
  const setOpen = useUiStore((s) => s.setExportOpen)
  const doc = useDocumentStore((s) => s.doc)
  const commit = useDocumentStore((s) => s.commit)
  const byKey = useRenderStore((s) => s.byKey)
  const latest = useRenderStore((s) => s.latest)
  const assets = useAssetStore((s) => s.byId)

  // 初始值来自「设置 → 导出默认值」；对话框内的改动只影响本次
  const [formats, setFormats] = useState<string[]>(() => readExportDefaults().formats)
  const [dpi, setDpi] = useState(() => readExportDefaults().dpi)
  const [stem, setStem] = useState(doc.name)
  const [withProof, setWithProof] = useState(() => readExportDefaults().withProof)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ExportResponse | null>(null)
  const [packResult, setPackResult] = useState<{ name: string; url: string; assets: number } | null>(
    null,
  )
  const [error, setError] = useState<string | null>(null)
  /**
   * 用户对本次导出的显式确认：阻断项与「无法核验」项都要点过才放行。
   * **不做成记住的偏好**——每次导出都得重新面对一次当前这批问题。
   */
  const [confirmed, setConfirmed] = useState(false)

  /* ------------------------------ 出版规范 ------------------------------- */
  // 清单来自 profileStore（内置 + 用户自建）。后端不在时它退回内置那两条，
  // 所以浏览器演练场里这一段照样能用。
  const specRecords = useProfileStore((s) => s.specs)
  const catalog = useMemo<SpecCatalogEntry[]>(
    () =>
      specRecords.map((r) => ({
        id: r.id,
        display_name: profileName(r),
        name_key: r.name_key || undefined,
        version: r.version,
        built_in: r.built_in,
        data: r.data,
      })),
    [specRecords],
  )
  // 规范绑定优先看文档自己的；旧文档没有该字段时用「上次用过的」，再退默认
  const docProfileId = doc.profile?.id
  const [profileId, setProfileId] = useState(
    () => docProfileId ?? readExportDefaults().profileId,
  )
  /**
   * **实际生效的规范只解析一次**（ADR 0029）：有快照就按快照，没有才按全局
   * 现值。导出面板不许自己再挑一遍——那正是「预检说合规、导出按另一套规矩」
   * 的来源。
   */
  const resolved = useMemo(
    () => resolveDocumentSpec(doc.profile ?? { id: profileId }, catalog),
    [doc.profile, profileId, catalog],
  )
  const profile = resolved.profile
  const column = columnOf(profile, doc.page.w)
  // 期刊自定义宽度：文档里存的是覆盖值，缺省显示 profile 的双栏宽
  const [journalWidth, setJournalWidth] = useState<number | null>(
    () => (doc.profile?.journal as JournalOverride | undefined)?.widths_mm?.double ?? null,
  )

  useEffect(() => {
    if (!open) return
    // 规范清单在这里拉一次：用户可能刚在设置里建了一条自定义规范
    void useProfileStore.getState().load()
    setStem(doc.name)
    setResult(null)
    setPackResult(null)
    setError(null)
    setConfirmed(false)
    setProfileId(doc.profile?.id ?? readExportDefaults().profileId)
    setJournalWidth((doc.profile?.journal as JournalOverride | undefined)?.widths_mm?.double ?? null)
  }, [open, doc.name, doc.profile])

  const visible = doc.objects.filter((o) => !o.hidden)
  const panels = visible.filter((o): o is PanelObject => o.type === 'panel')
  const texts = visible.filter((o): o is TextObject => o.type === 'text')
  const marks = visible.filter((o) => o.type === 'arrow' || o.type === 'shape')

  const pxW = Math.round((doc.page.w / 25.4) * Number(dpi))
  const pxH = Math.round((doc.page.h / 25.4) * Number(dpi))

  const issues = useMemo(
    () => (open ? runPreflight(doc, assets, { byKey, latest }, profile) : []),
    [open, doc, assets, byKey, latest, profile],
  )
  const sum = useMemo(() => summarize(issues), [issues])
  /** 最小的最终有效字号（体检里最有信息量的那个数字，直接摆在面板上） */
  const minEffectivePt = useMemo(() => {
    let min: number | null = null
    for (const i of issues) {
      const v = i.detail?.effective_pt
      if (typeof v === 'number' && (min == null || v < min)) min = v
    }
    return min
  }, [issues])

  /** 需要用户点头才放行的东西：阻断项 + 无法核验项 */
  const needsConfirm = sum.errors.length > 0 || sum.notVerifiable.length > 0

  /**
   * 匿名用量统计：**预检真的算完之后**记一次，每次打开导出对话框一条。
   *
   * 这一条刻意记在前端而不是后端：画布这一侧的预检求值器就是
   * `lib/preflight.ts`（两个求值器的分工见 CLAUDE.md），Flask 没有对应端点。
   * 发出去的只有**四个计数 + 一个布尔**——检查项的文案、字体名、对象 id、
   * 文件名一个都不发（白名单里也没有这些属性）。
   */
  useEffect(() => {
    if (!open) return
    captureTelemetry('preflight_completed', {
      errors: boundedCount(sum.counts.error),
      warnings: boundedCount(sum.counts.warn),
      not_verifiable: boundedCount(sum.counts.not_verifiable),
      suggestions: boundedCount(sum.counts.suggestion),
      passed: sum.counts.error === 0 && sum.counts.warn === 0,
    })
    // 依赖里**只有 open**：把 sum 放进去的话，用户在对话框里换一次期刊规范
    // 就会再发一条，而那不是一次新的体检行为
  }, [open])                                    // eslint-disable-line
  // 勾了确认框就**必须**留档。确认框上写着「这次确认会记录在留档里」，而
  // 用户可能早就把留档关掉了（这是个记住的偏好）——那样承诺的记录一份都
  // 不会产生，导出对话框在骗人。所以确认一旦成立，留档不再是可选项。
  const proofRequired = needsConfirm && confirmed
  const proofOn = withProof || proofRequired
  const blocked = needsConfirm && !confirmed

  /**
   * 选规范 = 写一条**带快照的绑定**进文档（可撤销、正确 dirty）。
   *
   * 快照让「项目结果稳定」成立：以后全局那套规范改了，这张图的结论一个字
   * 不变；界面提示有新版，由用户点「同步」明确确认（那一步同样进历史）。
   */
  const applyProfile = (id: string, width: number | null) => {
    setProfileId(id)
    writeExportDefaults({ profileId: id })
    const entry = catalog.find((e) => e.id === id)
    if (!entry) return
    const nextJournal =
      width != null && Number.isFinite(width) && width > 0
        ? { widths_mm: { double: width } }
        : undefined
    commit(msg('history.setPublicationProfile', undefined, 'workspace'), (d) => {
      d.profile = bindingFor(entry, { journal: nextJournal })
    })
  }

  /** 把文档里的快照同步到全局那一版。**用户明确点过才发生**，并进文档历史。 */
  const syncProfile = () => {
    const entry = catalog.find((e) => e.id === (doc.profile?.id ?? profileId))
    if (!entry) return
    commit(msg('history.syncPublicationProfile', undefined, 'workspace'), (d) => {
      d.profile = bindingFor(entry, { journal: doc.profile?.journal })
    })
  }

  const presets = useMemo(() => buildPresets(profile), [profile])
  const [preset, setPreset] = useState<string | null>(null)
  const activePreset = presets.find((p) => p.id === preset)
  const pageMismatch =
    activePreset?.pageW != null && Math.abs(doc.page.w - activePreset.pageW) > profile.widths_mm.tolerance_mm

  const applyPreset = (p: (typeof presets)[number]) => {
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
    if (!formats.length || blocked) return
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
        // proof 里必须带 profile 身份与全部检查结果，含 not_verifiable
        proof: proofOn
          ? buildProofPayload(doc, assets, issues, settings, profile, {
              forced: sum.errors.length > 0 && confirmed,
              acknowledged: needsConfirm
                ? [...sum.errors, ...sum.notVerifiable].map((i) => i.id)
                : [],
            })
          : undefined,
      })
      setResult(res)
      useUiStore
        .getState()
        .setStatus(
          msg('export.exported', { files: listJoin(res.files.map((f) => f.name)) }, 'dialogs'),
        )
    } catch (err) {
      setError(backendErrorText(err))
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
      setError(backendErrorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={ex('title')}
      description={ex('summary', {
        w: doc.page.w,
        h: doc.page.h,
        panels: panels.length,
        texts: texts.length,
        marks: marks.length,
      })}
      width={520}
      busy={busy}
      footer={
        <>
          <Menu
            width={200}
            align="start"
            trigger={
              <Button size="icon" disabled={busy} aria-label={ex('moreOptions')}>
                <MoreHorizontal size={14} className="text-ink-2" />
              </Button>
            }
          >
            <MenuItem disabled={!doc.objects.length} onSelect={() => void pack()}>
              <span className="flex items-center gap-2">
                <Archive size={13} className="text-ink-3" />
                {ex('package')}
              </span>
            </MenuItem>
          </Menu>
          <span className="flex-1" />
          <Button variant="outline" size="md" disabled={busy} onClick={() => setOpen(false)}>
            {translate('actions.close')}
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={!formats.length || blocked}
            loading={busy}
            loadingLabel={ex('composing')}
            title={blocked ? ex('blockedTitle') : undefined}
            onClick={run}
          >
            <Download size={14} />
            {ex('start')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2.5">
        <Row label={ex('presetLabel')} labelWidth={52}>
          <div className="flex min-w-0 flex-1 flex-wrap gap-1">
            {presets.map((p) => (
              <button
                key={p.id}
                onClick={() => applyPreset(p)}
                title={p.hint}
                className={cn(
                  'h-6 rounded-sm border px-2 text-xs transition-colors',
                  preset === p.id
                    ? 'border-accent bg-accent-subtle text-accent'
                    : 'border-border bg-surface text-ink-2 hover:border-border-strong',
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
        </Row>
        {pageMismatch && activePreset?.pageW != null && (
          <p className="pl-[60px] text-xs leading-relaxed text-ink-3">
            {ex('pageMismatch', {
              current: doc.page.w,
              preset: activePreset.label,
              want: activePreset.pageW,
            })}
          </p>
        )}

        <Row label={ex('profileLabel')} labelWidth={52}>
          <Select
            value={doc.profile?.id ?? profileId}
            onChange={(v) => applyProfile(v, journalWidth)}
            options={catalog.map((p) => ({ value: p.id, label: p.display_name }))}
            ariaLabel={ex('profileAria')}
            className="w-44"
          />
          {/* 内部 id 与版本号**不进默认视图**：对用户没有意义，摆出来只会让人
              以为那是要记住的东西。它们留在这一行的 title 里（技术详情）。 */}
          <span
            className="shrink-0 text-xs text-ink-3"
            title={profileTechnicalDetail({ id: profile.profile_id, version: profile.version })}
          >
            {resolved.source === 'snapshot' ? ex('profilePinned') : ''}
          </span>
        </Row>

        {resolved.updateAvailable && (
          <p className="flex items-center gap-2 pl-[60px] text-xs leading-relaxed text-ink-2">
            {ex('profileUpdateAvailable')}
            <Button variant="outline" size="sm" onClick={syncProfile}>
              {ex('profileSync')}
            </Button>
          </p>
        )}
        {resolved.globalMissing && (
          <p className="pl-[60px] text-xs leading-relaxed text-ink-3">
            {resolved.source === 'snapshot' ? ex('profileMissingPinned') : ex('profileMissing')}
          </p>
        )}

        <ProfileFacts
          pageW={doc.page.w}
          pageH={doc.page.h}
          column={column}
          singleMm={profile.widths_mm.single}
          doubleMm={profile.widths_mm.double}
          minPt={profile.min_effective_font_size_pt}
          minEffectivePt={minEffectivePt}
          minDpi={profile.min_raster_dpi}
          dpi={Number(dpi)}
          vector={profile.preferred_formats.vector}
          raster={profile.preferred_formats.raster}
        />

        {profile.widths_mm.allow_custom && (
          <Row label={ex('journalWidthLabel')} labelWidth={52}>
            <NumberField
              value={journalWidth ?? profile.widths_mm.double}
              min={20}
              max={500}
              step={0.5}
              precision={2}
              suffix="mm"
              ariaLabel={ex('journalWidthLabel')}
              className="w-24"
              onChange={(v) => {
                setJournalWidth(v)
                applyProfile(profileId, v)
              }}
            />
            <span className="min-w-0 flex-1 text-xs text-ink-3">
              {ex('journalWidthHint')}
              {journalWidth != null && (
                <button
                  className="ml-2 text-accent hover:underline"
                  onClick={() => {
                    setJournalWidth(null)
                    applyProfile(profileId, null)
                  }}
                >
                  {ex('journalWidthReset')}
                </button>
              )}
            </span>
          </Row>
        )}

        <PreflightBlock issues={issues} onLocate={locate} />

        {needsConfirm && (
          <label className="flex items-start gap-1.5 rounded-sm border border-danger/40 bg-surface-2 px-2 py-1.5 text-xs text-ink-2">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
              className="mt-0.5 shrink-0"
            />
            {/* 三种情况各是一句完整的话，不拼字符串：中文能靠「与」串起来，
                英文的从句位置不一样，拼出来的句子读着就是机翻 */}
            <span className="min-w-0 flex-1">
              {sum.errors.length > 0 && sum.notVerifiable.length > 0
                ? ex('confirmBoth', {
                    errors: sum.errors.length,
                    notVerifiable: sum.notVerifiable.length,
                  })
                : sum.errors.length > 0
                  ? ex('confirmErrors', { errors: sum.errors.length })
                  : ex('confirmNotVerifiable', { notVerifiable: sum.notVerifiable.length })}
            </span>
          </label>
        )}

        <Row label={ex('formatLabel')} labelWidth={52}>
          <FormatToggle
            checked={formats.includes('pdf')}
            onClick={() => toggleFormat('pdf')}
            title="PDF"
            hint={ex('pdfHint')}
          />
          <FormatToggle
            checked={formats.includes('png')}
            onClick={() => toggleFormat('png')}
            title="PNG"
            hint={ex('pngHint')}
          />
        </Row>
        {profile.preferred_formats.vector.includes('svg') && (
          <p className="pl-[60px] text-xs leading-relaxed text-ink-3">{ex('svgNote')}</p>
        )}

        <Row label={ex('dpiLabel')} labelWidth={52}>
          <Select
            value={dpi}
            onChange={(v) => {
              setPreset(null)
              setDpi(v)
            }}
            options={DPI_VALUES.map((v) => ({
              value: v,
              label: translate('measure.dpi', { value: v }),
              hint: ex(`dpiHint.${v}`),
            }))}
            disabled={!formats.includes('png')}
            ariaLabel={ex('dpiSelectLabel')}
            className="w-28"
          />
          <span className="shrink-0 font-mono text-xs text-ink-3">
            {formats.includes('png')
              ? translate('measure.pxSize', { w: pxW, h: pxH })
              : ex('pdfDpiIrrelevant')}
          </span>
        </Row>

        <Row label={ex('stemLabel')} labelWidth={52}>
          <TextInput value={stem} onChange={(e) => setStem(e.target.value)} placeholder="composed" />
          <span className="shrink-0 font-mono text-xs text-ink-3">{ex('timestampSuffix')}</span>
        </Row>

        <Row label={ex('proofLabel')} labelWidth={52}>
          <label className="flex items-center gap-1.5 text-xs text-ink-2" title={ex('proofTitle')}>
            <Toggle checked={proofOn} onChange={setWithProof} disabled={proofRequired} />
            {ex('proofToggle')}
          </label>
        </Row>

        {error && <p className="text-xs text-danger">{ex('operationFailed', { error })}</p>}

        {(result || packResult) && (
          <div className="flex flex-col gap-1 rounded-sm border border-border bg-surface-2 p-2">
            <p className="break-all text-xs text-ink-3">
              {ex('savedTo', {
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
                      if (!ok) setError(ex('revealFailed', { path: `${dir}/${f.name}` }))
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
                <p className="text-xs text-ink-2">{ex('warningsIntro')}</p>
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

/** 规范体检的四个关键数字：尺寸 / 栏位 / 最小有效字号 / DPI */
function ProfileFacts({
  pageW,
  pageH,
  column,
  singleMm,
  doubleMm,
  minPt,
  minEffectivePt,
  minDpi,
  dpi,
  vector,
  raster,
}: {
  pageW: number
  pageH: number
  column: 'single' | 'double' | null
  singleMm: number
  doubleMm: number
  minPt: number
  minEffectivePt: number | null
  minDpi: number
  dpi: number
  vector: string[]
  raster: string[]
}) {
  useTranslation('dialogs')
  const fontOk = minEffectivePt == null || minEffectivePt >= minPt
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-1 rounded-sm bg-surface-2 px-2 py-1.5 text-xs">
      <Fact label={ex('facts.page')} value={ex('facts.pageValue', { w: pageW, h: pageH })} />
      <Fact
        label={ex('facts.column')}
        value={
          column === 'single'
            ? ex('facts.columnSingle', { mm: singleMm })
            : column === 'double'
              ? ex('facts.columnDouble', { mm: doubleMm })
              : ex('facts.columnNone', { single: singleMm, double: doubleMm })
        }
        bad={column === null}
      />
      <Fact
        label={ex('facts.minFont')}
        value={
          minEffectivePt == null
            ? ex('facts.minFontNone', { min: minPt })
            : ex('facts.minFontValue', { pt: minEffectivePt })
        }
        bad={!fontOk}
      />
      <Fact
        label={ex('facts.dpi')}
        value={ex('facts.dpiValue', { dpi, min: minDpi })}
        bad={dpi < minDpi}
      />
      <Fact label={ex('facts.vector')} value={vector.join(' / ').toUpperCase()} />
      <Fact label={ex('facts.raster')} value={raster.join(' / ').toUpperCase()} />
    </div>
  )
}

function Fact({ label, value, bad = false }: { label: string; value: string; bad?: boolean }) {
  return (
    <span className="flex min-w-0 items-baseline gap-1.5">
      <span className="shrink-0 text-ink-3">{label}</span>
      <span className={cn('min-w-0 truncate font-mono', bad ? 'text-danger' : 'text-ink-2')}>
        {value}
      </span>
    </span>
  )
}

/** 预检：先一句摘要，问题明细按需展开；有阻断项时默认展开 */
function PreflightBlock({
  issues,
  onLocate,
}: {
  issues: PreflightIssue[]
  onLocate: (ids: string[]) => void
}) {
  useTranslation('dialogs')
  const sum = summarize(issues)
  const [expanded, setExpanded] = useState(sum.errors.length > 0)
  if (issues.length === 0) {
    return (
      <p className="flex items-center gap-1.5 rounded-sm bg-surface-2 px-2 py-1.5 text-xs text-ink-2">
        <Check size={12} className="shrink-0 text-accent" />
        {ex('preflightOk')}
      </p>
    )
  }
  const parts = (['error', 'warn', 'not_verifiable', 'suggestion'] as Severity[])
    .filter((s) => sum.counts[s] > 0)
    .map((s) => ex('severityCount', { count: sum.counts[s], label: severityLabel(s) }))
  return (
    <div className="rounded-sm bg-surface-2 px-2 py-1.5">
      <button
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-1.5 rounded-sm text-left text-xs outline-none focus-visible:focus-ring"
      >
        <TriangleAlert
          size={12}
          className={sum.blocking ? 'shrink-0 text-danger' : 'shrink-0 text-ink-3'}
        />
        <span className={cn('min-w-0 flex-1', sum.blocking ? 'text-danger' : 'text-ink-2')}>
          {ex('preflightParts', { parts: parts.join(' · ') })}
        </span>
        <ChevronRight
          size={11}
          className={cn('shrink-0 text-ink-3 transition-transform', expanded && 'rotate-90')}
        />
      </button>
      {expanded && (
        <ul className="mt-1.5 flex flex-col gap-1.5 border-t border-border pt-1.5">
          {issues.map((it) => (
            <IssueRow key={it.id} issue={it} onLocate={onLocate} />
          ))}
        </ul>
      )}
    </div>
  )
}

function IssueRow({
  issue,
  onLocate,
}: {
  issue: PreflightIssue
  onLocate: (ids: string[]) => void
}) {
  useTranslation('dialogs')
  const Icon = SEVERITY_ICON[issue.severity]
  const tone =
    issue.severity === 'error'
      ? 'text-danger'
      : issue.severity === 'suggestion'
        ? 'text-ink-faint'
        : 'text-ink-3'
  return (
    <li>
      <button
        onClick={() => onLocate(issue.objectIds)}
        disabled={!issue.objectIds.length}
        className="group flex w-full items-start gap-1.5 text-left text-xs leading-relaxed text-ink-2 hover:text-ink disabled:cursor-default"
      >
        <Icon size={12} className={cn('mt-px shrink-0', tone)} />
        <span className="min-w-0 flex-1">
          <span className="mr-1 rounded-[3px] bg-surface px-1 font-mono text-[10px] text-ink-3">
            {severityLabel(issue.severity)}
          </span>
          {issueText(issue)}
          {issue.objectIds.length > 1 && ex('issueOccurrences', { count: issue.objectIds.length })}
          {!!issue.gids.length && (
            <span className="ml-1 font-mono text-[10px] text-ink-faint">
              {issue.gids.slice(0, 3).join(' ')}
              {issue.gids.length > 3 && ' …'}
            </span>
          )}
        </span>
        {!!issue.objectIds.length && (
          <span className="shrink-0 text-ink-3 group-hover:text-accent">{ex('locate')}</span>
        )}
      </button>
    </li>
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
