import { useEffect, useMemo, useState } from 'react'
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
import { createPackage, exportFigure, type ExportResponse } from '@/lib/api'
import { readExportDefaults, writeExportDefaults } from '@/lib/exportDefaults'
import { toExportObjects } from '@/lib/exportPayload'
import {
  buildProofPayload,
  runPreflight,
  summarize,
  type PreflightIssue,
} from '@/lib/preflight'
import {
  columnOf,
  listProfiles,
  loadProfile,
  type JournalOverride,
  type PublicationProfile,
  type Severity,
} from '@/lib/profile'
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
import { NumberField, TextInput } from './ui/Input'
import { Select } from './ui/Select'
import { Toggle } from './ui/Toggle'

const DPI_OPTIONS = [
  { value: '300', label: '300 dpi', hint: '投稿' },
  { value: '600', label: '600 dpi', hint: '出版' },
  { value: '900', label: '900 dpi', hint: '大幅' },
  { value: '1200', label: '1200 dpi', hint: '极限' },
]

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
      label: '单栏投稿',
      dpi,
      formats: [vector],
      pageW: profile.widths_mm.single,
      hint: `PDF · ${profile.widths_mm.single}mm 单栏`,
    },
    {
      id: 'double',
      label: '通栏投稿',
      dpi,
      formats: [vector],
      pageW: profile.widths_mm.double,
      hint: `PDF · ${profile.widths_mm.double}mm 通栏`,
    },
    {
      id: 'both',
      label: 'PDF + PNG',
      dpi,
      formats: ['pdf', 'png'],
      pageW: null as number | null,
      hint: `PDF+PNG · ${dpi}dpi`,
    },
    {
      id: 'screen',
      label: '屏幕预览',
      dpi: String(profile.min_raster_dpi),
      formats: ['png'],
      pageW: null as number | null,
      hint: `PNG ${profile.min_raster_dpi}dpi`,
    },
  ]
}

const SEVERITY_LABEL: Record<Severity, string> = {
  error: '阻断',
  warn: '警告',
  not_verifiable: '无法核验',
  suggestion: '建议',
}

const SEVERITY_ICON: Record<Severity, typeof TriangleAlert> = {
  error: TriangleAlert,
  warn: TriangleAlert,
  not_verifiable: ShieldQuestion,
  suggestion: Lightbulb,
}

export function ExportDialog() {
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
  const profiles = useMemo(() => listProfiles(), [])
  // 规范绑定优先看文档自己的；旧文档没有该字段时用「上次用过的」，再退默认
  const docProfileId = doc.profile?.id
  const [profileId, setProfileId] = useState(
    () => docProfileId ?? readExportDefaults().profileId,
  )
  const journal = doc.profile?.journal as JournalOverride | undefined
  const profile = useMemo(() => loadProfile(profileId, journal), [profileId, journal])
  const column = columnOf(profile, doc.page.w)
  // 期刊自定义宽度：文档里存的是覆盖值，缺省显示 profile 的双栏宽
  const [journalWidth, setJournalWidth] = useState<number | null>(
    () => (doc.profile?.journal as JournalOverride | undefined)?.widths_mm?.double ?? null,
  )

  useEffect(() => {
    if (!open) return
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
  const blocked = needsConfirm && !confirmed

  const applyProfile = (id: string, width: number | null) => {
    setProfileId(id)
    writeExportDefaults({ profileId: id })
    const nextJournal: JournalOverride | undefined =
      width != null && Number.isFinite(width) && width > 0
        ? { widths_mm: { double: width } }
        : undefined
    // 规范绑定写进文档（可撤销）：proof 与下次打开都按同一套规矩
    commit('设置出版规范', (d) => {
      d.profile = {
        id,
        ...(nextJournal ? { journal: nextJournal as Record<string, unknown> } : {}),
      }
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
        proof: withProof
          ? buildProofPayload(doc, assets, issues, settings, profile, {
              forced: sum.errors.length > 0 && confirmed,
              acknowledged: needsConfirm
                ? [...sum.errors, ...sum.notVerifiable].map((i) => i.id)
                : [],
            })
          : undefined,
      })
      setResult(res)
      useUiStore.getState().setStatus(`导出完成：${res.files.map((f) => f.name).join('、')}`)
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
        .setStatus(`已生成项目包 ${res.name}（${res.assets} 个素材），换机器可从「文档菜单 → 导入项目包」打开`)
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
      title="导出"
      description={`${doc.page.w}×${doc.page.h} mm · ${panels.length} 面板 · ${texts.length} 文字 · ${marks.length} 标注`}
      width={520}
      busy={busy}
      footer={
        <>
          <Menu
            width={200}
            align="start"
            trigger={
              <Button size="icon" disabled={busy} aria-label="更多导出选项">
                <MoreHorizontal size={14} className="text-ink-2" />
              </Button>
            }
          >
            <MenuItem disabled={!doc.objects.length} onSelect={() => void pack()}>
              <span className="flex items-center gap-2">
                <Archive size={13} className="text-ink-3" />
                打包项目（.magplot）
              </span>
            </MenuItem>
          </Menu>
          <span className="flex-1" />
          <Button variant="outline" size="md" disabled={busy} onClick={() => setOpen(false)}>
            关闭
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={!formats.length || blocked}
            loading={busy}
            loadingLabel="正在合成…"
            title={
              blocked
                ? '存在阻断性问题或无法自动核验的项，先在上方勾选确认再导出'
                : undefined
            }
            onClick={run}
          >
            <Download size={14} />
            开始导出
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2.5">
        <Row label="预设" labelWidth={52}>
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
            当前页面宽 {doc.page.w}mm，与「{activePreset.label}」的 {activePreset.pageW}mm
            不一致——预设不改页面，需要的话去「画布」标签页调整。
          </p>
        )}

        <Row label="规范" labelWidth={52}>
          <Select
            value={profileId}
            onChange={(v) => applyProfile(v, journalWidth)}
            options={profiles.map((p) => ({
              value: p.profile_id,
              label: p.label,
              hint: `v${p.version}`,
            }))}
            ariaLabel="出版规范"
            className="w-44"
          />
          <span className="shrink-0 font-mono text-xs text-ink-3">
            {profile.profile_id} · v{profile.version}
          </span>
        </Row>

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
          <Row label="期刊宽" labelWidth={52}>
            <NumberField
              value={journalWidth ?? profile.widths_mm.double}
              min={20}
              max={500}
              step={0.5}
              precision={2}
              suffix="mm"
              className="w-24"
              onChange={(v) => {
                setJournalWidth(v)
                applyProfile(profileId, v)
              }}
            />
            <span className="min-w-0 flex-1 text-xs text-ink-3">
              覆盖本规范的双栏宽度（只改这一条，其余规则继承；写进文档并随 proof 留档）
              {journalWidth != null && (
                <button
                  className="ml-2 text-accent hover:underline"
                  onClick={() => {
                    setJournalWidth(null)
                    applyProfile(profileId, null)
                  }}
                >
                  还原
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
            <span className="min-w-0 flex-1">
              我已知悉上述
              {sum.errors.length > 0 && ` ${sum.errors.length} 类阻断性问题`}
              {sum.errors.length > 0 && sum.notVerifiable.length > 0 && '与'}
              {sum.notVerifiable.length > 0 && ` ${sum.notVerifiable.length} 类无法自动核验的项`}
              ，仍然导出。确认记录会写进 proof report。
            </span>
          </label>
        )}

        <Row label="格式" labelWidth={52}>
          <FormatToggle
            checked={formats.includes('pdf')}
            onClick={() => toggleFormat('pdf')}
            title="PDF"
            hint="真矢量，投稿首选"
          />
          <FormatToggle
            checked={formats.includes('png')}
            onClick={() => toggleFormat('png')}
            title="PNG"
            hint="位图，按 dpi 渲染"
          />
        </Row>
        {profile.preferred_formats.vector.includes('svg') && (
          <p className="pl-[60px] text-xs leading-relaxed text-ink-3">
            规范也接受 SVG，但画布合成只出 PDF/PNG（合成走 PyMuPDF）。要 SVG
            请对单张图导出——图内编辑态的写回 / Codex 插件的 magplot_export 都给真矢量 SVG。
          </p>
        )}

        <Row label="分辨率" labelWidth={52}>
          <Select
            value={dpi}
            onChange={(v) => {
              setPreset(null)
              setDpi(v)
            }}
            options={DPI_OPTIONS}
            disabled={!formats.includes('png')}
            ariaLabel="导出分辨率"
            className="w-28"
          />
          <span className="shrink-0 font-mono text-xs text-ink-3">
            {formats.includes('png') ? `${pxW} × ${pxH} px` : 'PDF 与 dpi 无关'}
          </span>
        </Row>

        <Row label="文件名" labelWidth={52}>
          <TextInput value={stem} onChange={(e) => setStem(e.target.value)} placeholder="composed" />
          <span className="shrink-0 font-mono text-xs text-ink-3">_时间戳</span>
        </Row>

        <Row label="留档" labelWidth={52}>
          <label
            className="flex items-center gap-1.5 text-xs text-ink-2"
            title="JSON 留档：profile 身份 + 全部预检结果（含无法核验项）+ 素材清单 + 导出设置，随成图写入导出目录"
          >
            <Toggle checked={withProof} onChange={setWithProof} />
            随成图生成 proof report
          </label>
        </Row>

        {error && <p className="text-xs text-danger">操作失败：{error}</p>}

        {(result || packResult) && (
          <div className="flex flex-col gap-1 rounded-sm border border-border bg-surface-2 p-2">
            <p className="break-all text-xs text-ink-3">
              已保存到 {result?.export_dir ?? useProjectStore.getState().project?.export_dir ?? 'exports/'}
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
                      if (!ok) setError(`无法在文件管理器中定位，文件在：${dir}/${f.name}`)
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
                <p className="text-xs text-ink-2">
                  以下修改未能应用到重渲染的面板上，成图可能与画布不一致：
                </p>
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
  const fontOk = minEffectivePt == null || minEffectivePt >= minPt
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-1 rounded-sm bg-surface-2 px-2 py-1.5 text-xs">
      <Fact label="页面" value={`${pageW} × ${pageH} mm`} />
      <Fact
        label="栏位"
        value={
          column === 'single'
            ? `单栏 ${singleMm}mm`
            : column === 'double'
              ? `双栏 ${doubleMm}mm`
              : `不符（规范 ${singleMm}/${doubleMm}mm）`
        }
        bad={column === null}
      />
      <Fact
        label="最小有效字号"
        value={minEffectivePt == null ? `未发现低于 ${minPt}pt 的文字` : `${minEffectivePt}pt`}
        bad={!fontOk}
      />
      <Fact label="导出 DPI" value={`${dpi} dpi（规范下限 ${minDpi}）`} bad={dpi < minDpi} />
      <Fact label="矢量格式" value={vector.join(' / ').toUpperCase()} />
      <Fact label="位图格式" value={raster.join(' / ').toUpperCase()} />
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
  const sum = summarize(issues)
  const [expanded, setExpanded] = useState(sum.errors.length > 0)
  if (issues.length === 0) {
    return (
      <p className="flex items-center gap-1.5 rounded-sm bg-surface-2 px-2 py-1.5 text-xs text-ink-2">
        <Check size={12} className="shrink-0 text-accent" />
        导出前检查通过
      </p>
    )
  }
  const parts = (['error', 'warn', 'not_verifiable', 'suggestion'] as Severity[])
    .filter((s) => sum.counts[s] > 0)
    .map((s) => `${sum.counts[s]} ${SEVERITY_LABEL[s]}`)
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
          预检：{parts.join(' · ')}
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
            {SEVERITY_LABEL[issue.severity]}
          </span>
          {issue.text}
          {issue.objectIds.length > 1 && `（${issue.objectIds.length} 处）`}
          {!!issue.gids.length && (
            <span className="ml-1 font-mono text-[10px] text-ink-faint">
              {issue.gids.slice(0, 3).join(' ')}
              {issue.gids.length > 3 && ' …'}
            </span>
          )}
        </span>
        {!!issue.objectIds.length && (
          <span className="shrink-0 text-ink-3 group-hover:text-accent">定位</span>
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
