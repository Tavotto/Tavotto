import { newId } from '@/lib/id'
import type {
  CanvasObject,
  FigureDocument,
  PanelObject,
  ProjectDocument,
  TextObject,
} from '@/types/document'
import { emptyDocument, migrateToProject } from '@/types/document'

type Unknown = Record<string, unknown>

const num = (v: unknown, fallback: number): number =>
  typeof v === 'number' && Number.isFinite(v) ? v : fallback

const str = (v: unknown, fallback: string): string => (typeof v === 'string' ? v : fallback)

/** v1 布局对象 → schema 2 对象；无法识别的条目返回 null 并被丢弃。 */
function migrateObject(raw: Unknown): CanvasObject | null {
  const type = raw.type
  const base = {
    id: raw.uid != null ? `o_${raw.uid}` : newId(),
    x: num(raw.x, 0),
    y: num(raw.y, 0),
    w: num(raw.w, 20),
    h: num(raw.h, 20),
  }

  if (type === 'panel') {
    const panel: PanelObject = {
      ...base,
      type: 'panel',
      fileId: str(raw.id, ''),
      fileKind: raw.kind === 'raster' ? 'raster' : 'pdf',
      nativeW: num(raw.nativeW, base.w),
      nativeH: num(raw.nativeH, base.h),
      pxW: num(raw.pxW, 0) || undefined,
      pxH: num(raw.pxH, 0) || undefined,
      script: typeof raw.script === 'string' ? raw.script : null,
      overrides: Array.isArray(raw.overrides) ? (raw.overrides as PanelObject['overrides']) : [],
      name: typeof raw.name === 'string' ? raw.name : undefined,
    }
    return panel.fileId ? panel : null
  }

  if (type === 'text') {
    const text: TextObject = {
      ...base,
      type: 'text',
      text: str(raw.text, ''),
      sizePt: num(raw.size ?? raw.sizePt, 10),
      bold: raw.bold === true,
      italic: raw.italic === true || undefined,
      color: str(raw.color, '#000000'),
      align: (['left', 'center', 'right'] as const).includes(raw.align as 'left')
        ? (raw.align as TextObject['align'])
        : 'left',
    }
    return text
  }

  return null
}

/**
 * 把 /api/layouts 返回的任意载荷规范化成当前文档模型。
 * schema:3 项目文档直接透传（switchDocument 会校验），
 * schema:2 原样透传（补齐缺失字段），v1 结构走字段映射。
 */
export function normalizeLayout(
  payload: unknown,
  name: string,
): FigureDocument | ProjectDocument {
  if (payload && typeof payload === 'object' && (payload as { schema?: number }).schema === 3) {
    const pd = migrateToProject(payload)
    if (pd) return pd
  }
  const doc = emptyDocument()
  doc.name = name
  if (!payload || typeof payload !== 'object') return doc

  const raw = payload as Unknown
  const page = raw.page as Unknown | undefined
  doc.page = {
    w: num(page?.w, doc.page.w),
    h: num(page?.h, doc.page.h),
  }

  if (raw.schema === 2 && Array.isArray(raw.objects)) {
    doc.objects = (raw.objects as CanvasObject[]).filter(
      (o) => o && typeof o === 'object' && typeof o.type === 'string',
    )
    doc.guides = Array.isArray(raw.guides) ? (raw.guides as FigureDocument['guides']) : []
    doc.name = typeof raw.name === 'string' && raw.name ? raw.name : name
    // 样式绑定（ADR 0081）是 schema 2 形状里的可选字段：原样带过来，否则打开一份绑定的版面
    // 再存一次，绑定就静默没了（Codex #547 P1）。形状不对的不收
    const style = raw.style as Unknown | undefined
    if (style && typeof style.id === 'string' && style.snapshot && typeof style.snapshot === 'object') {
      doc.style = {
        id: style.id,
        snapshot: style.snapshot as Record<string, unknown>,
        ...(style.detached === true ? { detached: true as const } : {}),
        // 样式写的 override 登记（§十三）：丢了它，重开后样式写的全被当成用户手改，脚本重跑不再让位
        ...(style.owned && typeof style.owned === 'object' && !Array.isArray(style.owned)
          ? { owned: style.owned as NonNullable<FigureDocument['style']>['owned'] }
          : {}),
      }
    }
    return doc
  }

  const objs = Array.isArray(raw.objs) ? raw.objs : []
  doc.objects = objs
    .map((o) => migrateObject(o as Unknown))
    .filter((o): o is CanvasObject => o !== null)
  return doc
}
