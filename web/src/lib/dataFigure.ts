/**
 * 「从数据到图」（ADR 0178）前端的纯函数：键、回答合并、文件清单 → 行状态。
 * 事实在后端（读表、认表头、出备选、核对数据），这里只做「把后端的话翻成界面要的形状」，不判断、不编造。
 */
import type { AiDataFileEntry, AiDataOption, AiDataOverview } from '@/lib/api'

/** 一张表的结构化键：项目相对路径 + 工作表名（文本表为 null）。不用文件名、不拼字符串（sheet 名里可以有任何字符） */
export interface TableRef {
  path: string
  sheet: string | null
}

/** 作 Map / Record 的键用：JSON 数组，不会因路径或工作表名里的字符撞车 */
export const tableKey = (r: TableRef): string => JSON.stringify([r.path, r.sheet])

export const sameTable = (a: TableRef | null, b: TableRef | null): boolean =>
  a != null && b != null && a.path === b.path && a.sheet === b.sheet

export const isWorkbook = (path: string): boolean => /\.xlsx$/i.test(path)

/** 清单里一行（一张表）此刻怎么说 */
export type RowStatus =
  | { kind: 'table'; rows: number; columns: number }
  | { kind: 'unsure' }
  | { kind: 'unreadable'; code: string | null }
  /** 超出读取预算没读（`table: null` / 工作表四项全 null）：不是「读不了」 */
  | { kind: 'unread' }

export interface DataRow {
  ref: TableRef
  status: RowStatus
}

export interface DataFileRow {
  path: string
  /** 文本表：这一个；XLSX：每张工作表一行（可展开）；整本没读时没有子行 */
  rows: DataRow[]
  workbook: boolean
  /** XLSX 整本没读（`sheets` 是 null） */
  unread: boolean
}

/** 后端 `/api/ai/data-files?formats=all` → 清单行。读不出形状的项当「没读」，不当成空 */
export function fileRows(entries: AiDataFileEntry[]): DataFileRow[] {
  return entries.map((e) => {
    if (isWorkbook(e.path)) {
      const sheets = Array.isArray(e.sheets) ? e.sheets : null
      return {
        path: e.path,
        workbook: true,
        unread: sheets === null,
        rows: (sheets ?? [])
          .filter((s) => !s.hidden)
          .map((s) => ({
            ref: { path: e.path, sheet: s.name },
            status:
              s.status === 'ok' && s.rows != null && s.columns != null
                ? { kind: 'table', rows: s.rows, columns: s.columns }
                : s.status === 'unreadable'
                  ? { kind: 'unreadable', code: s.code }
                  : { kind: 'unread' },
          })),
      }
    }
    const t = e.table
    const status: RowStatus =
      !t || typeof t !== 'object'
        ? { kind: 'unread' }
        : t.status === 'ok' && t.rows != null && t.columns != null
          ? { kind: 'table', rows: t.rows, columns: t.columns }
          : t.status === 'unsure'
            ? { kind: 'unsure' }
            : t.status === 'unreadable'
              ? // 读不了但后端带着原文行（指定表头行就能救回来，如仪器说明行触发的 data_ragged）：和「没认准」同一档，别让人直接放弃
                Array.isArray(t.preview) && t.preview.length > 0
                ? { kind: 'unsure' }
                : { kind: 'unreadable', code: t.code }
              : { kind: 'unread' }
    return { path: e.path, workbook: false, unread: false, rows: [{ ref: { path: e.path, sheet: null }, status }] }
  })
}

/** 用户指定了「每列的名字」那一行并重读成功后，清单里这一行改说重读后的行列数 */
export function withOverview(row: DataRow, ov: AiDataOverview | undefined): RowStatus {
  if (!ov || ov.path !== row.ref.path || (ov.sheet ?? null) !== row.ref.sheet) return row.status
  if (ov.status === 'ok' && ov.rows != null && ov.columns != null) return { kind: 'table', rows: ov.rows, columns: ov.columns }
  // 重读后仍读不了但还有原文行：还是「没认准」（可以接着点别的行）；没有原文行才是真读不了
  if (ov.status === 'unreadable') return (ov.raw_lines?.length ?? 0) > 0 ? { kind: 'unsure' } : { kind: 'unreadable', code: ov.code }
  return row.status
}

/** 一个回答并进已有回答：`cells` 按列深合并（后端一次可能问多列，浅合并只剩最后一列） */
export function mergeAnswers(cur: Record<string, unknown>, add: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...cur }
  for (const [k, v] of Object.entries(add)) {
    if (k === 'cells' && v && typeof v === 'object' && out.cells && typeof out.cells === 'object') {
      out.cells = { ...(out.cells as Record<string, unknown>), ...(v as Record<string, unknown>) }
    } else out[k] = v
  }
  return out
}

/** 有文字输入的选项（「我来说明」）这版不出现：它要模型把说明整理成规则，没有就会 409 */
export const usableOptions = (opts: AiDataOption[] | undefined): AiDataOption[] => (opts ?? []).filter((o) => !o.free_text)

/** 混了文字的两个处理方式（`answer.cells.<列>.mode`），其余 mode 一律不出现 */
export type CellMode = 'skip' | 'extract'
export const cellModeOf = (o: AiDataOption): CellMode | null => {
  const cells = o.answer?.cells as Record<string, { mode?: string }> | undefined
  const mode = cells ? Object.values(cells)[0]?.mode : undefined
  return mode === 'skip' || mode === 'extract' ? mode : null
}

/** 会话过期 / 数据变了：不是用户的错，静默重新出备选 */
export const isStaleProposeCode = (code: unknown): boolean => code === 'propose_session_expired' || code === 'propose_data_changed' || code === 'propose_candidate_unknown'

/** 备选没出来的原因码里，界面认得的（其余走通用一句） */
export const KNOWN_NOTHING_REASONS: readonly string[] = [
  'no_usable_table',
  'value_column_mixed',
  'table_not_found',
  'no_numeric_column',
  'matrix_unsupported',
  'no_renderable_family',
  'all_candidates_rejected',
  'candidate_template_unsupported',
  'candidate_render_failed',
  'candidate_timeout',
  'candidate_thumbnail_failed',
  'candidate_fingerprint_mismatch',
  'candidate_write_blocked',
]

/**
 * 预览浮层的目标宽度（px，最终再夹进 [360, 640] 与画布栏的 60%）：按内容估——每列取「列名 / 前几行里最长的格子」的字符数，
 * 一个字符约 7.5px，加列间距 24；没认准的原文行固定 560。不测 DOM：宽度变化要走 CSS 过渡，必须在渲染前就知道目标值。
 */
export function previewCardWidth(ov: AiDataOverview | null): number {
  if (!ov) return 420
  if ((ov.status === 'unsure' || ov.status === 'unreadable') && (ov.raw_lines?.length ?? 0) > 0) return 560
  if (ov.status !== 'ok' || ov.fields.length === 0) return 420
  let w = 40
  ov.fields.forEach((f, i) => {
    const longest = Math.max(f.name.length, ...ov.preview_rows.map((r) => (r.cells[i] ?? '').length))
    w += Math.min(Math.max(longest, 6) * 7.5 + 24, 232)
  })
  return Math.round(Math.min(640, Math.max(360, w)))
}

/** 卡片的目标宽度（px，未按画布栏 60% 封顶）：预览按内容估，其余各屏固定；`DataOverlay` 与画布浮条的左内缩共用这一份 */
export function overlayTargetWidth(sceneKind: string, ov: AiDataOverview | null, pickCount = 2): number {
  // 选画法：只有 1 个画法时卡片贴着单列的磁贴（左右留白相等），其余 2 列
  return sceneKind === 'pick' ? (pickCount === 1 ? 360 : 640) : sceneKind === 'ask' ? 480 : sceneKind === 'browse' ? previewCardWidth(ov) : 420
}

/** 卡片宽度的 CSS 表达式：不窄于 360、不宽于目标、不超过画布栏的 60%（百分数相对画布栏，浮条那一头的容器与它同一个包含块） */
export const overlayWidthCss = (target: number): string => `max(360px, min(${target}px, 60%))`

/** 这一列前三个**不同的**值的样例（`0, 2, 4…`），选择题里的灰字；不足三个不同值有几个显示几个（全相同只显示一个），还有更多不同值才加省略号 */
export function columnSample(ov: AiDataOverview | null, column: string): string | null {
  if (!ov || ov.status !== 'ok') return null
  const i = ov.fields.findIndex((f) => f.name === column)
  if (i < 0) return null
  const distinct = [...new Set(ov.preview_rows.map((r) => (r.cells[i] ?? '').trim()).filter((v) => v !== ''))]
  if (distinct.length === 0) return null
  return distinct.length > 3 ? `${distinct.slice(0, 3).join(', ')}…` : distinct.join(', ')
}

/** 卡片的实际宽度（px）：与 `overlayWidthCss` 同一条规则，给量测用（`parentW` = 画布栏宽） */
export const overlayCardPx = (target: number, parentW: number): number => Math.max(360, Math.min(target, parentW * 0.6))

/**
 * 卡片让位之后，剩下的那部分画布放不放得下底部工具条：剩余宽度 ≥ 工具条自身宽度 + 两侧各 12px。
 * 全是量出来的数（`toolbarW` = 工具条内容的自然宽度），不写死像素；放不下的时候浮条整体淡出，卡片关掉或变窄再回来。
 */
export const chromeFits = (parentW: number, insetPx: number, toolbarW: number): boolean => parentW - insetPx >= toolbarW + 24

/** 这张表里有没有能画的数值：数值列或「混有文字」的列（后者会先问怎么处理）。没有就不给「看看怎么画」 */
export const hasNumericColumn = (ov: AiDataOverview): boolean => ov.fields.some((f) => f.kind === 'numeric' || f.kind === 'mixed')
