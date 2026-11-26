/**
 * 「这张图是怎么从数据画出来的」（点选误差线时在属性页换 SD / SEM / 95% CI 用，ADR 0178）：纯函数。
 *
 * 只给**算出来的**误差线（`error_source === 'computed'`：均值 ± SD / SEM / 95% CI）：表里自带误差列的，类型由数据决定，不在这里换。
 * 来历按脚本路径记（`store/figureOriginStore`，不持久化、换项目即清）；这里只放判据，不碰 store。
 *
 * 「图没被改过」是**换图的前提**：换图 = 在同一块面板上换成另一个脚本画的图，旧图上的手改带不过去。判据按「图上看得见的东西」逐条列：
 *  - `panel.overrides`：每一条都得是样式绑定写的、且仍是样式写下的值（`ownedLive`）；用户手改的任何一条都算改过。
 *    （绑定了样式的画布上新图会被自动对齐，那些 override 不是用户做的，不能拿「overrides 为空」当判据）；
 *  - 脚本改过：**写回脚本（ADR 0094）会把写进脚本的那几条从 overrides 里清掉**，所以 overrides 为空不代表没改。来源指纹
 *    （`source_fingerprint` 含脚本内容的 sha256）与登记时不同、或素材不是「新鲜」的，就算改过；
 *  - 写回原始文件留下的基线（素材的 `baked_overrides`）：同样是图上已有的改动；
 *  - 位置 / 缩放 / 裁剪 / 旋转不算（换素材保留它们，与 `replacePanelAsset` 同一口径）。
 */
import type { CapturedFigureDescriptor, AiDataProposeItem, RuntimeAssetInfo } from '@/lib/api'
import { candidateIdentity } from '@/lib/dataFigureAdapter'
import type { TableRef } from '@/lib/dataFigure'
import { ownedLive } from '@/lib/styleOwned'
import type { FigureDocument, PanelObject } from '@/types/document'

/** 可换的三种误差类型（顺序 = 下拉里的顺序，选项文案沿用数据页右栏的 `dataFigureV2.confirm.option.*`） */
export const ERROR_KINDS = ['sd', 'sem', 'ci95'] as const
export type ErrorKind = (typeof ERROR_KINDS)[number]
export const isErrorKind = (v: unknown): v is ErrorKind => (ERROR_KINDS as readonly unknown[]).includes(v)

/** 来历最多记几份（最旧的先丢）：数据页里一次会话画不了这么多，只是不让它无限长 */
export const ORIGIN_MAX = 8

/** 一张图的来历：重新出同一种画法、只换误差类型所需的一切（与当初 propose 请求里发的是同一份） */
export interface FigureOrigin {
  /** 画这张图的脚本（项目相对路径，POSIX）：记录的键 */
  script: string
  /** 画的是哪张表（单文件；合画不记） */
  file: TableRef
  /** 用户指过的表头物理行（没指过 = null，不带 `header_rows`） */
  headerRow: number | null
  /** 当初 propose 带的 `answers`（含看图目的、模型建议写进去的列角色）；换类型时只改其中的 `error` */
  answers: Record<string, unknown>
  /** 当初 propose 带的已解析规范与字号范围（没带 = null） */
  spec: Record<string, unknown> | null
  fontRange: { min_pt?: number; max_pt?: number } | null
  /** 候选身份（`candidateIdentity`，不含误差类型）：换类型后在新的一批里认回同一种画法 */
  identity: string
  /** 此刻这张图的误差类型 */
  kind: ErrorKind
  /** 误差画成阴影带（趋势的 `line_band`，图里是 `fill` 元素）而不是误差线；点阴影带时属性页才出这一行 */
  band: boolean
  /** 这张图第一次放上画布时的来源指纹；还没见到它的面板时是 null（此时不给换） */
  fingerprint: string | null
  /** 画这张图时的项目（换项目即清，这里再记一份是防御） */
  pj: string | null
}

/** 项目相对路径统一成正斜杠再比 */
export const normScript = (s: string): string => s.replace(/\\/g, '/')

/**
 * 这一张候选能不能登记成「可换误差类型」的来历：误差线是用重复值**算出来的**、种类是 SD / SEM / 95% CI 三者之一。
 * 取自现成误差列的（`column`）、没有误差线的、种类说不清（`unknown`）的都不登记。
 */
export function switchableKind(item: Pick<AiDataProposeItem, 'error' | 'error_source'>): ErrorKind | null {
  return item.error_source === 'computed' && isErrorKind(item.error) ? item.error : null
}

/** 登记一份来历：调用方给齐当时请求里的那几样（合画 / 不可换的候选返回 null） */
export function makeOrigin(args: {
  script: string
  item: AiDataProposeItem
  file: TableRef
  grouped: boolean
  headerRow: number | null
  answers: Record<string, unknown>
  spec: Record<string, unknown> | null
  fontRange: { min_pt?: number; max_pt?: number } | null
  pj: string | null
}): FigureOrigin | null {
  if (args.grouped) return null
  const kind = switchableKind(args.item)
  if (!kind) return null
  return {
    script: normScript(args.script),
    file: { path: args.file.path, sheet: args.file.sheet },
    headerRow: args.headerRow,
    answers: structuredClone(args.answers),
    spec: args.spec ? structuredClone(args.spec) : null,
    fontRange: args.fontRange ? { ...args.fontRange } : null,
    identity: candidateIdentity(args.item),
    kind,
    band: /band/.test(args.item.title_code),
    fingerprint: null,
    pj: args.pj,
  }
}

/** 换类型的请求里的 `answers`：沿用当初的，只把 `error` 换成点选的那一种 */
export const answersWithKind = (o: FigureOrigin, kind: ErrorKind): Record<string, unknown> => ({ ...o.answers, error: kind })

/** 新一批候选里认回「同一种画法」且误差类型正是点选的那一种、仍是算出来的；没有 = null（调用方静默不换） */
export function findSameDrawing<T extends AiDataProposeItem>(items: readonly T[], o: FigureOrigin, kind: ErrorKind): T | null {
  return items.find((i) => candidateIdentity(i) === o.identity && i.error_source === 'computed' && i.error === kind) ?? null
}

/** 出现换类型那一行的素材事实（调用方从素材清单里取这块面板对应的那一条） */
export interface FigureFacts {
  /** 运行时素材清单里这张图的条目；还没取到 / 找不到 = undefined（不知道就不给换） */
  runtime: Pick<RuntimeAssetInfo, 'status' | 'descriptor'> | undefined
  /** 这张图写回原始文件留下的基线条数（`PanelInfo.baked_overrides`）；没有 = 0 */
  bakedCount: number
}

/**
 * 这块面板是不是**没被改过**（见文件头）。任何一条拿不准都回 false——宁可不给换，也不把用户的改动换没。
 * `descriptor` 在手时再核一遍它的指纹（素材条目的状态也许还没来得及变）。
 */
export function figureUntouched(doc: FigureDocument, panel: PanelObject, origin: FigureOrigin, facts: FigureFacts): boolean {
  if (panel.fileKind !== 'runtime' || !panel.script || normScript(panel.script) !== origin.script) return false
  if (origin.fingerprint == null) return false
  // 图上每一条 override 都得是样式写的、且还是它写下的值
  for (const o of panel.overrides) if (!ownedLive(doc, panel, o.gid, o.prop)) return false
  if (facts.bakedCount > 0) return false
  // 脚本没变：面板记的指纹、素材清单里的指纹都还是登记时那个，素材是「新鲜」的
  if (panel.source?.fingerprint !== origin.fingerprint) return false
  const rt = facts.runtime
  if (!rt || rt.status !== 'fresh') return false
  const d: CapturedFigureDescriptor | null = rt.descriptor
  if (d && d.source_fingerprint !== origin.fingerprint) return false
  return true
}
