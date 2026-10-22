/**
 * 导出面板（ADR 0031）。
 *
 * ### 信息优先级
 *
 * ```text
 * 导出对象（缩略图 · 名字 · 范围 · 最终尺寸）→ 输出范围 → 文件名 → 格式
 *   → 分辨率（仅位图）→ 规范 → 检查（阻断项逐条 + 一句提示）→ 高级选项
 * ```
 *
 * 这个顺序不是排版偏好，是**用户做决定的顺序**：先看清这次导的是哪一张、
 * 多大，再决定文件叫什么、要什么格式，才轮到"够不够清晰"和"合不合规范"
 * （审计 T33：以前文件名在最上面而对象与尺寸要到第二行才出现，正在编
 * Fig1_kinetics 时默认名却是画布名「Figure 1」）。
 *
 * ### 尺寸只有一个出处
 *
 * 原图 = `hooks/useOriginalSpec`（第 ① 档 manifest `size_mm` = 这张图的图幅，与
 * `do_export` 出的页面一致；脚本按 `bbox_inches` 存盘的图，图幅就是 savefig 裁出来的那个框，
 * ADR 0098），画布 = `doc.page`。界面上只在对象头部显示一次。
 * 它与磁盘原件仍可能不同：升级前的排版按 figsize 保留着（`figure.frame = "figsize"`）、或原件
 * 是别的机器 / 别的 matplotlib 存的（文字度量差零点几毫米）——判据是素材是矢量源且
 * `logical_w_mm` / `logical_h_mm` 与图幅差过 0.05 mm，满足时多说一句「磁盘上的原件是 … ；
 * 这里按图幅 … 出图」。
 *
 * ### 这里**不做**的事
 *
 * * 不现算「这个值合不合规范」。阈值一个字都不进组件；要判就加一条规则进
 *   `lib/validation.exportContextRaw()`（Session 11 的第 19 条）。
 * * 不列第二套问题清单。摘要 + 「查看问题」，完整清单在左侧问题面板（§四）。
 *   **唯一例外是阻断项**：它们逐条列在知情确认框上方（无筛选、无修复、一个
 *   「定位」入口），其下一句提示 +「去修改」；**不拦导出**，未处理的项自动记进报告。
 * * 不拼载荷。请求的构造只有 `lib/exportRequest.buildExportRequest()` 一处。
 * * 不出现内部标识：库名、gid、对象 id、绝对路径一个都不进这个界面（§五）。
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import {
  Check,
  CircleCheck,
  Download,
  FileExclamationPoint,
  ImageOff,
  Pencil,
  TriangleAlert,
  X,
} from "@/components/ui/icons";
import { ICON_SIZE } from "@/components/ui/Icon";
import { CanvasThumb } from "./CanvasThumb";
import { Checkbox } from "./ui/Checkbox";
import { Segmented } from "./ui/Segmented";
import {
  engineErrorMsg,
  type AssetOriginalSpec,
  type ExportJob,
  type ExportOutput,
  type PanelInfo,
} from "@/lib/api";
import { formatMessage, msg, t as translate, type UiMessage } from "@/i18n";
import { emitActivity } from "@/lib/activity";
import { useHtmlMarkup } from "@/lib/useHtmlMarkup";
import {
  onExportDefaultsHydrated,
  readExportDefaults,
  writeExportDefaults,
} from "@/lib/exportDefaults";
import { artifactValidationIssue } from "@/lib/artifactValidation";
import { inspectionState } from "@/lib/artifactInspection";
import { RetryImg } from "@/components/ui/RetryImg";
import {
  contextFigureId,
  findExportPanel,
  figureOfPanel,
  figureThumbSrc,
  listExportableFigures,
  type ExportableFigure,
} from "@/lib/exportFigures";
import { openProblems } from "@/lib/issueFocus";
import { stemOf } from "@/lib/openRequest";
import type { Severity } from "@/lib/profile";
import type { OriginalOutputSpec } from "@/lib/originalSpec";
import {
  exportContextIssues,
  exportContextRaw,
  rawIssuesForObject,
  summaryFor,
  type ValidationSummary,
} from "@/lib/validation";
import { buildProofPayload } from "@/lib/preflight";
import {
  defaultScope,
  epsAvailability,
  pixelPreview,
  PPI_DEFAULT,
  hasRaster,
  type ExportFormat,
  type ExportScope,
  type ExportRequestInput,
} from "@/lib/exportRequest";
import { copiesSourceVerbatim as copiesVerbatimFromRoute, unavailablePanels, type ExportRoute } from "@/lib/exportRoute";
import { fetchExportRoute, routeKeyOf, useExportRouteStore } from "@/store/exportRouteStore";
import { buildBatchInput, type BatchShared } from "@/lib/exportBatchInput";
import type { OverwritePolicy } from "@/lib/exportRequest";
import { DEFAULT_PROFILE_ID, type PublicationProfile } from "@/lib/profile";
import { profileName } from "@/lib/profileText";
import { useLocale } from "@/i18n/react";
import {
  bindingFor,
  resolveDocumentSpec,
  type SpecCatalogEntry,
} from "@/lib/specBinding";
import { apiUrl } from "@/lib/session";
import { boundedCount, captureTelemetry } from "@/lib/telemetry";
import { cn } from "@/lib/utils";
import { canRevealInFileManager, isDesktop, revealExportedFile } from "@/lib/desktop";
import { useOriginalAvailability } from "@/hooks/useOriginalSpec";
import { isJustBakedBaseline } from "@/store/actions";
import { useAssetStore } from "@/store/assetStore";
import { usePanelRender, useRenderStore } from "@/store/renderStore";
import { useRuntimeAssetStore } from "@/store/runtimeAssetStore";
import { useSelectionStore } from "@/store/selectionStore";
import {
  beginExportBatch,
  recordBatchFrozen,
  cancelCurrentExport,
  recordBatchJob,
  recordBatchStartFailure,
  refreshExportStatus,
  requestBatchCancel,
  prepareExport,
  runExport,
  useExportStore,
  type ExportBatch,
} from "@/store/exportStore";
import { useProfileStore } from "@/store/profileStore";
import { useProjectStore } from "@/store/projectStore";
import { useDocumentStore } from "@/store/documentStore";
import { dialogCovered, useUiStore } from "@/store/uiStore";
import { useWorkspaceStore } from "@/store/workspace";
import type { FigureDocument, PanelObject } from "@/types/document";
import {
  getValidationSummary,
  originalValidationFor,
  rawIssuesFor,
  runValidation,
  useValidationStore,
} from "@/store/validationStore";
import { Button } from "./ui/Button";
import { FormRow } from "./FormRow";
import { Dialog } from "./ui/Dialog";
import { TextInput } from "./ui/Input";
import { TaskDiagnostic } from "./TaskDiagnostic";
import { SendReportButton } from "./SendReportButton";
import { Select } from "./ui/Select";
import { Toggle } from "./ui/Toggle";
import { Card } from "./ui/Card";
import { CountChip, type CountTone } from "./ui/CountChip";
import { FieldGroup, FormSection } from "./ui/FormSection";
import { Notice } from "./ui/Notice";
import { splitNoticeSentences } from "@/lib/noticeText";
import { ProgressBar } from "./ui/ProgressBar";
import { dirTail } from "@/lib/pathDisplay";


/** 导出终局 toast 的主人：重开对话框 / 新一轮开始时撤掉 */
const EXPORT_STATUS_OWNER = "export.result";

/** 本对话框的文案都在 `dialogs:export.*` 下 */
/** 一条失败的原始材料：标题（一句人话）+ 后端原文 + 可选稳定码 */
interface FailureRaw {
  /** 失败事件的稳定身份（原始错误描述符 / 稳定码，**不是**翻译后的文字）：排先后、重置详情都认它，切语言不改变它 */
  id: string;
  title: string;
  text: string;
  code?: string;
  /** `text` 是标题之外的补充（出路 / 技术原文）：整段进「详情」，不再接在标题后面 */
  detailOnly?: boolean;
}
type FailureKind = "refusal" | "start-error" | "cancel-error" | "poll-error" | "reveal-error";

/**
 * 导出脚部状态区的失败条**同时最多一条**：几路错误各自在不同时刻出现，后来的替换前一条（按「由空变有 / 内容变了」
 * 的先后盖戳，取戳最大的那路）。原文是一句读得懂的话就接在标题后面（连着两句就取开头放得下的整句，其余进详情）；裸的「HTTP 500」、过长或多行的整段进「详情」，
 * 详情里再带上稳定码——不再只剩一个状态码。
 */
function useLatestFailure(raw: Record<FailureKind, FailureRaw | null>) {
  const book = useRef({ n: 0, stamp: {} as Record<string, number>, last: {} as Record<string, string | null> });
  const kinds = Object.keys(raw) as FailureKind[];
  for (const k of kinds) {
    const sig = raw[k] ? raw[k]!.id : null;
    if (sig !== null && sig !== book.current.last[k]) book.current.stamp[k] = ++book.current.n;
    book.current.last[k] = sig;
  }
  const live = kinds.filter((k) => raw[k]);
  if (live.length === 0) return null;
  const kind = live.reduce((a, b) => ((book.current.stamp[b] ?? 0) > (book.current.stamp[a] ?? 0) ? b : a));
  const r = raw[kind]!;
  const bare = /^HTTP \d{3}$/.test(r.text.trim());
  const split = r.detailOnly ? { inline: null, detail: r.text.trim() || null } : bare ? { inline: null, detail: r.text.trim() } : splitNoticeSentences(r.text);
  const detail = [r.code, split.detail].filter(Boolean).join("\n") || null;
  return { kind, id: r.id, title: r.title, inline: split.inline, detail };
}

/**
 * 起作业失败的一条原材料。有具体的、翻译过的原因（源文件已变 / 会话失效 / 连接失败……）时，**那一句就是标题**
 * （用户要读的是原因，不是「导出没完成」）；连着的出路句、稳定码进详情。只有后端原文（裸「HTTP 500」、
 * 没有文案的 code）才用通用标题兜底，原文进详情。
 */
function startFailureRaw(code: string, message: UiMessage): FailureRaw {
  const text = formatMessage(message);
  const id = `${code}:${JSON.stringify(message)}`;
  const known = message.key !== "literal" ? splitNoticeSentences(text) : null;
  if (known?.inline) return { id, title: known.inline, text: known.detail ?? "", code, detailOnly: true };
  return { id, title: ex("failTitle"), text, code };
}

const ex = (key: string, values?: Record<string, unknown>) =>
  translate(`export.${key}`, { ns: "dialogs", ...(values ?? {}) });

/** 可选的位图分辨率。**没有第二份**——数字进不了组件之外的任何地方 */
const PPI_VALUES = ["300", "600", "900", "1200"] as const;


/**
 * 默认文件名**跟着导出对象走**（审计 T33）：按原图 = 那张图的名字（面板名，
 * 否则文件 stem），按画布 = 画布名。用户改过之后就不再动它（`filenameTouched`
 * ——按标志不按字符串：他恰好敲回默认值也算改过）。合法性不在这里判，那是
 * `lib/exportName.ts` 的事（严格同源对）。
 */
export function defaultExportName(
  scope: ExportScope,
  panel: PanelObject | null,
  doc: Pick<FigureDocument, "name">,
): string {
  if (scope === "original" && panel) return panel.name ?? stemOf(panel.fileId);
  return doc.name;
}

/**
 * 「定位」/「查看问题」把对话框让开时留下的表单状态。对话框组件本身一直挂着
 * （`App` 里无条件渲染），所以这只是一个 ref，不进 store、不落盘；再点「导出」
 * 时原样还回去（确认态除外——问题集合可能已经变了）。换了文档就作废。
 */
interface ParkedState {
  documentId: string | null;
  scope: ExportScope;
  formats: string[];
  ppi: string;
  filename: string;
  filenameTouched: boolean;
  withReport: boolean;
  transparent: boolean;
  strict: boolean;
}

/** 此刻快速编辑正在编的那张图（打开对话框那一刻现取，不从渲染闭包里拿） */
function currentFigurePanel(): PanelObject | null {
  const id = useWorkspaceStore.getState().activePanelId;
  if (!id) return null;
  const o = useDocumentStore.getState().doc.objects.find((x) => x.id === id);
  return o?.type === "panel" ? o : null;
}

export function ExportDialog() {
  // 订阅语言变化：文案是模块级 ex() 拼出来的，没有这一句切语言后停在旧语言上
  useTranslation(["dialogs", "common"]);
  const open = useUiStore((s) => s.exportOpen);
  // 设置 / 论文样式压在上面时整层藏起来（状态不丢），它们关掉就回来（审计 T35）
  const covered = useUiStore((s) => dialogCovered(s.dialogStack, "export"));
  const setOpen = useUiStore((s) => s.setExportOpen);
  const doc = useDocumentStore((s) => s.doc);
  const commit = useDocumentStore((s) => s.commit);
  const documentId = useDocumentStore((s) => s.documentId);
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId);
  const assets = useAssetStore((s) => s.byId);
  const mode = useWorkspaceStore((s) => s.mode);
  const activePanelId = useWorkspaceStore((s) => s.activePanelId);
  // 订阅**值**而不是订阅一个现算的摘要：摘要的组装只有 `summaryFor()` 一份
  const validationIssues = useValidationStore((s) => s.issues);
  const validationReady = useValidationStore((s) => s.ready);
  const validationFailed = useValidationStore((s) => s.failed);
  const storeJob = useExportStore((s) => s.job);
  const running = useExportStore((s) => s.running);
  const batch = useExportStore((s) => s.batch);
  const batchActive = !!batch && !batch.done;
  /** 一批收尾后，结果块读的是汇总作业；其余时候是 store 里当前那一个 */
  const job = batch?.done ? batch.aggregate : storeJob;
  /** 有作业在飞，或一批还没收尾（两张之间也算忙；撞名停住的那一张不算——它在等用户决定） */
  const busy = running || (batchActive && storeJob?.status !== "conflict");
  const lastInput = useExportStore((s) => s.lastInput);
  const startError = useExportStore((s) => s.startError);
  const pollError = useExportStore((s) => s.pollError);
  const refreshing = useExportStore((s) => s.refreshing);
  const cancelError = useExportStore((s) => s.cancelError);
  const cancelState = useExportStore((s) => s.cancelState);
  const editedDuringExport = useExportStore((s) => s.editedDuringExport);
  /** 导出会落到哪个目录（后端说了算：`project.export_dir`）；导出之前就摆出来，改它在设置 › 导出 */
  const exportDir = useProjectStore((s) => s.project?.export_dir ?? null);

  const [formats, setFormats] = useState<string[]>(
    () => readExportDefaults().formats,
  );
  const [ppi, setPpi] = useState(() => readExportDefaults().dpi);
  const [filename, setFilename] = useState(doc.name);
  /** 用户亲手改过文件名（之后切范围不再替他换默认名） */
  const [filenameTouched, setFilenameTouched] = useState(false);
  const [scopeState, setScope] = useState<ExportScope>(() => defaultScope(mode));
  /**
   * 单图 / 多图两档由工作区 mode 定（`fast_edit` = 单图，`layout` = 多图；以后改成按图数推出时
   * 只换这一行）。单图编辑**没有「输出范围」**：固定按原图尺寸导（标注 / 序号照常带入）。
   */
  const single = mode === "fast_edit";
  /** 多图时左栏预览挑的那一项（"page" = 整版，否则是 figureId）；null = 跟着导出内容走。只换预览，不改导出内容 */
  const [previewId, setPreviewId] = useState<string | null>(null);
  const parked = useRef<ParkedState | null>(null);
  const [withReport, setWithReport] = useState(
    () => readExportDefaults().withProof,
  );
  const [transparent, setTransparent] = useState(false);
  /** 严格核验产物（ADR 0068 的 `strict` 政策）：必需项失败或无法核验的那一项不发布 */
  const [strict, setStrict] = useState(
    () => readExportDefaults().strictInspection,
  );
  /**
   * 打开对话框那一刻已经落定的作业：它是**上一回**的结果，不是这一回刚导完的。
   * 完成态脚部（「再次导出 · 完成」）只给这一回在对话框里跑完的作业；重新打开时
   * 脚部回到「开始导出」——用户打开导出就是要导，不该先面对一个「完成」。
   */
  const [settledJobId, setSettledJobId] = useState<string | null>(null);
  /** 完成态「在文件夹中显示」失败时的完整路径（不静默） */
  const [revealError, setRevealError] = useState<string | null>(null);

  /* ------------------------------ 出版规范 ------------------------------- */
  const specRecords = useProfileStore((s) => s.specs);
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
  );
  const docProfileId = doc.profile?.id;
  const [profileId, setProfileId] = useState(
    () => docProfileId ?? readExportDefaults().profileId,
  );

  /* --------------------------- 这次要导的是什么 --------------------------- */
  /*
   * 下面几个 memo 读的是 store 的**当前快照**（候选清单问文档 / 素材清单 /
   * runtime 清单，`contextFigureId` 问工作区与选区，`findFigurePanel` 问文档），
   * 所以依赖里必须带上那几份状态——只挂 `figureId` 的话，对话框开着时素材被
   * 删/掉线，组件重渲染了而 memo 还是旧值：那颗按钮继续亮着，按下去后端报
   * `source_missing`（PR #214 复审）。那几份状态是**触发重算的信号**，不是入参，
   * linter 看不见那一层。
   */
  const runtimeAssets = useRuntimeAssetStore((s) => s.assets);
  const runtimeById = useRuntimeAssetStore((s) => s.byId);
  // 缩略图换代用（对象头与清单行共用 `figure.stamp`，runtime 预览重画时要跟着换）
  const runtimePreviewNonce = useRuntimeAssetStore((s) => s.previewNonce);
  const assetPanels = useAssetStore((s) => s.panels);
  const canvases = useDocumentStore((s) => s.canvases);
  const selectedIds = useSelectionStore((s) => s.ids);
  /** 项目里能按原图导的图：文档里的面板优先，其次素材清单里还没上画布的 */
  const figures = useMemo(
    () => listExportableFigures(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [doc.objects, canvases, assets, assetPanels, runtimeAssets, runtimeById, runtimePreviewNonce],
  );
  /**
   * 多图排版里「每张图」= **当前激活画布上**的图面板（用户 10-21 裁决，设计稿左栏的「整版 / 图 a / 图 b」）。
   * 隐藏的图也算在画布上（与「隐藏的图也计数」一致）；素材库里还没上画布的图、别的画布上的图都不在其内——
   * 左栏缩略图条、`每张图单独导出` 的批都只认它。
   */
  const canvasFigures = useMemo(
    () =>
      figures.filter((f) =>
        doc.objects.some((o) => o.type === "panel" && o.fileId === f.figureId),
      ),
    [figures, doc.objects],
  );
  /**
   * 用户在对话框列表里点过的那张。**对话框本地状态**，打开时清空：它是这一次
   * 导出的选择，不是画布选区，也不进工作区——点缩略图不该改画布上选中了什么。
   */
  const [pickedFigureId, setPickedFigureId] = useState<string | null>(null);
  /**
   * 这次导的是哪一张：用户点过的优先（还在清单里才算数——对话框开着时素材
   * 被删了，点过的那张不能变成一个凭空的 id）；没点过就按上下文
   * （快速编辑正在编的 → 画布上选中的面板 → 项目里只有一张时就是它）。
   */
  const figureId = useMemo(
    () =>
      pickedFigureId && figures.some((f) => f.figureId === pickedFigureId)
        ? pickedFigureId
        : contextFigureId(figures),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pickedFigureId, figures, activePanelId, selectedIds, doc.objects],
  );
  /*
   * 可用性与规格走 `hooks/useOriginalSpec` 那一份绑定——它订阅了规格依赖的
   * **每一份**状态（文档 / 渲染态 / 素材清单 / runtime 清单）。这里从前只挂
   * 素材清单：素材被删/掉线时会重算，但渲染回来、图幅同步进文档时**不会**
   * ——对话框于是停在打开那一刻的旧尺寸，与快速编辑条上的数对不上（审计 T33：
   * 75.3 × 58.7 对 80 × 57.6）。快速编辑条用的是同一个 hook，两处不可能再各挂
   * 各的依赖。`anyFigures` 仍要给：**「没选」与「没得选」是两句不同的话**。
   */
  const target = useMemo(
    () => (figureId ? findExportPanel(figureId) : null),
    // Selection and workspace choose the same-file instance, not just the file.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [figureId, doc.objects, canvases, activePanelId, selectedIds],
  );
  const panel = target?.panel ?? null;
  const availability = useOriginalAvailability(figureId, {
    anyFigures: figures.length > 0,
    panel,
  });
  /**
   * 这次导出每个面板用哪份来源——**后端回答**（`/api/export/validate` 的 `route`，#1209），
   * 对话框与预检不再各自推算。键对不上当前输入（改了编辑 / 范围 / 格式）就当没有，走
   * `lib/exportRoute` 的退路；请求在输入稳定 250ms 后发，失败不打扰用户。
   */
  /**
   * 单图档没有「输出范围」：默认固定按原图尺寸导。**原图口径不可用时**（这张图没有磁盘原件——例如脚本现跑出来后
   * 重开、素材清单里还没有它——或根本没有可选的图）静默改按画布导（内部 canvas scope，界面上不出现范围选择、
   * 不报错）；左栏预览如实显示导出的页面与页面尺寸（`ExportPreviewPane` 的 `fallbackPage`）。多图档由用户选。
   */
  const scope: ExportScope = single ? (availability.ok ? "original" : "canvas") : scopeState;
  const singleFallback = single && scope === "canvas";
  const routeKey = routeKeyOf({ scope, formats, doc, panel, figureId });
  const route = useExportRouteStore((s) => (s.key === routeKey ? s.route : null));
  const refreshRoute = useExportRouteStore((s) => s.refresh);
  const invalidateRoute = useExportRouteStore((s) => s.invalidate);
  // 打开的那一刻先把上一次的答案作废：250 ms 防抖 + 往返期间界面走退路（不比现状乐观），
  // 不拿关着时的旧 offers 去禁用 / 放开 EPS
  useEffect(() => {
    if (open) invalidateRoute();
  }, [open, invalidateRoute]);
  // 只在对话框开着时取；每次打开（`open` false→true）都重取一次，哪怕输入没变——
  // 脚本登记可能在对话框关着的时候变了。关着不发请求
  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => void refreshRoute(inputOf()), 250);
    return () => clearTimeout(timer);
    // inputOf 每次渲染都是新函数；真正决定 route 的输入都在 routeKey 里
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, routeKey, refreshRoute]);
  /**
   * 这次的检查、留档与严格核验**按哪张画布算**：原图 = 那张图所在的画布（它可能不是
   * 当前画布），画布 = 当前画布。下面的规范、摘要、导出上下文检查、报告的条目与规范戳、
   * 严格核验的 `profile_id` 全都从这一个 id 取——各取各的，就会出现「摘要按目标画布的
   * 8 pt 判干净、报告却说按当前画布的 10 pt 查过」（Codex 评审 #596 P1）。
   */
  const checkCanvasId =
    scope === "original" && target ? target.canvasId : activeCanvasId;
  const checkOnActive = checkCanvasId === activeCanvasId;
  /**
   * **实际生效的规范只解析一次**（ADR 0029），解析的是 `checkCanvasId` 那张画布的
   * 绑定：有快照就按快照，没有才按全局现值。导出面板不许自己再挑一遍——那正是
   * 「预检说合规、导出按另一套规矩」的来源。
   *
   * - 当前画布：绑定读现值 `doc.profile`（`canvases[]` 那份要等切画布才回写），
   *   没绑过时按对话框里挑的那套；
   * - 别的画布：**只按它自己的绑定**，与 `collectCanvases()` 给那张画布跑检查时的
   *   输入逐字相同——摘要是按那一份算出来的，报告与严格核验就不能盖另一份的戳。
   */
  const resolved = useMemo(
    () =>
      resolveDocumentSpec(
        checkOnActive
          ? (doc.profile ?? { id: profileId })
          : canvases.find((c) => c.id === checkCanvasId)?.profile,
        catalog,
      ),
    [checkOnActive, checkCanvasId, doc.profile, profileId, canvases, catalog],
  );
  const profile: PublicationProfile = resolved.profile;
  /** 这次生效的那套规范的 id：规范下拉的显示值、严格核验的 `profile_id` 都认它 */
  const checkProfileId =
    resolved.profileId ?? (checkOnActive ? profileId : DEFAULT_PROFILE_ID);
  /** 这次按哪套规范查的（检查结论里说出来）；取名与问题面板的证据句同一做法 */
  const locale = useLocale();
  const checkSpecName = useMemo(() => {
    const record = resolved.profileId
      ? specRecords.find((r) => r.id === resolved.profileId)
      : undefined;
    return record ? profileName(record) : resolved.profile.label;
    // 内置规范的名字（`profileName`）在调用时按当前语言成文：换语言要重算，否则结论行换了语言、
    // 规范名还是旧的（#1194 评审 P2；问题面板 NoneEvidence 同一条，Codex #832）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolved, specRecords, locale]);
  /** 别的画布上的规范在这里改不了（`applyProfile` 写的是当前画布）——说出原因，不静默改错画布 */
  const checkCanvasName = checkOnActive
    ? null
    : (canvases.find((c) => c.id === checkCanvasId)?.name ?? "");

  useEffect(() => {
    if (!open) return;
    void useProfileStore.getState().load();
    // **当场同步跑一遍检查**：防抖那 250ms 里对话框会说"检查通过"，
    // 而那句话在检查跑完之前是假的。纯计算，没有请求
    runValidation();
    /*
     * 匿名用量统计：**预检真的算完之后**记一次。计数在这里**现取**而不是读
     * 渲染闭包里的 `summary`——上一次渲染发生在 `runValidation()` 之前。
     * 发出去的只有四个计数 + 一个布尔：文案、字体名、对象 id、文件名一个不发。
     */
    const fresh = getValidationSummary("activeCanvas");
    captureTelemetry("preflight_completed", {
      errors: boundedCount(fresh.counts.error),
      warnings: boundedCount(fresh.counts.warn),
      not_verifiable: boundedCount(fresh.counts.not_verifiable),
      suggestions: boundedCount(fresh.counts.suggestion),
      passed: fresh.counts.error === 0 && fresh.counts.warn === 0,
    });
    /*
     * 这几个初值现取，**不从渲染闭包里拿**：下面那行依赖里没有 `doc`，
     * linter 看不见这一层，但闭包里的 `doc` 在 effect 真正跑的时候就是当下
     * 那一份（effect 只在 `open` / `documentId` 变化时跑，两者变化都会带来
     * 一次重渲染）。现取只是把这件事写明白，顺便挡住以后加依赖时的走样
     */
    const snap = useDocumentStore.getState().doc;
    setPreviewId(null);
    const prior = useExportStore.getState();
    setSettledJobId(prior.job && !prior.running ? prior.job.job_id : null);
    setRevealError(null);
    setPickedFigureId(null);
    setProfileId(snap.profile?.id ?? readExportDefaults().profileId);
    // 从「定位」/「查看问题」回来：把用户填过的东西原样还回去。换了文档不还
    const restore = parked.current;
    parked.current = null;
    if (
      restore &&
      restore.documentId === useDocumentStore.getState().documentId
    ) {
      setScope(restore.scope);
      setFormats(restore.formats);
      setPpi(restore.ppi);
      setFilename(restore.filename);
      setFilenameTouched(restore.filenameTouched);
      setWithReport(restore.withReport);
      setTransparent(restore.transparent);
      setStrict(restore.strict);
      return;
    }
    // scope 默认跟着当前工作流走，**但原图不可用时不静默改成画布**：
    // 那样用户会拿到一张他没要的图。可用性由下面那一行说出来
    const scope0 = defaultScope(useWorkspaceStore.getState().mode);
    setScope(scope0);
    // 默认文件名跟着导出对象走：正在编 Fig1_kinetics 就叫 Fig1_kinetics，不叫画布名
    setFilenameTouched(false);
    setFilename(defaultExportName(scope0, currentFigurePanel(), snap));
    /*
     * **依赖只有「打开」与「换文档」，没有 `doc.profile`。**
     * 在对话框里挑一套出版规范会 `commit()` 一个新的 `d.profile`，把它列进
     * 依赖的话这个初始化 effect 当场重跑：用户刚敲进去的文件名被冲回
     * `doc.name`、确认态被清、输出范围被改回默认——一串他没要求的重置，而且
     * 没有任何提示（PR #214 第七轮评审）。
     * `doc.name` 同理：改名不该顺手把导出名冲掉。
     */
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, documentId]);

  // 导出默认值以后端为准（#715 PR-B）：这个对话框在 App 里常驻挂载，上面几个 useState 的初值
  // 在挂载那一刻读的本机缓存——换了 origin 的首启那一刻还是空的。后端那份取回来之后重读一次；
  // 对话框此刻开着就先记下、**关上时**再重读（用户可能正在改，不当面换掉；但也不丢——
  // 否则关了再开仍是取回前的 600 ppi，#719 Codex P2）
  const hydrationDeferred = useRef(false);
  const applyHydratedDefaults = useCallback(() => {
    const d = readExportDefaults();
    setFormats(d.formats);
    setPpi(d.dpi);
    setWithReport(d.withProof);
    setStrict(d.strictInspection);
  }, []);
  useEffect(
    () =>
      onExportDefaultsHydrated(() => {
        if (useUiStore.getState().exportOpen) hydrationDeferred.current = true;
        else applyHydratedDefaults();
      }),
    [applyHydratedDefaults],
  );
  useEffect(() => {
    if (open || !hydrationDeferred.current) return;
    hydrationDeferred.current = false;
    applyHydratedDefaults();
  }, [open, applyHydratedDefaults]);

  // 本地活动信号：面板开着时输出范围是什么（初值与每次切换都发）。教程按它
  // 判「用户确认过原图 / 画布」——不读 DOM、不猜 CSS class
  useEffect(() => {
    if (open) emitActivity({ kind: "export.scope_changed", scope });
  }, [open, scope]);

  /* -------------------------------- 检查 --------------------------------- */
  const raster = hasRaster(formats);
  // 只出 PDF 时这条规则自己就不出场（`exportContextRaw()` 先看格式里有没有
  // 位图）——**不在这里再判一次**：阈值与适用范围都归求值器，组件里加一层
  // "顺手的"守卫，就是两处判据分叉的起点
  const exportIssues = useMemo(
    () =>
      exportContextIssues({ formats, dpi: Number(ppi) }, profile, {
        documentId,
        canvasId: checkCanvasId,
      }),
    [formats, ppi, profile, documentId, checkCanvasId],
  );
  /**
   * 摘要**按导出目标取范围**（审计 T33）：按原图导出只算那张图上的问题——
   * 别的图的字号、页面的比例都不是这次要出的东西；按画布算整张画布。
   * 范围的裁法在 `summaryFor()` 一处，对话框不自己筛。
   */
  const targetObjectId = scope === "original" && panel ? panel.id : undefined;
  /** 原图导出的图幅（mm）：摘要的原图口径检查与留档的 `page_mm` 认同一份 */
  const originalWidthMm = targetObjectId
    ? (availability.spec?.widthMm ?? panel?.nativeW)
    : undefined;
  const originalHeightMm = targetObjectId
    ? (availability.spec?.heightMm ?? panel?.nativeH)
    : undefined;
  /**
   * 原图范围检查的是**原图那份产物**（scale 1、页面 = 图幅），不是图在画布上的摆法：
   * 画布那份字号乘了画布缩放，160 mm 的图缩到 80 mm 摆着时，8 pt 会被说成 4 pt
   * 的阻断，而导出的文件里它仍是 8 pt（ADR 0028）。同一个求值器、同一份输入，
   * 由 store 给出（`originalValidationFor`），对话框不自己求值；`validationIssues`
   * 只作为「检查刚重跑过」的信号进依赖。
   */
  const original = useMemo(
    () =>
      targetObjectId && originalWidthMm && originalHeightMm
        ? originalValidationFor(checkCanvasId, {
            objectId: targetObjectId,
            widthMm: originalWidthMm,
            heightMm: originalHeightMm,
            formats,
            route,
          })
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [validationIssues, checkCanvasId, targetObjectId, originalWidthMm, originalHeightMm, formats, route],
  );
  /**
   * 原图范围、选中的图只在素材 / 运行时清单里（`panel` 为 null）：它不在任何画布上，
   * 没有对象可按原图口径求值（`originalValidationFor` 要一个画布对象），而当前画布的
   * 问题是**别的东西**的问题——借来当结论，干净画布会被读成「这张图查过、没问题」，
   * 画布上的阻断项也会挡住一张无关的图（#1228 评审 P2）。如实说没检查：摘要记作
   * 「没查成」（`failed`，沿用既有的确认门禁与报告里的 checkFailed 档），不带任何画布条目。
   */
  const uncheckedOffCanvas = scope === "original" && !!figureId && !panel;
  const summary = useMemo(
    () =>
      summaryFor(uncheckedOffCanvas ? [] : (original?.result?.issues ?? validationIssues), {
        canvasId: checkCanvasId,
        objectId: targetObjectId,
        extra: exportIssues,
        ready: validationReady,
        failed: uncheckedOffCanvas || validationFailed || !!original?.failed,
      }),
    [
      uncheckedOffCanvas,
      original,
      validationIssues,
      checkCanvasId,
      targetObjectId,
      exportIssues,
      validationReady,
      validationFailed,
    ],
  );
  const errors = useMemo(
    () => summary.issues.filter((i) => i.severity === "error"),
    [summary.issues],
  );
  const notVerifiable = useMemo(
    () => summary.issues.filter((i) => i.severity === "not_verifiable"),
    [summary.issues],
  );
  /**
   * 导出时**还没处理的项**：阻断项 + 无法核验项 + **这一次没查成**（查不成时那份清单可能是更早
   * 留下的，不能当成「这一版的结论」）。不再要用户点头——导出永远可点，这批项自动记进
   * 样式检查报告（`forced` / `acknowledged` / `checkFailed`）与请求的 `validation.acknowledged`。
   */
  const hasUnhandled =
    errors.length > 0 || notVerifiable.length > 0 || summary.failed;
  // 有未处理项就**必须**留档：它是「导出时未处理的项」唯一的落点，用户关掉报告开关也不能让它消失
  const reportRequired = hasUnhandled;
  const reportOn = withReport || reportRequired;

  /**
   * 这次导出会不会**照抄源位图**（而不是让引擎重画一张）。
   *
   * 判据里那个 `overrides.length` 不是细节：面板带 override 时后端会先让
   * worker 全质量重渲染一次，**拿到的是一份 PDF**——像素网格不复存在，
   * PPI 重新变得有意义。少了这一条，界面会对着一张即将被重画的图报源像素
   * 网格、还说 PPI 无关（PR #214 第四轮评审）。
   */
  const copiesSourceVerbatim = copiesVerbatimFromRoute({
    scope,
    specIsRaster: availability.spec?.sourceKind === "raster",
    panel,
    assets,
    formats,
    route,
    guardOk: !panel || !artifactValidationIssue(panel),
  });

  /** 透明背景这次起不起作用：要有位图格式，且不是「照抄源位图」那条路 */
  const transparentApplies = raster && !copiesSourceVerbatim;

  /**
   * 位图产物的像素数，显示在对象头部那一行里。**只在选了位图格式时算**（§五）。
   * 出来多少像素**按这次的范围算**：原图范围下拿画布页面尺寸乘一遍是在报另一张
   * 图的数字（一张 70.6mm 的图摆在 180mm 画布上，600ppi 会显示成 4252px，而真实
   * 产物约 1668px）；位图原图更是照抄源像素网格，与 ppi 无关。
   */
  const pixels = raster
    ? pixelPreview(
        scope,
        Number(ppi),
        doc.page,
        availability.spec,
        copiesSourceVerbatim,
      )
    : null;

  /**
   * EPS 这次给不给得出（ADR 0046）。判据在 `epsAvailability()` 一处：画布范围
   * 没有它（画布合成没有 PostScript 写入器），没有脚本的图也没有它。**不隐藏选项**：禁用并
   * 说原因；勾过它的用户切到画布时，请求里自动不带它（`buildExportRequest`）。
   */
  const unreachable = useMemo(
    () => (scope === "canvas" ? [...new Set(unavailablePanels(route, formats))] : []),
    [scope, route, formats],
  );
  const eps = useMemo(
    () => epsAvailability(scope, figureId, route),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scope, figureId, assets, runtimeAssets, route],
  );

  function inputOf(over?: Partial<ExportRequestInput>): ExportRequestInput {
    return {
      scope,
      formats,
      filename,
      ppi: Number(ppi) || PPI_DEFAULT,
      background: transparent && transparentApplies ? "transparent" : "white",
      includeReport: reportOn,
      strictInspection: strict,
      profileId: checkProfileId,
      // 后端的 `acknowledged` 策略 = 「这些阻断项导出时仍未处理」：自动带上，不再靠勾选
      acknowledged: [...new Set(errors.map((i) => i.ruleCode))],
      documentId,
      doc,
      figureId,
      panel,
      spec: availability.spec,
      route,
      ...over,
    };
  }

  /**
   * 「这次能不能导」**只有这一份判断**。
   *
   * 主按钮的 `disabled` 与 `start()` 里的闸读的是同一个值——各写一遍的话，
   * 少写一条的那一侧就成了绕过去的路：第四轮评审那条 P1 是"按钮有闸、
   * `start()` 没有"，第五轮又抓到"两边都有，但 `start()` 那份少了一条"。
   * 一份判断、两个消费点，就没有"少写一条"这回事了。
   */
  const prepared = prepareExport(inputOf());
  const filenameIssue = prepared.filenameProblem;
  const names = prepared.names;
  /**
   * 对象头里「会写出的文件名」：导出进行中取**正在跑的那次请求**（`lastInput` 经同一个
   * `prepareExport`），不取此刻的表单——运行期间文件名 / 格式 / 范围控件仍可改，头部
   * 若跟着变，会说在写 other.svg 而实际在写 Fig1.pdf（#1228 评审 P2）。空闲时取当前表单。
   */
  const runningNames = useMemo(
    () => (running && lastInput ? prepareExport(lastInput).names : null),
    [running, lastInput],
  );

  // 数的是**这次真会发出去的**格式（`names` 由 `buildExportRequest` 来，画布范围下
  // 勾着的 EPS 不在其中）：只勾 EPS 又切到画布，按钮得灰，不能发一个空格式列表
  const canStart =
    !prepared.error &&
    names.length > 0 &&
    !filenameIssue &&
    (scope !== "original" || availability.ok);

  /**
   * 「每张图单独导出」= 一次点击把项目里每张图各导一份：多图排版 + 原图范围。后端一次作业仍只导一张原图，
   * 所以是**依次发多个普通作业**（下面的驱动 effect），每张各自按它自己的原图口径做检查与留档——
   * 走的就是单张导出的那条路，没有第二套。
   */
  const batchMode = !single && scope === "original";
  const canStartBatch = batchMode && canvasFigures.length > 0 && formats.length > 0 && !busy;
  /**
   * 「每张图单独导出」的**冻结**：点「导出」之后分两步，且两步之间没有任何交错——
   * ① 先 await 拿齐每张图的 export route（只取后端的回答，不读文档、不生成请求输入；用户这几秒里改了什么，
   *    ② 都按改后的来）；
   * ② 再在**同一个同步块里**（中间没有任何 await / then / 定时器）对整批图连着调 `buildBatchInput`——
   *    同一个同步块里活 store 不可能被用户或后台（如 `useEngineSync` 校正图幅）改动，整批读的是同一个时刻的状态，
   *    等价于快照，不会混进两个文档版本。route 超时（1.5 s）按退路（无 route）构建，仍在那个同步块里。
   * 之后的发送只读冻结好的输入。表单在冻结段里整块禁用（`fieldset`）。
   */
  const startBatch = () => {
    writeExportDefaults({ formats, dpi: String(ppi), withProof: withReport, strictInspection: strict });
    const figs = canvasFigures;
    if (
      !beginExportBatch(
        figs.map((f) => f.figureId),
        figs.map((f) => f.name),
      )
    )
      return;
    const shared: BatchShared = {
      formats: [...formats],
      ppi: String(ppi),
      transparent,
      strict,
      withReport,
      profileId,
      catalog,
    };
    void freezeBatch(figs, shared);
  };
  const freezeBatch = async (figs: readonly ExportableFigure[], shared: BatchShared) => {
    const alive = () => {
      const b = useExportStore.getState().batch;
      return !!b && !b.done && b.phase === "prepare";
    };
    // ① 只取 route
    const routes = new Map<string, { route: ExportRoute | null; key: string | null }>();
    for (const f of figs) {
      if (!alive()) return;
      const probe = buildBatchInput(f, shared, null);
      let route: ExportRoute | null = null;
      if (probe.ok) {
        route = await Promise.race([
          fetchExportRoute(probe.input).catch(() => null),
          new Promise<null>((r) => setTimeout(() => r(null), 1500)),
        ]);
      }
      routes.set(f.figureId, { route, key: probe.ok ? routeKeyOf(probe.input) : null });
    }
    if (!alive()) return;
    // ② 同一个同步块：下面到 `recordBatchFrozen` 之间没有 await
    const items = figs.map((f, pos) => {
      const got = routes.get(f.figureId);
      let res = buildBatchInput(f, shared, got?.route ?? null);
      // route 回答的是取它那一刻的面板编辑；等待期间编辑变了（键对不上）就按退路再建一次，不拿旧答案配新内容
      if (res.ok && got?.route && routeKeyOf(res.input) !== got.key) res = buildBatchInput(f, shared, null);
      if (res.ok) return { ok: true as const, pos, name: f.name, input: res.input };
      const why = res.reason;
      const reason =
        why.kind === "unavailable"
          ? ex(`scopeUnavailable.${why.code}`)
          : why.kind === "filename"
            ? ex(`filenameError.${why.code}`)
            : formatMessage(engineErrorMsg(why.error as never));
      return { ok: false as const, name: f.name, reason };
    });
    recordBatchFrozen(items);
  };

  /* -------------------------------- 动作 --------------------------------- */
  const applyProfile = (id: string) => {
    // 规范绑定写在当前画布上；检查按的是别的画布时下拉是灰的，这里再挡一道
    if (!checkOnActive) return;
    setProfileId(id);
    writeExportDefaults({ profileId: id });
    const entry = catalog.find((e) => e.id === id);
    if (!entry) return;
    // **跟随的表态跟着项目走，不跟着某一套规范走**
    commit(
      msg("history.setPublicationProfile", undefined, "workspace"),
      (d) => {
        d.profile = bindingFor(entry, {
          journal: doc.profile?.journal,
          follow: doc.profile?.follow,
        });
      },
    );
  };

  const syncProfile = () => {
    if (!checkOnActive) return;
    const entry = catalog.find((e) => e.id === (doc.profile?.id ?? profileId));
    if (!entry) return;
    commit(
      msg("history.syncPublicationProfile", undefined, "workspace"),
      (d) => {
        d.profile = bindingFor(entry, {
          journal: doc.profile?.journal,
          follow: doc.profile?.follow,
        });
      },
    );
  };

  /**
   * 这一张此刻的完整请求输入（含各自按原图口径做的检查与留档）。单张导出的 `start()` 与一批作业的
   * 「冻结」阶段读的是同一份——批的每张在点「导出」那一刻就冻结成这样一份，之后不再读活的表单。
   */
  const buildRequestInput = (overwrite: OverwritePolicy): ExportRequestInput => {
    const report = reportOn
      ? buildProofPayload(
          doc,
          assets,
          [
            // 报告里的条目集合与界面上的摘要裁同一刀（按原图 = 只有那张图的）
            ...(uncheckedOffCanvas
              ? []
              : targetObjectId
              ? rawIssuesForObject(
                  original?.result?.raw ?? rawIssuesFor(checkCanvasId),
                  targetObjectId,
                )
              : rawIssuesFor(checkCanvasId)),
            ...exportContextRaw({ formats, dpi: Number(ppi) }, profile),
          ],
          {
            dpi: Number(ppi),
            formats,
            stem: filename,
            // 原图范围：报告的页面 = 这张图的图幅，不写画布页面与摆放（QA FLAG-B1）
            // 直接交那张图本身：它可能不在当前画布（`doc`）上
            original:
              targetObjectId && panel && originalWidthMm && originalHeightMm
                ? {
                    panel,
                    widthMm: originalWidthMm,
                    heightMm: originalHeightMm,
                  }
                : undefined,
          },
          profile,
          {
            forced: errors.length > 0,
            acknowledged: hasUnhandled
              ? [
                  ...new Set(
                    [...errors, ...notVerifiable].map((i) => i.ruleCode),
                  ),
                ]
              : [],
            // 「这一次没查成，仍然导出了」是**独立的一档**：
            // 只看 forced / acknowledged 的话，它与"干干净净跑过一遍"
            // 在报告里长得一模一样
            checkFailed: summary.failed || !summary.ready,
            acknowledgedCheckFailed:
              (summary.failed || !summary.ready) && hasUnhandled,
          },
        )
      : undefined;
    return inputOf({
      overwrite,
      report: report as Record<string, unknown> | undefined,
    });
  };

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
      if (!canStart) return;
      // 这一回在对话框里起的作业：它跑完就该给完成态脚部（不论 job_id 是否与上一回重号）
      setSettledJobId(null);
      setRevealError(null);
      writeExportDefaults({
        formats,
        dpi: String(ppi),
        withProof: withReport,
        strictInspection: strict,
      });
      await runExport(buildRequestInput(overwrite));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      doc,
      assets,
      formats,
      ppi,
      profile,
      checkProfileId,
      filename,
      errors,
      notVerifiable,
      hasUnhandled,
      reportOn,
      withReport,
      scope,
      transparent,
      strict,
      figureId,
      panel,
      availability.spec,
      canStart,
      targetObjectId,
      uncheckedOffCanvas,
      checkCanvasId,
      original,
      originalWidthMm,
      originalHeightMm,
    ],
  );

  /** 主按钮 / 再次导出 / 重试的同一个入口：一批或单张 */
  const startExport = () => (batchMode ? startBatch() : void start("ask"));

  /** 起作业本身失败的原因（store 里的 `startError`） */
  const sendFailureReason = (): string => {
    const st = useExportStore.getState().startError;
    if (!st) return "";
    return st.code === "bad_filename" ? ex(`filenameError.${st.message}`) : formatMessage(st.message);
  };

  /* ------------------------ 一批作业的驱动（先冻结、再依次发） ------------------------ */
  const launched = useRef<{ ids: string[] | null; index: number }>({ ids: null, index: -1 });
  const lastRecorded = useRef<unknown>(null);
  // 发（send）：只按冻结的输入依次发
  useEffect(() => {
    if (!batch || batch.done || batch.cancelRequested || batch.phase !== "send" || running) return;
    if (launched.current.ids === batch.ids && launched.current.index === batch.index) return; // 已发出：等终局或撞名决定
    const cur = batch.prepared[batch.index];
    if (!cur) return;
    launched.current = { ids: batch.ids, index: batch.index };
    setSettledJobId(null);
    setRevealError(null);
    void runExport(cur.input);
    // 同步路径上就没起来（请求成形被拒）：不会有终局，直接记失败
    if (!useExportStore.getState().running) recordBatchStartFailure(sendFailureReason());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch, running]);
  // 收尾：这一张落了终局（撞名除外——停在冲突条上等用户选覆盖 / 另存，再发出的是新作业）
  useEffect(() => {
    if (!batch || batch.done || batch.phase !== "send" || running) return;
    if (launched.current.ids !== batch.ids || launched.current.index !== batch.index) return;
    // 起作业本身失败了（先校验被拒 / 网络断）：不会有终局，记一笔失败，继续下一张
    if (!storeJob) {
      if (startError) recordBatchStartFailure(sendFailureReason());
      return;
    }
    // 认快照对象而不是 job_id：同一个终局只记一次，不依赖 id 在两张之间一定不同
    if (storeJob.status === "conflict" || storeJob === lastRecorded.current) return;
    lastRecorded.current = storeJob;
    recordBatchJob(storeJob);
  }, [batch, running, storeJob, startError]);
  /** 撞名后的「覆盖 / 另存一份」：一批里重发的是这一张**冻结的**输入，不读活的表单 */
  const restart = (overwrite: OverwritePolicy) => {
    if (batch && !batch.done && batch.phase === "send") {
      const cur = batch.prepared[batch.index];
      if (!cur) return;
      setSettledJobId(null);
      setRevealError(null);
      void runExport({ ...cur.input, overwrite });
      return;
    }
    void start(overwrite);
  };

  /* ------------------------------ 让开与回来 ------------------------------ */
  /** 关掉对话框去别处（定位 / 问题面板），但把填过的东西留下 */
  const park = () => {
    parked.current = {
      documentId,
      scope,
      formats,
      ppi: String(ppi),
      filename,
      filenameTouched,
      withReport,
      transparent,
      strict,
    };
    setOpen(false);
  };

  /** 点芯片 / 「在问题面板中查看」要带过去的目标：摘要是按它算的，面板就得看同一份（Codex #1106 P2） */
  const problemsTarget = { canvasId: checkCanvasId, figureId: targetObjectId ?? null };
  /**
   * 这次导的那张在候选清单里的那一条（名字 = 面板名 / 文件名主干；缩略图的种类与换代）。
   * 素材 / runtime 清单里还没上画布的图也在清单里（`panel === null`）——「选中了对象」
   * 的判据是 `figureId`，不是「文档里有没有它的面板」。
   */
  const figure = figureId
    ? (panel ? figureOfPanel(panel) : (figures.find((f) => f.figureId === figureId) ?? null))
    : null;
  const figureName = figure?.name;

  /** 切换输出范围：用户没碰过文件名的话，默认名跟着换 */
  const changeScope = (next: ExportScope) => {
    setScope(next);
    setPreviewId(null);
    if (!filenameTouched) {
      setFilename(
        next === "original" && figureName
          ? figureName
          : defaultExportName(next, panel, doc),
      );
    }
  };

  /**
   * 在清单里点一张图：意思就是「按它的原图尺寸导」——范围切到原图，不让用户再去
   * 点一次；默认文件名跟着这张图走（用户改过的不动）。
   */
  const pickFigure = (id: string) => {
    setPickedFigureId(id);
    setPreviewId(id);
    setScope("original");
    const f = figures.find((x) => x.figureId === id);
    if (!filenameTouched && f) setFilename(f.name);
  };

  /**
   * 「原图尺寸」这个范围能不能选：项目里**有图可挑**就能选（审计 B20）。
   * 选了之后没有当前图 → 清单展开让用户点一张，主按钮灰着；清单为空才禁用按钮。
   * 「当前这张图此刻导不导得出」是另一个问题（`availability`），它只在原图范围下
   * 说话——画布范围下同时出现「当前画布」与「还没定要导哪一张」是两个状态互相矛盾。
   */
  // 多图排版里「每张图单独导出」只导画布上的图：画布上一张都没有就没得选
  const originalSelectable = single ? figures.length > 0 : canvasFigures.length > 0;

  /**
   * 左栏预览此刻显示什么。单图 = 要导的那张；多图 = 用户点的那一项，没点过就跟着导出内容走
   * （整版 → 页面，每张图单独导出 → 选中的那张）。点缩略图**只换预览**；只有「每张图单独导出」下
   * 点图才同时改要导的那张（后端一次作业只导一张原图，见 `pickFigure`）。
   */
  const shownId: string | null = single
    ? figureId
    : (previewId ?? (scope === "canvas" ? "page" : (figureId ?? "page")));
  const showPreview = (id: string) => {
    if (id !== "page" && scope === "original") pickFigure(id);
    else setPreviewId(id);
  };
  /** 带检查提示的对象（缩略图上的橙点）：当前画布上除建议级以外的问题所指的对象 */
  const flaggedObjectIds = useMemo(() => {
    const ids = new Set<string>();
    for (const i of validationIssues) {
      if (i.severity === "suggestion") continue;
      if (i.objectRef.objectId) ids.add(i.objectRef.objectId);
    }
    return ids;
  }, [validationIssues]);
  const pageFlagged = flaggedObjectIds.size > 0 || errors.length > 0 || notVerifiable.length > 0;

  /**
   * 导出终局报一次状态。**只在终局报一次**（作业 + 终态为键，重复快照不再报），不在每次进度推送上报。
   * 对话框开着（没被设置 / 样式压住）时结果就在眼前的结果块里，不再弹第二份；关着的时候才走通知轨——
   * 失败 / 部分完成 / 等你决定 / 作业丢失带「查看」，点了重开对话框看结果，不自己消失（导出关窗不取消作业，
   * 否则用户以为已经导出了）。
   */
  const announced = useRef<string | null>(null);
  const resultVisible = open && !covered;
  useEffect(() => {
    if (!job) return;
    if (busy) {
      useUiStore.getState().clearStatusOwnedBy(EXPORT_STATUS_OWNER);
      return;
    }
    const key = `${job.job_id}:${job.status}`;
    if (announced.current === key) return;
    announced.current = key;
    if (resultVisible) return;
    const ui = useUiStore.getState();
    const view = {
      label: msg("export.viewResult", undefined, "dialogs"),
      run: () => {
        useUiStore.getState().clearStatusOwnedBy(EXPORT_STATUS_OWNER);
        useUiStore.getState().setExportOpen(true);
      },
    };
    const owner = { owner: EXPORT_STATUS_OWNER, action: view };
    const done = job.outputs.filter((o) => o.status === "done" && o.name);
    if (job.status === "done" && done.length) {
      ui.setStatus(
        msg("export.exported", { files: done.map((o) => o.name).join("、") }, "dialogs"),
        "done",
        { owner: EXPORT_STATUS_OWNER },
      );
    } else if (job.status === "cancelled") {
      ui.setStatus(msg("export.cancelled", undefined, "dialogs"), "info", { owner: EXPORT_STATUS_OWNER });
    } else if (job.status === "failed") {
      ui.setStatus(msg("export.toast.failed", undefined, "dialogs"), "error", owner);
    } else if (job.status === "partial") {
      ui.setStatus(msg("export.toast.partial", undefined, "dialogs"), "error", owner);
    } else if (job.status === "conflict") {
      ui.setStatus(msg("export.toast.conflict", undefined, "dialogs"), "info", owner);
    } else if (job.status === "unknown") {
      ui.setStatus(msg("export.toast.unknown", undefined, "dialogs"), "error", owner);
    }
  }, [job, busy, resultVisible]);

  // 重新打开对话框 = 用户已经看到结果：撤掉关窗期间挂着的那条
  useEffect(() => {
    if (resultVisible) useUiStore.getState().clearStatusOwnedBy(EXPORT_STATUS_OWNER);
  }, [resultVisible]);

  const conflicts = job?.status === "conflict" ? job.conflicts : [];
  /**
   * 导完了（至少一件真的写出来了）：脚部换成「再次导出 · 在文件夹中显示 · 完成」。
   * 之前完成后脚部还挂着「开始导出」，用户再点一下就撞上自己刚写出的同名文件
   * （设计审计 §10.2 P0）——新的一次导出必须是显式的「再次导出」。
   */
  const doneOutputs = job ? job.outputs.filter((o) => o.status === "done" && o.name) : [];
  const finished =
    !!job &&
    !busy &&
    job.job_id !== settledJobId &&
    (job.status === "done" || job.status === "partial") &&
    doneOutputs.length > 0;
  const revealTarget =
    finished && job?.export_dir && canRevealInFileManager() ? doneOutputs[0] : null;
  const jobId = job?.job_id;
  /** 每次「在文件夹中显示」/ 换一次导出都换代：只有最近那一次的结果能改 revealError（Codex #821 P2） */
  const revealSeq = useRef(0);
  useEffect(() => {
    revealSeq.current += 1;
    setRevealError(null);
  }, [jobId]);
  /*
   * 完成态换脚部时「开始导出」那颗按钮被卸掉：焦点要是正停在它上面（键盘按 Enter 导出），
   * 就会摔到 body 上——键盘用户当场失去位置。接到唯一的主动作「完成」上。
   */
  const finishRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!finished) return;
    const active = document.activeElement;
    if (!active || active === document.body) finishRef.current?.focus();
  }, [finished]);
  const revealDone = () => {
    if (!revealTarget || !job?.export_dir) return;
    const dir = job.export_dir;
    const name = revealTarget.name!;
    const seq = ++revealSeq.current;
    void revealExportedFile(dir, name).then((ok) => {
      // 迟到的旧一次（又点了一次 / 已开始新导出）不许覆盖当前状态
      if (seq !== revealSeq.current) return;
      // reveal 失败绝不静默——把完整路径告诉用户
      setRevealError(ok ? null : ex("revealFailed", { path: `${dir}/${name}` }));
    });
  };

  /*
   * 进度 / 冲突 / 拒绝 / 结果：**不在可滚正文的末尾**，而在脚部正上方那块不滚的状态区
   * （`Dialog status`）。正文一长，末尾就在视口外——点了「开始导出」看不见进度，
   * 撞名时看不见那两条出路（设计审计 §10.2 P0）。各种回应一律是 `Notice`（一种说明条，不是七种方言）。
   */
  // 摆有具体原因的那一条（几张都没起来时优先说得出原因的）；全是空原因就摆最后一条
  const batchFailure = batch?.failedStarts.length
    ? (batch.failedStarts.find((f) => f.reason) ?? batch.failedStarts[batch.failedStarts.length - 1])
    : null;
  // 五路失败里此刻要摆的**一条**：最近才出现的那条（见 `useLatestFailure`）
  const failure = useLatestFailure({
    // 拒绝本来就是一句完整的人话（engineErrorMsg）：它自己就是标题
    "refusal": prepared.error
      ? { id: String(prepared.error.reason), title: formatMessage(engineErrorMsg(prepared.error)), text: "" }
      : null,
    // 一批里任何一张没起来（冻结不了 / 预检被拒 / 起作业失败）：照原样渲染 `[data-export-start-error]`，点名哪张 +
    // 那条原因；它同时保留在汇总结果里。`store.startError` 会被后面一张的 `runExport` 复位，所以读批里记下的这份
    "start-error": batchFailure
      ? {
          id: `batch:${batchFailure.name}:${batchFailure.reason}`,
          title: ex("outputFailed", {
            format: batchFailure.name,
            reason: batchFailure.reason || ex("operationFailed", { error: "" }),
          }),
          text: "",
        }
      : startError && !prepared.error
        ? startError.code === "bad_filename"
          ? { id: `bad_filename:${startError.message}`, title: ex(`filenameError.${startError.message}`), text: "" }
          : startFailureRaw(startError.code, startError.message)
        : null,
    "cancel-error": cancelError
      ? { id: JSON.stringify(cancelError), title: ex("cancelFailed"), text: formatMessage(cancelError) }
      : null,
    "poll-error": pollError
      ? { id: JSON.stringify(pollError), title: ex("statusFailed"), text: formatMessage(pollError) }
      : null,
    "reveal-error": revealError ? { id: revealError, title: revealError, text: "" } : null,
  });
  /** 「在文件夹中显示」失败的标题里是完整路径，要能在任意处折行 */
  const failureIsPath = failure?.kind === "reveal-error";
  const statusVisible =
    busy ||
    conflicts.length > 0 ||
    !!failure ||
    (!!job && job.status !== "conflict");
  const statusBlock = statusVisible ? (
    <div data-export-status className="flex flex-col gap-2">
      {busy && <ProgressRow job={running ? storeJob : null} batch={batchActive ? batch : null} />}
      {!!conflicts.length && (
        // 撞名：一句话说清是哪几个文件；两条出路在脚部（另存一份 / 覆盖），不在这里另摆一排小按钮
        <Notice tone="warn" icon={FileExclamationPoint} data-export-conflict>
          {ex("conflict", { files: conflicts.join("、") })}
        </Notice>
      )}
      {/* 失败条同时**最多一条**：几路错误（拒绝 / 启动 / 取消 / 状态 / 在文件夹中显示）后来的替换前一条，不堆叠、不挤正文；
          一句话 + 一个动作，状态码 / 后端 detail 在「详情」里 */}
      {failure && (
        <Notice
          tone="danger"
          data-export-failure={failure.kind}
          {...{ [`data-export-${failure.kind}`]: "" }}
          title={failure.title}
          detailKey={`${failure.kind}\u0000${failure.id}`}
          detail={failure.detail}
          className={failureIsPath ? "break-all" : undefined}
          action={
            failure.kind === "poll-error" ? (
              <Button
                data-export-status-retry
                size="sm"
                disabled={refreshing}
                onClick={() => void refreshExportStatus()}
              >
                {ex("retryStatus")}
              </Button>
            ) : undefined
          }
        >
          {failure.inline}
        </Notice>
      )}
      {job && !busy && job.status !== "conflict" && (
        <ResultBlock job={job} edited={editedDuringExport} onRetry={startExport} />
      )}
    </div>
  ) : null;

  /*
   * 页脚是一台四态的状态机（2026-10-07 设计审计 §10.2）：
   *   idle     [关闭] [开始导出]
   *   running  [关闭] [取消导出]（关闭不取消作业：作业活在 store 里）
   *   conflict [另存一份] [覆盖]（覆盖是破坏性的那一条：危险浅底胶囊；另存一份是安全答案，也是 Esc）
   *   done     [再次导出] …… [在文件夹中显示] [完成]
   */
  const close = () => setOpen(false);
  const footer = finished
    ? {
        // 完成态：再导一次是显式的次要动作（左），唯一的主动作是「完成」
        start: (
          <Button
            variant="ghost"
            size="lg"
            data-export-again
            disabled={batchMode ? !canStartBatch : !canStart}
            onClick={startExport}
          >
            {ex("exportAgain")}
          </Button>
        ),
        secondary: revealTarget ? (
          <Button variant="secondary" size="lg" data-export-reveal onClick={revealDone}>
            {ex("showInFolder")}
          </Button>
        ) : undefined,
        primary: (
          <Button ref={finishRef} variant="primary" size="lg" data-export-finish onClick={close}>
            {ex("finish")}
          </Button>
        ),
      }
    : conflicts.length > 0
      ? {
          start: (
            <Button variant="ghost" size="lg" data-export-conflict-close onClick={close}>
              {translate("actions.cancel")}
            </Button>
          ),
          secondary: (
            <Button variant="secondary" size="lg" data-export-conflict-rename onClick={() => restart("rename")}>
              {ex("conflictRename")}
            </Button>
          ),
          primary: (
            <Button
              variant="danger-tinted"
              size="lg"
              data-export-conflict-replace
              onClick={() => restart("replace")}
            >
              {ex("conflictReplace")}
            </Button>
          ),
        }
      : {
          secondary: (
            <Button variant="secondary" size="lg" onClick={close}>
              {translate("actions.close")}
            </Button>
          ),
          primary: busy ? (
            <Button
              variant="secondary"
              size="lg"
              data-export-cancel
              disabled={!storeJob || cancelState !== "idle"}
              onClick={() => {
                requestBatchCancel();
                void cancelCurrentExport();
              }}
            >
              <X size={ICON_SIZE.sm} />
              {cancelState === "pending"
                ? ex("cancelPending")
                : cancelState === "requested"
                  ? ex("cancelRequested")
                  : ex("cancelExport")}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="lg"
              data-export-start
              disabled={batchMode ? !canStartBatch : !canStart}
              onClick={startExport}
            >
              <Download size={ICON_SIZE.sm} />
              {ex("start")}
            </Button>
          ),
        };

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      // Esc 的安全答案：撞名时 = 另存一份（绝不是覆盖）；其余 = 关（关掉不取消作业）
      onEscape={conflicts.length > 0 ? () => restart("rename") : close}
      title={ex("title")}
      // 对象头（缩略图 · 名字 · 范围 · 尺寸）就是这个对话框的说明：要导的是什么
      description={
        (scope === "canvas" || figure) && (
          <TargetHeader
            scope={scope}
            figure={figure}
            spec={availability.spec}
            doc={doc}
            asset={figureId ? assets[figureId] : undefined}
            // 写不出来的名字不报：文件名不合法、或原图范围下这张图此刻导不出
            files={
              runningNames ??
              (filenameIssue || (scope === "original" && !availability.ok) ? [] : names)
            }
          />
        )
      }
      // 左栏预览 + 右栏表单两列；比 `xl` 再宽一点，右栏的标签 + 下拉才排得下
      width="min(920px, calc(100vw - 32px))"
      covered={covered}
      anchor="export"
      status={statusBlock}
      footer={footer}
    >
      {/* 三组（2026-10-07 设计审计 §10.2）：输出（范围 · 文件名 · 位置）/ 格式（格式 · 分辨率 · 透明背景）/
          检查（规范 · 摘要 + 阻断项 + 一句提示 · 严格核验 · 报告）。「高级选项」拆散进各自所属的组——
          折起来的开关没人看得见，而它们决定的是交出去的文件 */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      <ExportPreviewPane
        single={single}
        fallbackPage={singleFallback}
        doc={doc}
        figures={single ? figures : canvasFigures}
        figureId={figureId}
        shownId={shownId}
        spec={availability.spec}
        transparent={transparent && transparentApplies}
        flaggedObjectIds={flaggedObjectIds}
        pageFlagged={pageFlagged}
        onShow={showPreview}
      />
      {/* 点「导出」之后的冻结阶段（逐张摆好、生成请求输入）里表单整块禁用：这几百毫秒里改动不会只落进一部分图 */}
      <fieldset
        disabled={batchActive && batch?.phase === "prepare"}
        className="m-0 flex min-w-0 flex-col gap-5 border-0 p-0"
      >
        <FormSection title={ex("groupOutput")} data-export-group="output">
          <FieldGroup>
            {/* 0. 范围 —— 一组互斥取值 → `Segmented`（宪法第五节；2026-09-14 审计 B1 / S3）。
                `data-onboarding-anchor`：新手教程的 coachmark 挂在这一组上（Step 5 / 8） */}
            <section aria-label={ex("scopeLabel")} className="flex flex-col gap-2">
              {/* 单图编辑没有「输出范围」：固定按原图尺寸导。多图排版才有「导出内容」 */}
              {!single && (
                <FormRow label={ex("contentLabel")}>
                  <Segmented<ExportScope>
                    value={scope}
                    onChange={changeScope}
                    ariaLabel={ex("contentLabel")}
                    data-onboarding-anchor="export-scope"
                    className="w-auto shrink-0"
                    items={[
                      { value: "canvas", label: ex("contentWhole") },
                      {
                        value: "original",
                        label: ex("contentEach"),
                        disabled: !originalSelectable,
                        title: originalSelectable ? undefined : ex("scopeUnavailable.no_figures"),
                      },
                    ]}
                  />
                </FormRow>
              )}
              <ScopeNote
                scope={scope}
                // 每张图单独导出不用先选一张：「尚未选择要导出的图」那句指引在这一档不成立
                available={availability.ok || (batchMode && availability.reason === "no_figure")}
                reason={availability.reason}
                originalSelectable={originalSelectable}
                ignored={availability.spec?.ignored ?? []}
                fallback={availability.spec?.fallback ?? false}
              />
            </section>

            {batchMode ? (
              <FormRow label={ex("filenameLabel")}>
                <span data-export-batch-names className="type-meta">
                  {ex("batchNames")}
                </span>
              </FormRow>
            ) : (
            // 文件名：默认名跟着导出对象走；校验在**输入的那一刻**就地给出
            <section className="flex flex-col gap-1.5">
              <FormRow label={<label htmlFor="export-filename">{ex("filenameLabel")}</label>}>
                <TextInput
                  id="export-filename"
                  value={filename}
                  onChange={(e) => {
                    setFilenameTouched(true);
                    setFilename(e.target.value);
                  }}
                  placeholder={ex("filenamePlaceholder")}
                  aria-invalid={filenameIssue ? true : undefined}
                  aria-describedby={filenameIssue ? "export-filename-error" : undefined}
                  className="w-full"
                />
              </FormRow>
              {filenameIssue && (
                <FormRow>
                  <p id="export-filename-error" className="text-sm text-danger-content">
                    {ex(`filenameError.${filenameIssue}`)}
                  </p>
                </FormRow>
              )}
            </section>
            )}

            {/* 2. 位置 —— 导出之前就看得见文件会落到哪（审计 §10.2 P0）；改它在设置 › 导出（这里只读） */}
            <FormRow label={ex("locationLabel")}>
              <span
                data-export-location
                className="min-w-0 flex-1 truncate font-mono text-sm text-ink-2"
                title={exportDir ?? undefined}
              >
                {exportDir ? dirTail(exportDir) : ex("locationDefault")}
              </span>
              <Button
                variant="ghost"
                size="sm"
                data-export-location-change
                onClick={() => useUiStore.getState().setSettingsOpen(true, "export")}
              >
                {ex("locationChange")}
              </Button>
            </FormRow>
          </FieldGroup>
        </FormSection>

        <FormSection title={ex("groupFormat")} data-export-group="format">
          <FieldGroup>
            {/* 格式 —— 五颗复选框均分一行（balanced），顺序与 `FORMATS` 同源 */}
            <section className="flex flex-col gap-1.5">
              <fieldset aria-label={ex("formatLabel")} className="m-0 min-w-0 border-0 p-0">
                <FormRow label={ex("formatLabel")}>
                  <div className="grid flex-1 grid-cols-5 items-center gap-2" data-export-formats>
                    <FormatCheck
                      checked={formats.includes("pdf")}
                      onChange={() => toggleFormat("pdf")}
                      format="pdf"
                      hint={ex("pdfHint")}
                    />
                    <FormatCheck
                      checked={formats.includes("png")}
                      onChange={() => toggleFormat("png")}
                      format="png"
                      hint={ex("pngHint")}
                    />
                    {/* EPS 只在「原图 + 有脚本」时给得出：不可用就禁用并说原因，不藏 */}
                    <FormatCheck
                      checked={formats.includes("eps") && eps.ok}
                      onChange={() => toggleFormat("eps")}
                      format="eps"
                      hint={eps.ok ? ex("epsHint") : ex(`epsUnavailable.${eps.reason}`)}
                      disabled={!eps.ok}
                      describedBy={formats.includes("eps") && !eps.ok ? "export-eps-reason" : undefined}
                    />
                    <FormatCheck
                      checked={formats.includes("tiff")}
                      onChange={() => toggleFormat("tiff")}
                      format="tiff"
                      hint={ex("tiffHint")}
                    />
                    <FormatCheck
                      checked={formats.includes("svg")}
                      onChange={() => toggleFormat("svg")}
                      format="svg"
                      hint={ex("svgHint")}
                      describedBy={formats.includes("svg") ? "export-svg-note" : undefined}
                    />
                  </div>
                </FormRow>
              </fieldset>
              {formats.includes("svg") && (
                <FormRow>
                  <p id="export-svg-note" data-export-svg-note className="type-caption">
                    {ex("svgNote")}
                  </p>
                </FormRow>
              )}
              {/* 勾着 EPS 却给不出（切回了画布 / 这张图没有脚本）：原因摆成可见的一行，
                  不只藏在灰掉的复选框的 title 里——一个灰掉的复选框解释不了自己 */}
              {formats.includes("eps") && !eps.ok && (
                <FormRow>
                  <p id="export-eps-reason" className="type-caption">
                    {ex(`epsUnavailable.${eps.reason}`)}
                  </p>
                </FormRow>
              )}
              {/* 画布范围：带编辑的图找不到脚本，后端画不出它——导出必然失败，点之前就说 */}
              {unreachable.length > 0 && (
                <FormRow>
                  <p data-export-route-unavailable className="type-caption">
                    {ex("routeNeedsScript", { files: unreachable.join("、") })}
                  </p>
                </FormRow>
              )}
            </section>

            {/* 分辨率 —— 只在选了位图格式时出现，旁边直接给像素数 */}
            {raster && (
              <FormRow label={ex("ppiLabel")}>
                <Select
                  value={String(ppi)}
                  onChange={setPpi}
                  options={PPI_VALUES.map((v) => ({
                    value: v,
                    label: translate("measure.ppi", { value: v }),
                  }))}
                  ariaLabel={ex("ppiSelectLabel")}
                  className="w-56"
                />
                {pixels && (
                  <span id="export-pixel-preview" className="type-meta min-w-0 truncate tabular-nums">
                    {pixels}
                  </span>
                )}
              </FormRow>
            )}

            {/* 透明背景只在「有位图格式」且**不是照抄源文件**的那条路上有意义。
                原图 + 位图源出来的就是那张图本身（我们只换容器不换像素），
                背景是它自己的——开着一个不起作用的开关就是说了而不做 */}
            <div className="flex flex-col gap-1">
              <label
                className={cn(
                  "flex min-h-7 items-center justify-between gap-4",
                  transparentApplies ? "text-ink" : "text-ink-3",
                )}
              >
                <span>{ex("transparent")}</span>
                <Toggle
                  aria-label={ex("transparent")}
                  checked={transparent && transparentApplies}
                  onChange={setTransparent}
                  disabled={!transparentApplies}
                />
              </label>
              {raster && !transparentApplies && (
                <p className="type-caption">{ex("transparentNotForRaster")}</p>
              )}
            </div>
          </FieldGroup>
        </FormSection>

        {/* 检查 —— 规范 · 摘要行 + 阻断项清单 + 一句提示 · 严格核验 · 报告；
            摘要按导出目标取范围，完整清单在左侧问题面板（§四） */}
        <FormSection title={ex("groupChecks")} data-export-group="checks">
          <FieldGroup>
            <section className="flex flex-col gap-1.5">
              <FormRow label={ex("profileLabel")}>
                <Select
                  value={checkProfileId}
                  onChange={applyProfile}
                  disabled={!checkOnActive}
                  options={catalog.map((p) => ({
                    value: p.id,
                    label: p.display_name,
                  }))}
                  ariaLabel={ex("profileAria")}
                  /* 同一张表单里的下拉只有一种宽（全面打磨 D40） */
                  className="w-56"
                />
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={ex("profileEditAria")}
                  onClick={() => {
                    useUiStore.getState().setSettingsOpen(true, "spec");
                  }}
                >
                  <Pencil size={ICON_SIZE.sm} aria-hidden />
                  {ex("profileEdit")}
                </Button>
              </FormRow>
              {/* 图在别的画布上：显示的是那张画布的规范，改它得先切过去（Codex 评审 #596 P1） */}
              {checkCanvasName !== null && (
                <FormRow>
                  <p data-export-profile-canvas className="text-sm text-ink-2">
                    {ex("profileOtherCanvas", { canvas: checkCanvasName })}
                  </p>
                </FormRow>
              )}
              {/* 规范异常提示不藏起来 */}
              {resolved.updateAvailable && checkOnActive && (
                <FormRow>
                  <p className="flex flex-wrap items-center gap-2 text-sm text-ink-2">
                    {ex("profileUpdateAvailable")}
                    <Button variant="secondary" size="sm" onClick={syncProfile}>
                      {ex("profileSync")}
                    </Button>
                  </p>
                </FormRow>
              )}
            </section>

            <section aria-label={ex("checkLabel")} className="flex flex-col gap-2">
              <FormRow label={ex("checkLabel")}>
                <CheckRow
                  summary={summary}
                  specName={checkSpecName}
                  unchecked={uncheckedOffCanvas}
                  scopeHint={
                    uncheckedOffCanvas
                      ? ex("checkScopeOffCanvas", {
                          name: figure?.name ?? defaultExportName(scope, panel, doc),
                        })
                      : scope === "original" && panel
                      ? ex("checkScopeFigureNamed", {
                          name: figure?.name ?? defaultExportName(scope, panel, doc),
                        })
                      : ex("checkScopeCanvasNamed", { name: doc.name })
                  }
                  onOpenPanel={(opts) => {
                    park();
                    openProblems({ severities: opts?.severities, target: problemsTarget });
                  }}
                />
              </FormRow>

            </section>

            {/* 严格核验（ADR 0068）：按这套规范的阈值重新打开封口的文件量事实，
                必需项失败**或无法核验**的那一项不发布。standard 是缺省，不勾就不发这个键 */}
            <label className="flex min-h-7 items-center justify-between gap-4 text-ink" title={ex("strictTitle")}>
              <span>{ex("strictToggle")}</span>
              <Toggle aria-label={ex("strictToggle")} checked={strict} onChange={setStrict} />
            </label>
            <label className="flex min-h-7 items-center justify-between gap-4 text-ink" title={ex("reportTitle")}>
              <span>{ex("reportToggle")}</span>
              <Toggle
                aria-label={ex("reportToggle")}
                checked={reportOn}
                onChange={setWithReport}
                disabled={reportRequired}
              />
            </label>
          </FieldGroup>
        </FormSection>
      </fieldset>
      </div>
    </Dialog>
  );


  function toggleFormat(f: string) {
    setFormats((prev) =>
      prev.includes(f) ? prev.filter((v) => v !== f) : [...prev, f],
    );
  }
}

/* --------------------------------- 子组件 ---------------------------------- */

/** `useFigureThumbSource` 的结果：清单行与对象头共用，取图只有这一处 */
interface FigureThumbSource {
  svgMarkup: { __html: string } | undefined;
  src: string | null;
  /** 图幅宽高比（读不到时 4:3） */
  ratio: number;
}

/**
 * 一张图缩略图的取图逻辑——**左栏预览（`ExportPreviewPane`）与原图范围的对象头（`TargetHeader`）
 * 共用这一份**，不各起一套：原图范围导出的就是「带图内修改重渲」的那一版
 * （`_export_produce_original` → `_resolve_panel_source` 带 overrides），所以缩略图也得是
 * 同一版；头部若读磁盘原图而清单读编辑后，同屏会出现两个不一致的「这张图」。
 */
function useFigureThumbSource(
  figure: ExportableFigure,
  bucket: number,
): FigureThumbSource {
  const render = usePanelRender(figure.panel);
  const tracked = useRenderStore((s) => !!s.tracked[figure.figureId]);
  const panel = figure.panel;
  const needsEngine =
    !!panel &&
    ((panel.overrides.length > 0 && !isJustBakedBaseline(panel)) ||
      tracked ||
      figure.kind === "runtime");
  const svg = needsEngine ? (render?.svg ?? null) : null;
  // 同一份字符串复用同一个 `{__html}`：缩略格随对话框重渲时不必把整张 SVG 重新解析一遍
  const svgMarkup = useHtmlMarkup(svg);
  const src = figureThumbSrc(figure, bucket);
  const sizeMm = render?.manifest?.size_mm ?? figure.sizeMm;
  const ratio =
    sizeMm && sizeMm[0] > 0 && sizeMm[1] > 0 ? sizeMm[0] / sizeMm[1] : 4 / 3;
  return { svgMarkup: svg ? svgMarkup : undefined, src, ratio };
}

/**
 * 缩略图本体：按**真实图幅比例**定盒，再放进调用方给的固定外框里（外框尺寸不变、不溢出）。
 * `boxRatio` = 外框的宽高比，用来判断该贴宽还是贴高。
 */
function FigureThumbArt({
  source,
  boxRatio,
  ratio,
}: {
  source: FigureThumbSource;
  boxRatio: number;
  /** 覆盖图幅比例（对象头用 `OriginalOutputSpec` 的最终图幅） */
  ratio?: number;
}) {
  const r = ratio ?? source.ratio;
  const fit = {
    aspectRatio: String(r),
    ...(r >= boxRatio ? { width: "100%" } : { height: "100%" }),
    maxWidth: "100%",
    maxHeight: "100%",
  };
  if (source.svgMarkup) {
    // store 里的 SVG 已被 `prepareSvg` 改成 width/height 100%，得给它一个
    // 按图幅比例定好的盒子，否则会被拉成缩略格的形状
    return (
      <span
        data-export-thumb="svg"
        className="block"
        style={fit}
        dangerouslySetInnerHTML={source.svgMarkup}
      />
    );
  }
  if (source.src) {
    return (
      <span className="block" style={fit}>
        <RetryImg
          data-export-thumb="file"
          src={source.src}
          alt=""
          draggable={false}
          className="h-full w-full object-contain"
        />
      </span>
    );
  }
  return <span data-export-thumb="none" className="h-full w-full bg-surface-2" />;
}

/**
 * 左栏预览（两档共用，取图复用对象头 / 清单行同一份 `useFigureThumbSource`，不新起渲染）：
 *
 * * 单图：导出后的样子——真实比例的大预览 + mm 尺寸；
 * * 多图：顶部缩略图条「整版 / 每张图」，有检查提示的缩略图带橙点，下面是选中那一项的大预览。
 *
 * 点缩略图只是换预览（`onShow`）；是否同时改要导的那张由对话框按导出内容决定。
 * 预览不是最终文件（位图格式的像素、图内修改的合成不在这里），沿用 `thumbNote` 的克制说明。
 */
function ExportPreviewPane({
  single,
  fallbackPage,
  doc,
  figures,
  figureId,
  shownId,
  spec,
  transparent,
  flaggedObjectIds,
  pageFlagged,
  onShow,
}: {
  single: boolean;
  /** 单图档原图口径不可用、改按画布导：预览显示导出的页面 */
  fallbackPage: boolean;
  doc: FigureDocument;
  figures: readonly ExportableFigure[];
  /** 此刻要导的那张 */
  figureId: string | null;
  /** 预览显示的项："page" = 整版，否则 figureId；null = 没有可显示的 */
  shownId: string | null;
  /** 要导的那张的最终图幅（只对 `figureId` 那张成立） */
  spec: OriginalOutputSpec | null;
  transparent: boolean;
  flaggedObjectIds: ReadonlySet<string>;
  pageFlagged: boolean;
  onShow: (id: string) => void;
}) {
  useTranslation("dialogs");
  const shownFigure =
    shownId && shownId !== "page" ? (figures.find((f) => f.figureId === shownId) ?? null) : null;
  const showPage = fallbackPage || (!single && !shownFigure);
  const specMm =
    shownFigure && shownFigure.figureId === figureId && spec && !spec.fallback
      ? ([spec.widthMm, spec.heightMm] as const)
      : null;
  const sizeMm = showPage
    ? ([doc.page.w, doc.page.h] as const)
    : (specMm ?? shownFigure?.sizeMm ?? null);
  const title = showPage ? doc.name : (shownFigure?.name ?? ex("targetNoFigure"));
  const flagged = (f: ExportableFigure) => !!f.panel && flaggedObjectIds.has(f.panel.id);
  return (
    <div data-export-preview className="flex min-w-0 flex-col gap-3 md:sticky md:top-0 md:self-start">
      {!single && (
        <div
          role="tablist"
          aria-label={ex("previewTabs")}
          data-export-preview-tabs
          className="flex max-w-full gap-2 overflow-x-auto pb-1"
        >
          <PreviewTab
            id="page"
            label={ex("previewWhole")}
            selected={showPage}
            flagged={pageFlagged}
            onShow={onShow}
          >
            <CanvasThumb page={doc.page} objects={doc.objects} className="h-full w-full" />
          </PreviewTab>
          {figures.map((f) => (
            <PreviewTab
              key={f.figureId}
              id={f.figureId}
              label={f.name}
              selected={shownFigure?.figureId === f.figureId}
              flagged={flagged(f)}
              onShow={onShow}
            >
              <PreviewTabArt figure={f} />
            </PreviewTab>
          ))}
        </div>
      )}
      <div
        data-export-preview-stage
        className="flex h-[260px] items-center justify-center overflow-hidden rounded-md border border-border bg-surface-2 p-3"
      >
        {showPage ? (
          <CanvasThumb
            page={doc.page}
            objects={doc.objects}
            className="h-full w-full"
            transparent={transparent}
          />
        ) : shownFigure ? (
          <PreviewStageArt figure={shownFigure} ratio={sizeMm ? sizeMm[0] / sizeMm[1] : undefined} />
        ) : (
          <ImageOff size={ICON_SIZE.md} className="text-ink-3" aria-hidden />
        )}
      </div>
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-ink" title={title}>
          {title}
        </p>
        <p data-export-preview-size className="type-meta tabular-nums">
          {sizeMm ? ex("mmSize", { w: round1(sizeMm[0]), h: round1(sizeMm[1]) }) : ex("sizeUnknownShort")}
        </p>
      </div>
    </div>
  );
}

function PreviewTab({
  id,
  label,
  selected,
  flagged,
  onShow,
  children,
}: {
  id: string;
  label: string;
  selected: boolean;
  flagged: boolean;
  onShow: (id: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      title={label}
      data-export-preview-tab={id}
      data-export-figure={id === "page" ? undefined : id}
      data-flagged={flagged ? "" : undefined}
      onClick={() => onShow(id)}
      className={cn(
        "relative flex w-[64px] shrink-0 flex-col items-center gap-1 rounded-md p-1 outline-none focus-visible:focus-ring",
        selected ? "bg-selected ring-1 ring-inset ring-border" : "hover:bg-surface-hover",
      )}
    >
      <span className="flex h-10 w-full items-center justify-center overflow-hidden rounded-xs bg-paper ring-1 ring-inset ring-border">
        {children}
      </span>
      <span className="w-full truncate text-center text-xs text-ink-2">{label}</span>
      {flagged && (
        <span
          data-export-flag
          role="img"
          aria-label={translate("export.previewFlagged", { ns: "dialogs" })}
          className="absolute right-1 top-1 h-2 w-2 rounded-full bg-warn"
        />
      )}
    </button>
  );
}

function PreviewTabArt({ figure }: { figure: ExportableFigure }) {
  const source = useFigureThumbSource(figure, 160);
  return <FigureThumbArt source={source} boxRatio={56 / 40} />;
}

/** 大预览：按真实图幅比例定盒（`ratio` 优先，其次素材自报 / 渲染清单里的比例），贴在 260 高的舞台里 */
function PreviewStageArt({ figure, ratio }: { figure: ExportableFigure; ratio: number | undefined }) {
  const source = useFigureThumbSource(figure, 480);
  return (
    <div className="flex h-full w-full items-center justify-center rounded-xs bg-paper">
      <FigureThumbArt source={source} boxRatio={4 / 3} ratio={ratio} />
    </div>
  );
}

/**
 * 范围说明。**不可用时说出原因，不隐藏选项、不静默改成画布**（§五）。
 *
 * 原图范围下还要把「画布上设了、这次不套用」的变换逐项说出来：
 * 忽略而不说等于骗人（ADR 0028）。尺寸**不在这里**——它只在对象头部出现一次。
 */
function ScopeNote({
  scope,
  available,
  reason,
  originalSelectable,
  ignored,
  fallback,
}: {
  scope: ExportScope;
  available: boolean;
  reason: string;
  /** 「原图尺寸」按钮此刻能不能选（项目里有没有图可挑） */
  originalSelectable: boolean;
  ignored: readonly string[];
  fallback: boolean;
}) {
  useTranslation("dialogs");
  // 「按画布尺寸出图」「按这张图自己的尺寸出图」这两句只是把选中的范围再说一遍
  // （2026-09-14 审计 B4：三句解释同一件事）——删掉；留下的是范围本身说不出的事实：
  // 画布上的哪些变换不带进导出、磁盘原件与图幅不一致、尺寸读不到用了占位值、为什么灰
  // 没话说就不渲染这一行（连它占的 gap 一起消失）。此前靠 `:empty` 选择器把空行藏掉，
  // 而 `<input>` 也是「空元素」——把「文件名」整行一起藏了（2026-09-15 审计 D01）
  const hasNote =
    scope === "canvas"
      ? !originalSelectable
      : !available || fallback || ignored.length > 0;
  if (!hasNote) return null;
  if (scope === "canvas") {
    return (
      <FormRow>
        <div className="type-caption flex flex-col gap-0.5">
          {/* 「原图尺寸」灰着时说一句为什么——一个禁用的按钮解释不了自己（§五：不隐藏
              选项）。这是一句说明，不是错误：用户此刻导的是画布，什么都没挡着他 */}
          <span>{ex("scopeUnavailable.no_figures")}</span>
        </div>
      </FormRow>
    );
  }
  return (
    <FormRow>
      <div className="type-caption flex flex-col gap-0.5">
        {/* 原图范围下当前这张导不出：说出原因。「还没定要导哪一张」是一句指引
          （下面的清单就是给这个的），不是错误；源文件不见了 / 找不到这张图才是 */}
        {!available &&
          (reason === "no_figure" ? (
            <span className="text-ink-2">
              {ex("scopeUnavailable.no_figure")}
            </span>
          ) : (
            <span className="flex items-start gap-1.5 text-danger-content">
              <TriangleAlert
                size={ICON_SIZE.xs}
                className="mt-0.5 shrink-0"
                aria-hidden
              />
              {/* 三个原因各说各的话——折成两句的话「源文件不见了」会被说成
                「先选中一张图」，用户照做之后按钮还是灰的 */}
              {ex(`scopeUnavailable.${reason}`)}
            </span>
          ))}
        {available && (
          <>
            {fallback && (
              <span className="text-warn-content">{ex("scopeOriginalFallback")}</span>
            )}
            {ignored.length > 0 && (
              <span>
                {ex("scopeIgnored", {
                  list: ignored.map((k) => ex(`ignored.${k}`)).join("、"),
                })}
              </span>
            )}
          </>
        )}
      </div>
    </FormRow>
  );
}

/** 尺寸比较的容差（mm），与 `originalSpec` 的图幅同步同一档 */
const SIZE_EPS = 0.05;

/**
 * 磁盘原件与图幅不一致时多说一句（审计 T33 的 80×57.6 vs 75.3×58.7）。
 *
 * 图幅按脚本 savefig 的裁切框之后（ADR 0098）两者通常一致；不一致的是升级前按 figsize 保留
 * 的排版、或原件出自别的机器 / 别的 matplotlib。判据：素材是矢量源，且它的 `logical_w_mm` /
 * `logical_h_mm` 与图幅差过 0.05 mm。位图源没有这回事；图幅还是占位值时另有一句
 * 醒目的警告（`ScopeNote`），这里不重复。
 */
export function diskSizeNote(
  spec: OriginalOutputSpec,
  asset:
    | Pick<AssetOriginalSpec, "source_kind" | "logical_w_mm" | "logical_h_mm">
    | null
    | undefined,
): string | null {
  if (spec.fallback || !asset || asset.source_kind !== "vector") return null;
  const differs =
    Math.abs(asset.logical_w_mm - spec.widthMm) > SIZE_EPS ||
    Math.abs(asset.logical_h_mm - spec.heightMm) > SIZE_EPS;
  if (!differs) return null;
  return ex("sizeDiskDiffers", {
    dw: round1(asset.logical_w_mm),
    dh: round1(asset.logical_h_mm),
    w: round1(spec.widthMm),
    h: round1(spec.heightMm),
  });
}

/**
 * 导出对象头部：缩略图 · 名字 · 范围 · 最终尺寸。**尺寸只在这里出现一次**
 * （原图 = `OriginalOutputSpec`，画布 = 页面尺寸），别处只说话不报数。
 *
 * 缩略图不新起渲染：磁盘素材走现成的 `/api/render` 分档缩略图（素材库同一张），
 * runtime 素材走 materialized cache 预览；画布范围复用 `CanvasThumb`（画布列表 /
 * 版本列表同一张：按页面比例摆真实内容），不合成整页。
 */
function TargetHeader({
  scope,
  figure,
  spec,
  doc,
  asset,
  files,
}: {
  scope: ExportScope;
  /** 原图范围下这次导的那张（候选清单里的那一条；还没上画布的素材 `panel` 为 null 也是它） */
  figure: ExportableFigure | null;
  spec: OriginalOutputSpec | null;
  doc: FigureDocument;
  /** 素材清单里的那一条（缩略图换代的 mtime + 磁盘原件的尺寸）；不在清单里就是 undefined */
  asset: PanelInfo | undefined;
  /**
   * 这次会写出的文件名（含扩展名，也就是勾选的格式）。**直接是 `prepareExport().names`**
   * ——`buildExportRequest` 发给后端的同一份，这里不自己拼、不自己剥扩展名。
   * 文件名不合法 / 没有可导的格式时调用方传空数组：不报一个写不出来的名字。
   */
  files: readonly string[];
}) {
  useTranslation("dialogs");
  const original = scope === "original";
  const name = original
    ? figure
      ? figure.name
      : ex("targetNoFigure")
    : doc.name;
  const size = original
    ? spec && !spec.fallback
      ? ex("mmSize", { w: round1(spec.widthMm), h: round1(spec.heightMm) })
      : ex("sizeUnknownShort")
    : ex("mmSize", { w: round1(doc.page.w), h: round1(doc.page.h) });
  const originNote =
    original && spec ? diskSizeNote(spec, asset?.original_spec) : null;
  const visibleObjects = doc.objects.filter((o) => !o.hidden).length;
  return (
    <div data-export-target className="flex items-center gap-3">
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium text-ink" title={name}>
          {name}
        </p>
        <p className="type-meta">
          {ex(original ? "scopeOriginal" : "scopeCanvas")}
          {" · "}
          <span className="tabular-nums">{size}</span>
          {!original && (
            <>
              {" · "}
              {ex("canvasObjects", { count: visibleObjects })}
            </>
          )}
        </p>
        {originNote && <p className="type-caption">{originNote}</p>}
        {files.length > 0 && (
          <p
            data-export-target-files
            className="type-meta truncate"
            title={files.join(" · ")}
          >
            {files.join(" · ")}
          </p>
        )}
        {/* 缩略图不是最终文件（画布缩略图不含图内修改与页面合成、位图格式的像素也不在这里），
            说一句，克制：中性灰，不是警告 */}
        <p data-export-thumb-note className="type-caption text-ink-3">
          {ex("thumbNote")}
        </p>
      </div>
    </div>
  );
}

const round1 = (v: number) => Math.round(v * 10) / 10;

/**
 * 检查摘要。**只消费统一检查服务的结果**（ADR 0030）——不跑第二遍求值器，
 * 也不在这里列第二套清单（§四）。
 */
function CheckRow({
  summary,
  specName,
  scopeHint,
  unchecked,
  onOpenPanel,
}: {
  summary: ValidationSummary;
  /** 这次导出没有可检查的对象（原图不在画布上）：只说没检查，不报任何画布结论 */
  unchecked?: boolean;
  /** 按哪套规范查的：没查出问题时要说清是「按它没查出」，不是「合规」 */
  specName: string;
  /** 这份摘要算的是哪个范围（只算这张图 / 整个画布） */
  scopeHint: string;
  /**
   * 带名字的 options 对象，不是裸的 `severities?`：`onClick={onOpenPanel}` 时 React 的 click event 会被当成
   * 第一个参数——裸数组签名下它被存进 problemFilter、面板一打开就崩（#1106 P2）；对象签名下 event 过不了类型检查。
   */
  onOpenPanel: (opts?: { severities: Severity[] }) => void;
}) {
  useTranslation(["dialogs", "errors"]);
  if (unchecked) {
    return (
      <div className="flex items-center justify-between gap-3">
        <span
          data-export-check-scope
          className="flex min-w-0 flex-1 items-center gap-2 text-sm text-ink-2"
        >
          <TriangleAlert size={ICON_SIZE.sm} className="shrink-0" aria-hidden />
          {scopeHint}
        </span>
      </div>
    );
  }
  // 「查不了」与「没问题」是两个答案。压成一个 = 用户带着一屏静悄悄的绿投稿
  if (summary.failed || !summary.ready) {
    return (
      <div className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 flex-1 items-center gap-2 text-sm text-danger">
          <TriangleAlert size={ICON_SIZE.sm} className="shrink-0" aria-hidden />
          {ex(summary.total ? "preflightFailedKept" : "preflightFailed")}
        </span>
        <OpenProblems onOpen={() => onOpenPanel()} />
      </div>
    );
  }
  if (summary.total === 0) {
    // 通过态用中性图标，不做绿色横幅：这只是「按这套规范没查出问题」，不是「合规」——
    // 说出按哪套规范、查的是哪个范围，再用一行小字说清检查覆盖到哪（识别不出的内容不在其中）
    return (
      <div data-export-check-ok className="flex min-w-0 flex-col gap-0.5">
        <span className="flex min-w-0 items-center gap-2 text-sm text-ink">
          <Check
            size={ICON_SIZE.sm}
            className="shrink-0 text-ink-2"
            aria-hidden
          />
          <span className="min-w-0">
            {ex("preflightOk", { spec: specName })}
            <span data-export-check-scope className="text-xs text-ink-3">{` · ${scopeHint}`}</span>
          </span>
        </span>
        <span data-export-check-coverage className="pl-6 text-xs text-ink-3">
          {ex("preflightCoverage")}
        </span>
      </div>
    );
  }
  // 有问题：三个（至多四个）计数芯片 + 这份摘要算的是哪个范围 + 问题面板入口。
  // 阻断项由下方的清单逐条列出，完整清单归左侧问题面板（§四）——数量这一行不能省：
  // 只有警告时没有它，用户看到的就是一片空白，既不知道有警告、也没有去问题面板的路。
  // 颜色只在圆点上：要改 = danger、留意 = warn，建议 / 无法核验永远是灰。点芯片 = 带着这一档去问题面板。
  const CHIPS: { sev: Severity; tone: CountTone; key: string }[] = [
    { sev: "error", tone: "danger", key: "chipError" },
    { sev: "warn", tone: "warn", key: "chipWarn" },
    { sev: "not_verifiable", tone: "quiet", key: "chipNotVerifiable" },
    { sev: "suggestion", tone: "quiet", key: "chipSuggestion" },
  ];
  return (
    <div className="flex items-center justify-between gap-3">
      {/* 范围（整个画布 / 仅此图）不再是一段内联灰字：悬停说明，读屏照读 */}
      <span data-export-check-scope title={scopeHint} className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
        {CHIPS.filter((c) => summary.counts[c.sev] > 0).map((c) => (
          <CountChip
            key={c.sev}
            tone={c.tone}
            count={summary.counts[c.sev]}
            label={ex(c.key, { count: summary.counts[c.sev] })}
            onClick={() => onOpenPanel({ severities: [c.sev] })}
          />
        ))}
        <span className="sr-only">{scopeHint}</span>
      </span>
      <OpenProblems onOpen={() => onOpenPanel()} />
    </div>
  );
}

/** `onOpen` 不收参数，内部一律无参调用——不把 click event 递给上游那些带可选参数的回调 */
function OpenProblems({ onOpen }: { onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen()}
      className="shrink-0 rounded-md text-sm text-ink-2 outline-none hover:underline focus-visible:focus-ring"
    >
      {ex("openProblems")}
    </button>
  );
}

/**
 * 进度：阶段句 + 一根扫动的细进度条（`ProgressBar` 不定态，与更新下载 / 包管理同一份；
 * 阶段数是粗粒度的，拿它当百分比是假精确）。屏幕阅读器读得到阶段（§十）
 */
function ProgressRow({ job, batch }: { job: ExportJob | null; batch: ExportBatch | null }) {
  useTranslation("dialogs");
  const phase = job?.progress?.phase ?? "preparing";
  const step = job?.progress?.step ?? 0;
  const total = job?.progress?.total ?? 1;
  // 一批：先说第几张（k/N · 图名），再说这一张走到哪一步
  const text = batch
    ? ex("batchProgress", {
        // 冻结阶段数的是正在冻结的那张；发的阶段数的是冻结好的第几张在 `ids` 里的位置
        k: Math.min((batch.phase === "send" ? (batch.prepared[batch.index]?.pos ?? batch.index) : 0) + 1, batch.ids.length),
        n: batch.ids.length,
        name:
          (batch.phase === "send" ? batch.prepared[batch.index]?.name : batch.names[0]) ?? "",
        phase: ex(`phase.${phase}`, { step, total }),
      })
    : ex(`phase.${phase}`, { step, total });
  return (
    <div data-export-progress className="flex flex-col gap-1.5">
      <p role="status" aria-live="polite" className="text-shimmer text-sm text-ink-2">
        {text}
      </p>
      <ProgressBar pct={null} label={text} />
    </div>
  );
}

/**
 * 结果。**逐项显示**（§九）：一次请求要 PDF+PNG 而 PNG 挂了，PDF 照常在
 * 这里可点，那一行 PNG 说出自己为什么没出来——不许把部分成功报成全部成功。
 */
export function ResultBlock({
  job,
  edited,
  onRetry,
}: {
  job: ExportJob;
  edited: boolean;
  onRetry: () => void;
}) {
  useTranslation(["dialogs", "errors"]);
  if (job.status === "cancelled") {
    return <Notice tone="neutral">{ex("cancelledNote")}</Notice>;
  }
  if (job.status === "unknown") {
    // 后端重启 / 作业过期。**这与"失败"是两件事**：我们不知道那些文件写出来
    // 没有，所以既不说"已保存到"，也不说"导出失败"
    return (
      <Notice
        tone="warn"
        data-export-job-lost
        action={
          <Button size="sm" onClick={onRetry}>
            {ex("retry")}
          </Button>
        }
      >
        {ex("jobLost")}
      </Notice>
    );
  }
  if (job.status === "failed" && !job.outputs.length) {
    return (
      <Notice
        tone="danger"
        data-export-failed
        action={
          job.error?.recoverable !== false ? (
            <Button size="sm" onClick={onRetry}>
              {ex("retry")}
            </Button>
          ) : (
            // 没有可执行的修复动作：发送问题反馈就是这张卡的主按钮（开关关着时不渲染）
            <SendReportButton variant="primary" />
          )
        }
      >
        {translate(`backend.${job.error?.code ?? "export_failed"}`, {
          ns: "errors",
          ...(job.error?.params ?? {}),
          defaultValue: ex("operationFailed", {
            error: job.error?.code ?? "",
          }),
        })}
        {/* 那一次的诊断（T04）：折叠，主按钮仍是重试；key 绑作业，重试出新作业就是新的一份 */}
        {job.job_id && <TaskDiagnostic key={job.job_id} kind="export" refId={job.job_id} />}
      </Notice>
    );
  }
  return (
    <Card appearance="subtle" padding="sm" data-export-result className="flex flex-col gap-1 px-3">
      <p className="break-all text-sm text-ink-3">
        {ex("savedTo", {
          dir:
            job.export_dir ??
            useProjectStore.getState().project?.export_dir ??
            "exports/",
        })}
      </p>
      {/* 部分失败：先一句汇总（数字就是下面逐行的计数，来自后端回执的 outputs，不另算） */}
      {job.status === "partial" && (
        <p data-export-partial-summary className="text-sm font-medium text-ink">
          {ex("partialSummary", {
            ok: job.outputs.filter(outputSaved).length,
            failed: job.outputs.filter((o) => !outputSaved(o)).length,
          })}
        </p>
      )}
      {job.outputs.map((o) => (
        <OutputRow
          key={`${o.format}-${o.name ?? "x"}`}
          out={o}
          dir={job.export_dir ?? ""}
        />
      ))}
      {edited && (
        <p className="mt-1 text-sm text-warn-content">{ex("editedDuringExport")}</p>
      )}
      {/* 部分失败 / 每一件产物都失败（此时 outputs 非空，进不了上面的无产物分支）：同一个折叠入口（T04）；全部成功时不出现 */}
      {(job.status === "partial" || job.status === "failed") && job.job_id && (
        <TaskDiagnostic key={job.job_id} kind="export" refId={job.job_id} />
      )}
      {/* 引擎重渲染的警告：图已经出来了，但可能与画布不完全一致
          （元素不存在 = 脚本改过了）。不吞——用户投出去之前得知道 */}
      {!!job.warnings?.length && (
        <div className="mt-1 flex flex-col gap-0.5 border-t border-border pt-1">
          <p className="text-sm text-ink-2">{ex("warningsIntro")}</p>
          {job.warnings.map((w) => (
            <p key={w} className="break-all text-sm text-ink-3">
              {w}
            </p>
          ))}
        </div>
      )}
    </Card>
  );
}

/** 这一件产物已被后端确认写出（`OutputRow` 画成功行的同一条判据） */
const outputSaved = (o: ExportOutput) => o.status !== "failed" && !!o.name;

function OutputRow({ out, dir }: { out: ExportOutput; dir: string }) {
  useTranslation(["dialogs", "errors"]);
  const [revealError, setRevealError] = useState<string | null>(null);
  if (out.status === "failed" || !out.name) {
    return (
      <p className="flex items-start gap-1.5 text-sm text-danger-content">
        <TriangleAlert
          size={ICON_SIZE.xs}
          className="mt-0.5 shrink-0"
          aria-hidden
        />
        {ex("outputFailed", {
          // 一批里根本没起来的那张没有格式，点名用图名
          format: out.format ? out.format.toUpperCase() : (out.name ?? ""),
          reason: translate(`backend.${out.error?.code ?? "format_failed"}`, {
            ns: "errors",
            ...(out.error?.params ?? {}),
            defaultValue: out.error?.code ?? "",
          }),
        })}
      </p>
    );
  }
  const dims =
    out.dimensions.px?.[0] && out.dimensions.px?.[1]
      ? translate("measure.pxSize", {
          w: out.dimensions.px[0],
          h: out.dimensions.px[1],
        })
      : out.dimensions.mm?.[0] && out.dimensions.mm?.[1]
        ? ex("mmSize", {
            w: round1(out.dimensions.mm[0]),
            h: round1(out.dimensions.mm[1]),
          })
        : "";
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-baseline gap-2">
        {isDesktop() ? (
          // 桌面里不开浏览器式文件标签页：在系统文件管理器中显示。
          // reveal 失败绝不静默——把完整路径告诉用户
          <button
            type="button"
            onClick={() => {
              if (!dir) return;
              void revealExportedFile(dir, out.name!).then((ok) => {
                if (!ok)
                  setRevealError(
                    ex("revealFailed", { path: `${dir}/${out.name}` }),
                  );
              });
            }}
            className="min-w-0 truncate rounded-md font-mono text-sm text-ink-2 outline-none hover:underline focus-visible:focus-ring"
          >
            {out.name}
          </button>
        ) : (
          // 后端回的是裸路径 /exports/<name>，必须过 apiUrl() 补 pj：`<a>` 加不了
          // 请求头，不带 pj 时后端落到**默认项目**的导出目录
          <a
            href={apiUrl(out.url ?? "")}
            target="_blank"
            rel="noreferrer"
            className="min-w-0 truncate font-mono text-sm text-ink-2 hover:underline"
          >
            {out.name}
          </a>
        )}
        <span className="shrink-0 text-xs tabular-nums text-ink-3">{dims}</span>
        {out.replaced && (
          <span className="shrink-0 text-xs text-ink-3">{ex("replaced")}</span>
        )}
      </div>
      <InspectionLine manifest={out.manifest} />
      {revealError && <p className="text-sm text-danger-content">{revealError}</p>}
    </div>
  );
}

/**
 * 这一件产物核验得怎么样（ADR 0068）。解读只有 `lib/artifactInspection.inspectionState()`
 * 一份：**只有全部可判项都核过才画绿**；有未核验项就按「未核验」说、不画勾；有失败项
 * 红着列出（standard 政策下文件照样交付了，用户投出去之前得知道）；`not_applicable` 不画。
 * 没有 manifest（老服务端 / 检查器没跑）= 未核验。
 */
function InspectionLine({
  manifest,
}: {
  manifest: ExportOutput["manifest"];
}) {
  useTranslation("dialogs");
  const state = inspectionState(manifest);
  const label = (k: string) =>
    ex(`inspection.check.${k}`, { defaultValue: k });
  if (state.tone === "verified") {
    return (
      <p
        data-inspection="verified"
        className="flex items-center gap-1 text-sm text-ok-content"
      >
        <CircleCheck size={ICON_SIZE.xs} className="shrink-0" aria-hidden />
        {ex("inspection.verified")}
      </p>
    );
  }
  if (state.tone === "failed") {
    return (
      <p
        data-inspection="failed"
        className="flex items-start gap-1 text-sm text-danger-content"
      >
        <TriangleAlert
          size={ICON_SIZE.xs}
          className="mt-0.5 shrink-0"
          aria-hidden
        />
        {ex("inspection.failedItems", {
          items: state.failed.map(label).join("、"),
        })}
      </p>
    );
  }
  return (
    <p data-inspection="unknown" className="text-sm text-ink-3">
      {state.unknown.length
        ? ex("inspection.unknownItems", {
            items: state.unknown.map(label).join("、"),
          })
        : ex("inspection.unknown")}
    </p>
  );
}

/**
 * 一种输出格式：普通复选框 + 名字。格式是多选，所以就用复选框的语法，不做成
 * 大卡片。说明（矢量 / 位图 …）进 `title`；禁用的**原因**由调用方写成可见文字，
 * 这里只用 `describedBy` 把它接上——一个灰掉的复选框解释不了自己。
 */
/**
 * 对话框里唯一的一种行（2026-09-15 打磨批次 D）：标签列 80px 在左、控件在右、行高 28。
 * 没有标签的行（说明、清单、确认框）第一列留空，内容与上面的控件同一条竖线。
 */
function FormatCheck({
  checked,
  onChange,
  format,
  hint,
  disabled = false,
  describedBy,
}: {
  checked: boolean;
  onChange: () => void;
  format: ExportFormat;
  hint: string;
  disabled?: boolean;
  /** 禁用原因那段可见文字的 id */
  describedBy?: string;
}) {
  return (
    <label
      data-export-format={format}
      title={hint}
      aria-disabled={disabled || undefined}
      className={cn(
        "flex min-h-7 items-center gap-2 font-medium",
        disabled ? "cursor-not-allowed text-ink-faint" : "text-ink",
      )}
    >
      <Checkbox
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        aria-describedby={describedBy}
      />
      <span>{format.toUpperCase()}</span>
    </label>
  );
}
