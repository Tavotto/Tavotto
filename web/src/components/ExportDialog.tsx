/**
 * 导出面板（ADR 0031）。
 *
 * ### 信息优先级
 *
 * ```text
 * 文件名 → 输出范围 → 格式 → 分辨率（仅位图）→ 规范 → 检查 → 高级选项
 * ```
 *
 * 这个顺序不是排版偏好，是**用户做决定的顺序**：先决定这个文件叫什么、
 * 按哪个尺寸出、要什么格式，才轮到"够不够清晰"和"合不合规范"。
 *
 * ### 这里**不做**的事
 *
 * * 不现算「这个值合不合规范」。阈值一个字都不进组件；要判就加一条规则进
 *   `lib/validation.exportContextRaw()`（Session 11 的第 19 条）。
 * * 不列第二套问题清单。摘要 + 「查看问题」，完整清单在左侧问题面板（§四）。
 * * 不拼载荷。请求的构造只有 `lib/exportRequest.buildExportRequest()` 一处。
 * * 不出现内部标识：库名、gid、对象 id、绝对路径一个都不进这个界面（§五）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Check,
  Download,
  FileWarning,
  Loader2,
  Settings2,
  TriangleAlert,
  X,
} from 'lucide-react'
import type { ExportJob, ExportOutput } from '@/lib/api'
import { msg, t as translate } from '@/i18n'
import { readExportDefaults, writeExportDefaults } from '@/lib/exportDefaults'
import { openProblems } from '@/lib/issueFocus'
import {
  exportContextIssues,
  exportContextRaw,
  summaryFor,
  type ValidationSummary,
} from '@/lib/validation'
import { severityLabel } from '@/lib/validationText'
import { buildProofPayload } from '@/lib/preflight'
import {
  defaultScope,
  originalAvailability,
  pixelPreview,
  PPI_DEFAULT,
  hasRaster,
  type ExportScope,
  type ExportRequestInput,
} from '@/lib/exportRequest'
import type { OverwritePolicy } from '@/lib/exportRequest'
import type { PublicationProfile, Severity } from '@/lib/profile'
import { profileName } from '@/lib/profileText'
import { bindingFor, resolveDocumentSpec, type SpecCatalogEntry } from '@/lib/specBinding'
import { apiUrl } from '@/lib/session'
import { boundedCount, captureTelemetry } from '@/lib/telemetry'
import { cn } from '@/lib/utils'
import { isDesktop, revealExportedFile } from '@/lib/desktop'
import { useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import {
  cancelCurrentExport,
  prepareExport,
  runExport,
  useExportStore,
} from '@/store/exportStore'
import { useProfileStore } from '@/store/profileStore'
import { useProjectStore } from '@/store/projectStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { findFigurePanel, useWorkspaceStore } from '@/store/workspace'
import {
  getValidationSummary,
  rawIssuesFor,
  runValidation,
  useValidationStore,
} from '@/store/validationStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Row } from './ui/Field'
import { TextInput } from './ui/Input'
import { Select } from './ui/Select'
import { Toggle } from './ui/Toggle'

/** 本对话框的文案都在 `dialogs:export.*` 下 */
const ex = (key: string, values?: Record<string, unknown>) =>
  translate(`export.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 可选的位图分辨率。**没有第二份**——数字进不了组件之外的任何地方 */
const PPI_VALUES = ['300', '600', '900', '1200'] as const

export function ExportDialog() {
  // 订阅语言变化：文案是模块级 ex() 拼出来的，没有这一句切语言后停在旧语言上
  useTranslation(['dialogs', 'common'])
  const open = useUiStore((s) => s.exportOpen)
  const setOpen = useUiStore((s) => s.setExportOpen)
  const doc = useDocumentStore((s) => s.doc)
  const commit = useDocumentStore((s) => s.commit)
  const documentId = useDocumentStore((s) => s.documentId)
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  const assets = useAssetStore((s) => s.byId)
  const mode = useWorkspaceStore((s) => s.mode)
  const activePanelId = useWorkspaceStore((s) => s.activePanelId)
  // 订阅**值**而不是订阅一个现算的摘要：摘要的组装只有 `summaryFor()` 一份
  const validationIssues = useValidationStore((s) => s.issues)
  const validationReady = useValidationStore((s) => s.ready)
  const validationFailed = useValidationStore((s) => s.failed)
  const job = useExportStore((s) => s.job)
  const running = useExportStore((s) => s.running)
  const startError = useExportStore((s) => s.startError)
  const editedDuringExport = useExportStore((s) => s.editedDuringExport)

  const [formats, setFormats] = useState<string[]>(() => readExportDefaults().formats)
  const [ppi, setPpi] = useState(() => readExportDefaults().dpi)
  const [filename, setFilename] = useState(doc.name)
  const [scope, setScope] = useState<ExportScope>(() => defaultScope(mode))
  const [withReport, setWithReport] = useState(() => readExportDefaults().withProof)
  const [transparent, setTransparent] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  /**
   * 用户对本次导出的显式确认：阻断项与「无法核验」项都要点过才放行。
   * **不做成记住的偏好**——每次导出都得重新面对一次当前这批问题。
   */
  const [confirmed, setConfirmed] = useState(false)

  /* ------------------------------ 出版规范 ------------------------------- */
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
  const docProfileId = doc.profile?.id
  const [profileId, setProfileId] = useState(() => docProfileId ?? readExportDefaults().profileId)
  /**
   * **实际生效的规范只解析一次**（ADR 0029）：有快照就按快照，没有才按全局
   * 现值。导出面板不许自己再挑一遍——那正是「预检说合规、导出按另一套规矩」
   * 的来源。
   */
  const resolved = useMemo(
    () => resolveDocumentSpec(doc.profile ?? { id: profileId }, catalog),
    [doc.profile, profileId, catalog],
  )
  const profile: PublicationProfile = resolved.profile

  /* --------------------------- 这次要导的是什么 --------------------------- */
  /** 快速编辑正在编的那张图；画布模式下取选中的那个面板 */
  const figureId = useMemo(() => {
    const panelId = activePanelId
    if (!panelId) return null
    const o = doc.objects.find((x) => x.id === panelId)
    return o?.type === 'panel' ? o.fileId : null
  }, [activePanelId, doc.objects])
  /*
   * 这两个 memo 读的是 store 的**当前快照**（`originalAvailability` 问素材
   * 清单与 runtime 清单，`findFigurePanel` 问文档），所以依赖里必须带上那几份
   * 状态——只挂 `figureId` 的话，对话框开着时素材被删/掉线，组件重渲染了而
   * memo 还是旧值：那颗按钮继续亮着，按下去后端报 `source_missing`
   * （PR #214 复审）。
   */
  const runtimeAssets = useRuntimeAssetStore((s) => s.assets)
  const availability = useMemo(
    () => originalAvailability(figureId),
    // `assets` / `runtimeAssets` 是**触发重算的信号**，不是入参：
    // `originalAvailability()` 读的是 store 的当前快照，linter 看不见那一层
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [figureId, assets, runtimeAssets],
  )
  const panel = useMemo(
    () => (figureId ? (findFigurePanel(figureId)?.panel ?? null) : null),
    // 同上：`findFigurePanel()` 问的是 documentStore 的当前快照
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [figureId, doc.objects],
  )

  useEffect(() => {
    if (!open) return
    void useProfileStore.getState().load()
    // **当场同步跑一遍检查**：防抖那 250ms 里对话框会说"检查通过"，
    // 而那句话在检查跑完之前是假的。纯计算，没有请求
    runValidation()
    /*
     * 匿名用量统计：**预检真的算完之后**记一次。计数在这里**现取**而不是读
     * 渲染闭包里的 `summary`——上一次渲染发生在 `runValidation()` 之前。
     * 发出去的只有四个计数 + 一个布尔：文案、字体名、对象 id、文件名一个不发。
     */
    const fresh = getValidationSummary('activeCanvas')
    captureTelemetry('preflight_completed', {
      errors: boundedCount(fresh.counts.error),
      warnings: boundedCount(fresh.counts.warn),
      not_verifiable: boundedCount(fresh.counts.not_verifiable),
      suggestions: boundedCount(fresh.counts.suggestion),
      passed: fresh.counts.error === 0 && fresh.counts.warn === 0,
    })
    /*
     * 这几个初值现取，**不从渲染闭包里拿**：下面那行依赖里没有 `doc`，
     * linter 看不见这一层，但闭包里的 `doc` 在 effect 真正跑的时候就是当下
     * 那一份（effect 只在 `open` / `documentId` 变化时跑，两者变化都会带来
     * 一次重渲染）。现取只是把这件事写明白，顺便挡住以后加依赖时的走样
     */
    const snap = useDocumentStore.getState().doc
    setFilename(snap.name)
    setConfirmed(false)
    setProfileId(snap.profile?.id ?? readExportDefaults().profileId)
    // scope 默认跟着当前工作流走，**但原图不可用时不静默改成画布**：
    // 那样用户会拿到一张他没要的图。可用性由下面那一行说出来
    setScope(defaultScope(useWorkspaceStore.getState().mode))
    /*
     * **依赖只有「打开」与「换文档」，没有 `doc.profile`。**
     * 在对话框里挑一套出版规范会 `commit()` 一个新的 `d.profile`，把它列进
     * 依赖的话这个初始化 effect 当场重跑：用户刚敲进去的文件名被冲回
     * `doc.name`、确认态被清、输出范围被改回默认——一串他没要求的重置，而且
     * 没有任何提示（PR #214 第七轮评审）。
     * `doc.name` 同理：改名不该顺手把导出名冲掉。
     */
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, documentId])

  /* -------------------------------- 检查 --------------------------------- */
  const raster = hasRaster(formats)
  // 只出 PDF 时这条规则自己就不出场（`exportContextRaw()` 先看格式里有没有
  // 位图）——**不在这里再判一次**：阈值与适用范围都归求值器，组件里加一层
  // "顺手的"守卫，就是两处判据分叉的起点
  const exportIssues = useMemo(
    () =>
      exportContextIssues({ formats, dpi: Number(ppi) }, profile, {
        documentId,
        canvasId: activeCanvasId,
      }),
    [formats, ppi, profile, documentId, activeCanvasId],
  )
  const summary = useMemo(
    () =>
      summaryFor(validationIssues, {
        canvasId: activeCanvasId,
        extra: exportIssues,
        ready: validationReady,
        failed: validationFailed,
      }),
    [validationIssues, activeCanvasId, exportIssues, validationReady, validationFailed],
  )
  const errors = useMemo(
    () => summary.issues.filter((i) => i.severity === 'error'),
    [summary.issues],
  )
  const notVerifiable = useMemo(
    () => summary.issues.filter((i) => i.severity === 'not_verifiable'),
    [summary.issues],
  )
  /**
   * 需要用户点头才放行的东西：阻断项 + 无法核验项 + **这一次没查成**。
   * 查不成时那份清单可能是更早留下的，不能当成"这一版的结论"。
   */
  const needsConfirm = errors.length > 0 || notVerifiable.length > 0 || summary.failed
  /**
   * **用户确认的是"这一批"问题，不是"以后任何一批"。**
   *
   * 改了格式 / PPI、文档被编辑、或者上一次导出之后问题集合变了，那个勾必须
   * 掉——否则新出现的阻断项会**不经确认**被导出，而 `start()` 还会把它们的
   * 规则码写进样式检查报告，写成一句"用户知悉过"（PR #214 第三轮评审）。
   *
   * 指纹取自**要确认的那批问题的 issueId** + 「这次没查成」这一档。
   */
  const confirmKey = useMemo(
    () =>
      [
        summary.failed ? 'failed' : '',
        ...errors.map((i) => i.issueId),
        ...notVerifiable.map((i) => i.issueId),
      ]
        .sort()
        .join('|'),
    [errors, notVerifiable, summary.failed],
  )
  useEffect(() => {
    setConfirmed(false)
  }, [confirmKey])
  // 勾了确认框就**必须**留档：确认框上写着"这次确认会记录在报告里"，
  // 而用户可能早就把报告关掉了——那样承诺的记录一份都不会产生
  const reportRequired = needsConfirm && confirmed
  const reportOn = withReport || reportRequired
  const blocked = needsConfirm && !confirmed

  /**
   * 这次导出会不会**照抄源位图**（而不是让引擎重画一张）。
   *
   * 判据里那个 `overrides.length` 不是细节：面板带 override 时后端会先让
   * worker 全质量重渲染一次，**拿到的是一份 PDF**——像素网格不复存在，
   * PPI 重新变得有意义。少了这一条，界面会对着一张即将被重画的图报源像素
   * 网格、还说 PPI 无关（PR #214 第四轮评审）。
   */
  const copiesSourceVerbatim =
    scope === 'original' &&
    availability.spec?.sourceKind === 'raster' &&
    !(panel?.overrides?.length ?? 0)

  /** 透明背景这次起不起作用：要有位图格式，且不是「照抄源位图」那条路 */
  const transparentApplies = raster && !copiesSourceVerbatim

  /* ------------------------------ 文件名校验 ------------------------------ */
  const filenameIssue = useMemo(
    () => prepareExport(inputOf()).filenameProblem,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filename, formats, scope, ppi, doc, figureId],
  )

  function inputOf(over?: Partial<ExportRequestInput>): ExportRequestInput {
    return {
      scope,
      formats,
      filename,
      ppi: Number(ppi) || PPI_DEFAULT,
      background: transparent && transparentApplies ? 'transparent' : 'white',
      includeReport: reportOn,
      acknowledged: needsConfirm && confirmed ? [...new Set(errors.map((i) => i.ruleCode))] : [],
      documentId,
      doc,
      figureId,
      panel,
      spec: availability.spec,
      ...over,
    }
  }

  /**
   * 「这次能不能导」**只有这一份判断**。
   *
   * 主按钮的 `disabled` 与 `start()` 里的闸读的是同一个值——各写一遍的话，
   * 少写一条的那一侧就成了绕过去的路：第四轮评审那条 P1 是"按钮有闸、
   * `start()` 没有"，第五轮又抓到"两边都有，但 `start()` 那份少了一条"。
   * 一份判断、两个消费点，就没有"少写一条"这回事了。
   */
  const canStart =
    formats.length > 0 &&
    !blocked &&
    !filenameIssue &&
    (scope !== 'original' || availability.ok)

  const names = useMemo(
    () => prepareExport(inputOf()).names,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filename, formats, scope],
  )

  /* -------------------------------- 动作 --------------------------------- */
  const applyProfile = (id: string) => {
    setProfileId(id)
    writeExportDefaults({ profileId: id })
    const entry = catalog.find((e) => e.id === id)
    if (!entry) return
    // **跟随的表态跟着项目走，不跟着某一套规范走**
    commit(msg('history.setPublicationProfile', undefined, 'workspace'), (d) => {
      d.profile = bindingFor(entry, { journal: doc.profile?.journal, follow: doc.profile?.follow })
    })
  }

  const syncProfile = () => {
    const entry = catalog.find((e) => e.id === (doc.profile?.id ?? profileId))
    if (!entry) return
    commit(msg('history.syncPublicationProfile', undefined, 'workspace'), (d) => {
      d.profile = bindingFor(entry, { journal: doc.profile?.journal, follow: doc.profile?.follow })
    })
  }

  const start = useCallback(
    async (overwrite: OverwritePolicy) => {
      /*
       * **阻断闸放在这一个咽喉上，不挂在按钮上。**
       *
       * 「覆盖 / 另存一份 / 重试」都直接调 `start()`，它们没有经过主按钮的
       * `disabled`——于是一次已确认的导出撞名之后，点「覆盖」会把同一批阻断项
       * **不经确认**再导一次，而且 `acknowledged` 是空的（确认刚被清掉）、
       * 报告也不再被强制生成（PR #214 第四轮评审）。
       *
       * 逐颗按钮加 `disabled` 是治标：下一颗新按钮照样会漏。闸在这里，
       * 任何调用点都绕不过去。
       */
      // 条件要与主按钮的 `disabled` **逐条相同**：少一条就等于那颗按钮上的
      // 判断没有被这个咽喉接管，而「覆盖 / 另存 / 重试」走的正是这里
      // 闸与主按钮读**同一个** `canStart`
      if (!canStart) return
      const report = reportOn
        ? buildProofPayload(
            doc,
            assets,
            [
              ...rawIssuesFor(activeCanvasId),
              ...exportContextRaw({ formats, dpi: Number(ppi) }, profile),
            ],
            { dpi: Number(ppi), formats, stem: filename },
            profile,
            {
              forced: errors.length > 0 && confirmed,
              acknowledged: needsConfirm
                ? [...new Set([...errors, ...notVerifiable].map((i) => i.ruleCode))]
                : [],
              // 「这一次没查成，用户自己确认了继续」是**独立的一档**：
              // 只看 forced / acknowledged 的话，它与"干干净净跑过一遍"
              // 在报告里长得一模一样，而确认框上写着这次确认会被记下来
              checkFailed: summary.failed || !summary.ready,
              acknowledgedCheckFailed: (summary.failed || !summary.ready) && confirmed,
            },
          )
        : undefined
      writeExportDefaults({ formats, dpi: String(ppi), withProof: withReport })
      const job = await runExport(
        inputOf({ overwrite, report: report as Record<string, unknown> | undefined }),
      )
      /*
       * **每次真的导过之后都要重新确认**：一次点头只对那一次导出有效。
       *
       * 撞名（`conflict`）除外——那一次**什么都没写**，界面正在问的是同一次
       * 导出的另一个问题（覆盖还是另存），不是一次新的导出。在这里清掉的话，
       * 用户得为同一批问题点两次头，而第二次点头没有增加任何信息。
       */
      if (job?.status !== 'conflict') setConfirmed(false)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      doc,
      assets,
      activeCanvasId,
      formats,
      ppi,
      profile,
      filename,
      errors,
      notVerifiable,
      confirmed,
      needsConfirm,
      reportOn,
      withReport,
      scope,
      transparent,
      figureId,
      panel,
      availability.spec,
      canStart,
    ],
  )

  /** 导出完成时报一次状态。**只在终局报一次**，不在每次进度推送上报 */
  const announced = useRef<string | null>(null)
  useEffect(() => {
    if (!job || running) return
    if (announced.current === job.job_id) return
    announced.current = job.job_id
    const done = job.outputs.filter((o) => o.status === 'done' && o.name)
    if (job.status === 'done' && done.length) {
      useUiStore
        .getState()
        .setStatus(msg('export.exported', { files: done.map((o) => o.name).join('、') }, 'dialogs'))
    } else if (job.status === 'cancelled') {
      useUiStore.getState().setStatus(msg('export.cancelled', undefined, 'dialogs'))
    }
  }, [job, running])

  const conflicts = job?.status === 'conflict' ? job.conflicts : []
  const busy = running

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={ex('title')}
      width={480}
      footer={
        <>
          <span className="flex-1" />
          <Button variant="outline" size="md" onClick={() => setOpen(false)}>
            {translate('actions.close')}
          </Button>
          {busy ? (
            <Button variant="outline" size="md" onClick={() => void cancelCurrentExport()}>
              <X size={14} />
              {ex('cancelExport')}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="md"
              disabled={!canStart}
              onClick={() => void start('ask')}
              title={blocked ? ex('blockedTitle') : undefined}
            >
              <Download size={14} />
              {ex('start')}
            </Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-2.5">
        {/* 1. 文件名 —— 最上方（§五）。校验在**输入的那一刻**就地给出 */}
        <Row label={ex('filenameLabel')} labelWidth={56}>
          <TextInput
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            placeholder={ex('filenamePlaceholder')}
            aria-invalid={filenameIssue ? true : undefined}
            aria-describedby={filenameIssue ? 'export-filename-error' : undefined}
          />
        </Row>
        {filenameIssue ? (
          <p id="export-filename-error" className="pl-[64px] text-xs text-danger">
            {ex(`filenameError.${filenameIssue}`)}
          </p>
        ) : (
          <p className="pl-[64px] font-mono text-xs text-ink-3">{names.join('  ')}</p>
        )}

        {/* 2. 输出范围 —— 默认跟着工作流，用户随时切 */}
        <Row label={ex('scopeLabel')} labelWidth={56}>
          <div role="radiogroup" aria-label={ex('scopeLabel')} className="flex gap-1">
            <ScopeButton
              active={scope === 'original'}
              disabled={!availability.ok}
              label={ex('scopeOriginal')}
              onClick={() => setScope('original')}
            />
            <ScopeButton
              active={scope === 'canvas'}
              label={ex('scopeCanvas')}
              onClick={() => setScope('canvas')}
            />
          </div>
        </Row>
        <ScopeNote
          scope={scope}
          available={availability.ok}
          reason={availability.reason}
          ignored={availability.spec?.ignored ?? []}
          widthMm={availability.spec?.widthMm ?? null}
          heightMm={availability.spec?.heightMm ?? null}
          fallback={availability.spec?.fallback ?? false}
          pageW={doc.page.w}
          pageH={doc.page.h}
        />

        {/* 3. 格式 */}
        <Row label={ex('formatLabel')} labelWidth={56}>
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

        {/* 4. 分辨率 —— **只在选了位图格式时出现**（§五） */}
        {raster && (
          <Row label={ex('ppiLabel')} labelWidth={56}>
            <Select
              value={String(ppi)}
              onChange={setPpi}
              options={PPI_VALUES.map((v) => ({
                value: v,
                label: translate('measure.dpi', { value: v }),
              }))}
              ariaLabel={ex('ppiSelectLabel')}
              className="w-28"
            />
            {/* 出来多少像素**按这次的范围算**。原图范围下拿画布页面尺寸乘一遍
                是在报另一张图的数字（一张 70.6mm 的图摆在 180mm 画布上，
                600ppi 会显示成 4252px，而真实产物约 1668px）；位图原图更是
                照抄源像素网格，与 ppi 无关。 */}
            <span className="shrink-0 font-mono text-xs text-ink-3">
              {pixelPreview(scope, Number(ppi), doc.page, availability.spec, copiesSourceVerbatim)}
            </span>
          </Row>
        )}

        {/* 5. 规范 —— 只出现自然名称，id 与版本号在设置里 */}
        <Row label={ex('profileLabel')} labelWidth={56}>
          <Select
            value={doc.profile?.id ?? profileId}
            onChange={applyProfile}
            options={catalog.map((p) => ({ value: p.id, label: p.display_name }))}
            ariaLabel={ex('profileAria')}
            className="w-40"
          />
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              useUiStore.getState().setSettingsOpen(true, 'profiles')
            }}
            className="shrink-0 rounded-sm text-xs text-accent outline-none hover:underline focus-visible:focus-ring"
          >
            <Settings2 size={11} className="mr-0.5 inline" aria-hidden />
            {ex('profileEdit')}
          </button>
        </Row>
        {resolved.updateAvailable && (
          <p className="flex items-center gap-2 pl-[64px] text-xs leading-relaxed text-ink-2">
            {ex('profileUpdateAvailable')}
            <Button variant="outline" size="sm" onClick={syncProfile}>
              {ex('profileSync')}
            </Button>
          </p>
        )}
        {resolved.globalMissing && (
          <p className="pl-[64px] text-xs leading-relaxed text-ink-3">
            {resolved.source === 'snapshot' ? ex('profileMissingPinned') : ex('profileMissing')}
          </p>
        )}

        {/* 6. 检查 —— 只有摘要，完整清单在左侧问题面板（§四） */}
        <CheckRow
          summary={summary}
          onOpenPanel={() => {
            setOpen(false)
            openProblems()
          }}
        />

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
              {errors.length > 0 && notVerifiable.length > 0
                ? ex('confirmBoth', { errors: errors.length, notVerifiable: notVerifiable.length })
                : errors.length > 0
                  ? ex('confirmErrors', { errors: errors.length })
                  : notVerifiable.length > 0
                    ? ex('confirmNotVerifiable', { notVerifiable: notVerifiable.length })
                    : ex('confirmCheckFailed')}
            </span>
          </label>
        )}

        {/* 7. 高级选项 —— 默认收起 */}
        <details
          className="rounded-sm"
          open={advancedOpen}
          onToggle={(e) => setAdvancedOpen((e.target as HTMLDetailsElement).open)}
        >
          <summary className="cursor-pointer rounded-sm text-xs text-ink-2 outline-none focus-visible:focus-ring">
            {ex('advanced')}
          </summary>
          <div className="mt-1.5 flex flex-col gap-1.5 pl-1">
            <label
              className="flex items-center gap-1.5 text-xs text-ink-2"
              title={ex('reportTitle')}
            >
              <Toggle checked={reportOn} onChange={setWithReport} disabled={reportRequired} />
              {ex('reportToggle')}
            </label>
            {/* 透明背景只在「有位图格式」且**不是照抄源文件**的那条路上有意义。
                原图 + 位图源出来的就是那张图本身（我们只换容器不换像素），
                背景是它自己的——开着一个不起作用的开关就是说了而不做 */}
            <label
              className={cn(
                'flex items-center gap-1.5 text-xs',
                transparentApplies ? 'text-ink-2' : 'text-ink-3',
              )}
            >
              <Toggle
                checked={transparent && transparentApplies}
                onChange={setTransparent}
                disabled={!transparentApplies}
              />
              {ex('transparent')}
            </label>
            {raster && !transparentApplies && (
              <p className="pl-1 text-xs text-ink-3">{ex('transparentNotForRaster')}</p>
            )}
          </div>
        </details>

        {/* 进度 / 冲突 / 结果 */}
        {busy && <ProgressRow job={job} />}
        {!!conflicts.length && (
          <ConflictBar
            names={conflicts}
            onReplace={() => void start('replace')}
            onRename={() => void start('rename')}
          />
        )}
        {startError && (
          <p className="text-xs text-danger">
            {startError.code === 'bad_filename'
              ? ex(`filenameError.${startError.message}`)
              : ex('operationFailed', { error: startError.message })}
          </p>
        )}
        {job && !busy && job.status !== 'conflict' && (
          <ResultBlock job={job} edited={editedDuringExport} onRetry={() => void start('ask')} />
        )}
      </div>
    </Dialog>
  )

  function toggleFormat(f: string) {
    setFormats((prev) => (prev.includes(f) ? prev.filter((v) => v !== f) : [...prev, f]))
  }

}

/* --------------------------------- 子组件 ---------------------------------- */

function ScopeButton({
  active,
  label,
  onClick,
  disabled = false,
}: {
  active: boolean
  label: string
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'h-6 rounded-sm border px-2.5 text-xs outline-none transition-colors focus-visible:focus-ring',
        active
          ? 'border-accent bg-accent-subtle text-accent'
          : 'border-border bg-surface text-ink-2 hover:border-border-strong',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    >
      {label}
    </button>
  )
}

/**
 * 范围说明。**不可用时说出原因，不隐藏选项、不静默改成画布**（§五）。
 *
 * 原图范围下还要把「画布上设了、这次不套用」的变换逐项说出来：
 * 忽略而不说等于骗人（ADR 0028）。
 */
function ScopeNote({
  scope,
  available,
  reason,
  ignored,
  widthMm,
  heightMm,
  fallback,
  pageW,
  pageH,
}: {
  scope: ExportScope
  available: boolean
  reason: string
  ignored: readonly string[]
  widthMm: number | null
  heightMm: number | null
  fallback: boolean
  pageW: number
  pageH: number
}) {
  useTranslation('dialogs')
  return (
    <div className="flex flex-col gap-0.5 pl-[64px] text-xs leading-relaxed text-ink-3">
      {/* 原图不可用时**总是**说出原因，与当前选的是哪个范围无关：
          一个禁用的按钮解释不了自己，而"为什么灰着"正是用户此刻要问的
          （§五：不隐藏选项、不静默改为画布） */}
      {!available && (
        <span className="flex items-start gap-1.5 text-danger">
          <TriangleAlert size={11} className="mt-0.5 shrink-0" aria-hidden />
          {/* 三个原因各说各的话——折成两句的话「源文件不见了」会被说成
              「先选中一张图」，用户照做之后按钮还是灰的 */}
          {ex(`scopeUnavailable.${reason}`)}
        </span>
      )}
      {scope === 'canvas' ? (
        <span>{ex('scopeCanvasNote', { w: round1(pageW), h: round1(pageH) })}</span>
      ) : (
        <>
          <span>
            {widthMm != null && heightMm != null
              ? ex('scopeOriginalNote', { w: round1(widthMm), h: round1(heightMm) })
              : ex('scopeOriginalNoteUnknown')}
          </span>
          {fallback && <span className="text-warn">{ex('scopeOriginalFallback')}</span>}
              {ignored.length > 0 && (
            <span>
              {ex('scopeIgnored', {
                list: ignored.map((k) => ex(`ignored.${k}`)).join('、'),
              })}
            </span>
          )}
        </>
      )}
    </div>
  )
}

const round1 = (v: number) => Math.round(v * 10) / 10

/**
 * 检查摘要。**只消费统一检查服务的结果**（ADR 0030）——不跑第二遍求值器，
 * 也不在这里列第二套清单（§四）。
 */
function CheckRow({
  summary,
  onOpenPanel,
}: {
  summary: ValidationSummary
  onOpenPanel: () => void
}) {
  useTranslation(['dialogs', 'errors'])
  // 「查不了」与「没问题」是两个答案。压成一个 = 用户带着一屏静悄悄的绿投稿
  if (summary.failed || !summary.ready) {
    return (
      <Row label={ex('checkLabel')} labelWidth={56}>
        <span className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-danger">
          <TriangleAlert size={12} className="shrink-0" aria-hidden />
          {ex(summary.total ? 'preflightFailedKept' : 'preflightFailed')}
        </span>
        <OpenProblems onClick={onOpenPanel} />
      </Row>
    )
  }
  if (summary.total === 0) {
    return (
      <Row label={ex('checkLabel')} labelWidth={56}>
        <span className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-ink-2">
          <Check size={12} className="shrink-0 text-accent" aria-hidden />
          {ex('preflightOk')}
        </span>
      </Row>
    )
  }
  const parts = (['error', 'warn', 'not_verifiable', 'suggestion'] as Severity[])
    .filter((s) => summary.counts[s] > 0)
    .map((s) => ex('severityCount', { count: summary.counts[s], label: severityLabel(s) }))
  return (
    <Row label={ex('checkLabel')} labelWidth={56}>
      <span
        className={cn(
          'flex min-w-0 flex-1 items-center gap-1.5 text-xs',
          summary.blocking ? 'text-danger' : 'text-ink-2',
        )}
      >
        <TriangleAlert size={12} className="shrink-0" aria-hidden />
        {parts.join(' · ')}
      </span>
      <OpenProblems onClick={onOpenPanel} />
    </Row>
  )
}

function OpenProblems({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="shrink-0 rounded-sm text-xs text-accent outline-none hover:underline focus-visible:focus-ring"
    >
      {ex('openProblems')}
    </button>
  )
}

/** 进度。屏幕阅读器读得到阶段与进度（§十） */
function ProgressRow({ job }: { job: ExportJob | null }) {
  useTranslation('dialogs')
  const phase = job?.progress?.phase ?? 'preparing'
  const step = job?.progress?.step ?? 0
  const total = job?.progress?.total ?? 1
  return (
    <p
      role="status"
      aria-live="polite"
      className="flex items-center gap-1.5 rounded-sm bg-surface-2 px-2 py-1.5 text-xs text-ink-2"
    >
      <Loader2 size={12} className="shrink-0 motion-safe:animate-spin" aria-hidden />
      {ex(`phase.${phase}`, { step, total })}
    </p>
  )
}

/**
 * 已有同名文件。**先问再动手**（§六）：`ask` 是默认策略，撞上了就把两条
 * 明确的出路摆出来，绝不静默覆盖用户上一次的成果。
 */
function ConflictBar({
  names,
  onReplace,
  onRename,
}: {
  names: string[]
  onReplace: () => void
  onRename: () => void
}) {
  useTranslation('dialogs')
  return (
    <div className="flex flex-col gap-1.5 rounded-sm border border-warn/40 bg-surface-2 px-2 py-1.5">
      <p className="flex items-start gap-1.5 text-xs text-ink-2">
        <FileWarning size={12} className="mt-0.5 shrink-0 text-warn" aria-hidden />
        {ex('conflict', { files: names.join('、') })}
      </p>
      <div className="flex gap-1.5">
        <Button variant="outline" size="sm" onClick={onRename}>
          {ex('conflictRename')}
        </Button>
        <Button variant="outline" size="sm" onClick={onReplace}>
          {ex('conflictReplace')}
        </Button>
      </div>
    </div>
  )
}

/**
 * 结果。**逐项显示**（§九）：一次请求要 PDF+PNG 而 PNG 挂了，PDF 照常在
 * 这里可点，那一行 PNG 说出自己为什么没出来——不许把部分成功报成全部成功。
 */
function ResultBlock({
  job,
  edited,
  onRetry,
}: {
  job: ExportJob
  edited: boolean
  onRetry: () => void
}) {
  useTranslation(['dialogs', 'errors'])
  if (job.status === 'cancelled') {
    return <p className="text-xs text-ink-3">{ex('cancelledNote')}</p>
  }
  if (job.status === 'unknown') {
    // 后端重启 / 作业过期。**这与"失败"是两件事**：我们不知道那些文件写出来
    // 没有，所以既不说"已保存到"，也不说"导出失败"
    return (
      <div className="flex flex-col gap-1.5 rounded-sm border border-warn/40 bg-surface-2 p-2">
        <p className="flex items-start gap-1.5 text-xs text-ink-2">
          <TriangleAlert size={12} className="mt-0.5 shrink-0 text-warn" aria-hidden />
          {ex('jobLost')}
        </p>
        <Button variant="outline" size="sm" onClick={onRetry}>
          {ex('retry')}
        </Button>
      </div>
    )
  }
  if (job.status === 'failed' && !job.outputs.length) {
    return (
      <div className="flex flex-col gap-1.5 rounded-sm border border-danger/40 bg-surface-2 p-2">
        <p className="text-xs text-danger">
          {translate(`backend.${job.error?.code ?? 'export_failed'}`, {
            ns: 'errors',
            ...(job.error?.params ?? {}),
            defaultValue: ex('operationFailed', { error: job.error?.code ?? '' }),
          })}
        </p>
        {job.error?.recoverable !== false && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            {ex('retry')}
          </Button>
        )}
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-1 rounded-sm border border-border bg-surface-2 p-2">
      <p className="break-all text-xs text-ink-3">
        {ex('savedTo', {
          dir: job.export_dir ?? useProjectStore.getState().project?.export_dir ?? 'exports/',
        })}
      </p>
      {job.outputs.map((o) => (
        <OutputRow key={`${o.format}-${o.name ?? 'x'}`} out={o} dir={job.export_dir ?? ''} />
      ))}
      {edited && <p className="mt-1 text-xs text-warn">{ex('editedDuringExport')}</p>}
      {/* 引擎重渲染的警告：图已经出来了，但可能与画布不完全一致
          （元素不存在 = 脚本改过了）。不吞——用户投出去之前得知道 */}
      {!!job.warnings?.length && (
        <div className="mt-1 flex flex-col gap-0.5 border-t border-border pt-1">
          <p className="text-xs text-ink-2">{ex('warningsIntro')}</p>
          {job.warnings.map((w) => (
            <p key={w} className="break-all text-xs text-ink-3">
              {w}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

function OutputRow({ out, dir }: { out: ExportOutput; dir: string }) {
  useTranslation(['dialogs', 'errors'])
  const [revealError, setRevealError] = useState<string | null>(null)
  if (out.status === 'failed' || !out.name) {
    return (
      <p className="flex items-start gap-1.5 text-xs text-danger">
        <TriangleAlert size={11} className="mt-0.5 shrink-0" aria-hidden />
        {ex('outputFailed', {
          format: out.format.toUpperCase(),
          reason: translate(`backend.${out.error?.code ?? 'format_failed'}`, {
            ns: 'errors',
            ...(out.error?.params ?? {}),
            defaultValue: out.error?.code ?? '',
          }),
        })}
      </p>
    )
  }
  const dims =
    out.dimensions.px?.[0] && out.dimensions.px?.[1]
      ? translate('measure.pxSize', { w: out.dimensions.px[0], h: out.dimensions.px[1] })
      : out.dimensions.mm?.[0] && out.dimensions.mm?.[1]
        ? ex('mmSize', { w: round1(out.dimensions.mm[0]), h: round1(out.dimensions.mm[1]) })
        : ''
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-baseline gap-2">
        {isDesktop() ? (
          // 桌面里不开浏览器式文件标签页：在系统文件管理器中显示。
          // reveal 失败绝不静默——把完整路径告诉用户
          <button
            type="button"
            onClick={() => {
              if (!dir) return
              void revealExportedFile(dir, out.name!).then((ok) => {
                if (!ok) setRevealError(ex('revealFailed', { path: `${dir}/${out.name}` }))
              })
            }}
            className="min-w-0 truncate rounded-sm font-mono text-xs text-accent outline-none hover:underline focus-visible:focus-ring"
          >
            {out.name}
          </button>
        ) : (
          // 后端回的是裸路径 /exports/<name>，必须过 apiUrl() 补 pj：`<a>` 加不了
          // 请求头，不带 pj 时后端落到**默认项目**的导出目录
          <a
            href={apiUrl(out.url ?? '')}
            target="_blank"
            rel="noreferrer"
            className="min-w-0 truncate font-mono text-xs text-accent hover:underline"
          >
            {out.name}
          </a>
        )}
        <span className="shrink-0 font-mono text-[10px] text-ink-3">{dims}</span>
        {out.replaced && <span className="shrink-0 text-[10px] text-ink-3">{ex('replaced')}</span>}
      </div>
      {revealError && <p className="text-xs text-danger">{revealError}</p>}
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
      type="button"
      onClick={onClick}
      aria-pressed={checked}
      className={cn(
        'flex flex-1 items-center gap-1.5 rounded-sm border px-2 py-1 text-left outline-none transition-colors focus-visible:focus-ring',
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
