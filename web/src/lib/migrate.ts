import { newId } from '@/lib/id'
import { t } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import type {
  CanvasObject,
  FigureDocument,
  PanelObject,
  ProjectDocument,
  TextObject,
} from '@/types/document'
import { SCHEMA_CURRENT, emptyDocument, futureSchemaOf, migrateToProject } from '@/types/document'

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
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error(t('layoutSchemaInvalid', { ns: 'errors', product: PRODUCT_NAME }))
  }
  if (futureSchemaOf(payload) !== null) {
    throw new Error(`${t('docBanner.tooNewTitle', { ns: 'workspace', product: PRODUCT_NAME })}。${t('docBanner.tooNewBody', { ns: 'workspace', product: PRODUCT_NAME })}`)
  }
  if (payload && typeof payload === 'object' && Object.prototype.hasOwnProperty.call(payload, 'schema')) {
    const schema = (payload as {schema?: unknown}).schema
    if (typeof schema !== 'number' || !Number.isInteger(schema) || schema < 1 || schema > SCHEMA_CURRENT) {
      throw new Error(t('layoutSchemaInvalid', { ns: 'errors', product: PRODUCT_NAME }))
    }
  }
  if (payload && typeof payload === 'object' && (payload as { schema?: number }).schema === 3) {
    const pd = migrateToProject(payload)
    if (pd) return pd
    throw new Error(t('layoutSchemaInvalid', { ns: 'errors', product: PRODUCT_NAME }))
  }
  const doc = emptyDocument()
  doc.name = name
  const raw = payload as Unknown
  const page = raw.page as Unknown | undefined
  doc.page = {
    w: num(page?.w, doc.page.w),
    h: num(page?.h, doc.page.h),
  }

  if (raw.schema === 2) {
    if ('objects' in raw && !Array.isArray(raw.objects)) {
      throw new Error(t('layoutSchemaInvalid', { ns: 'errors', product: PRODUCT_NAME }))
    }
    const objects = (raw.objects ?? []) as unknown[]
    if (objects.some((o) => !o || typeof o !== 'object' || Array.isArray(o) || typeof (o as {type?: unknown}).type !== 'string')) {
      throw new Error(t('layoutSchemaInvalid', { ns: 'errors', product: PRODUCT_NAME }))
    }
    doc.objects = objects as CanvasObject[]
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

  if (['project', 'canvases', 'activeCanvasId', 'objects'].some((key) => Object.prototype.hasOwnProperty.call(raw, key))) {
    throw new Error(t('layoutSchemaInvalid', { ns: 'errors', product: PRODUCT_NAME }))
  }
  if ('objs' in raw && !Array.isArray(raw.objs)) {
    throw new Error(t('layoutSchemaInvalid', { ns: 'errors', product: PRODUCT_NAME }))
  }
  const objs = Array.isArray(raw.objs) ? raw.objs : []
  const migrated = objs.map((o) => o && typeof o === 'object' && !Array.isArray(o) ? migrateObject(o as Unknown) : null)
  if (migrated.some((o) => o === null)) {
    throw new Error(t('layoutSchemaInvalid', { ns: 'errors', product: PRODUCT_NAME }))
  }
  doc.objects = migrated as CanvasObject[]
  return doc
}
