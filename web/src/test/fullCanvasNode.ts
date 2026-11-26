import type { FigureDocument, TextObject } from '@/types/document'

/**
 * 一份**每个可选字段都非默认**的单画布节点（Codex #2235 P1 的看护夹具）：恢复之后逐字段与它相同才算没丢东西。
 * 字段形状只求可区分（恢复路径只拷贝、不解释）；新增 `FigureDocument` 可选字段时 `canvasVersionRecipe` 的
 * 穷举检查会先红，再回来这里补一个非默认值。
 */
export function fullCanvasNode(objects: TextObject[]): FigureDocument {
  return {
    schema: 2,
    name: '全字段版',
    page: { w: 183, h: 97 },
    objects,
    guides: [{ axis: 'x', pos: 12 }],
    layoutGroups: [{ id: 'g1', kind: 'grid', members: ['t_node'] }] as unknown as FigureDocument['layoutGroups'],
    profile: { id: 'nature', columns: 2 } as unknown as FigureDocument['profile'],
    style: { id: 'style_paper', follow: true } as unknown as FigureDocument['style'],
    framePad: { l: 1.5, t: 2.5, r: 3.5, b: 4.5 },
    framePadTight: ['l', 'b'] as FigureDocument['framePadTight'],
    quickLayout: { cols: 2, rows: 1, gap: 3 } as unknown as FigureDocument['quickLayout'],
    colorKeys: { series_a: '#1f77b4' } as unknown as FigureDocument['colorKeys'],
  }
}

/** 节点里存下的可选字段（逐字比对用） */
export const OPTIONAL_FIELDS = ['layoutGroups', 'profile', 'style', 'framePad', 'framePadTight', 'quickLayout', 'colorKeys'] as const
