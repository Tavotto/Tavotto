import { migrateFigureFrames } from '@/lib/figureFrame'
import type { FigureDocument } from '@/types/document'

/**
 * 画布节点里除了 name / page / objects / guides 之外**存下的每一个**可选字段。恢复时逐个照节点写回：节点里有 →
 * 拷进来；节点里没有 → 当前画布上的也去掉（节点拍下时就没有）。**新增 `FigureDocument` 的可选字段必须加在这里**——
 * 下面的 `_Exhaustive` 在漏了一个字段时 tsc 报错（Codex #2235 P1：旧配方只拷 layoutGroups，
 * framePad / framePadTight / profile / style / colorKeys / quickLayout 被静默丢掉，残缺的文档随后被自动保存）。
 */
const OPTIONAL_KEYS = ['layoutGroups', 'profile', 'style', 'framePad', 'framePadTight', 'quickLayout', 'colorKeys'] as const
type _Exhaustive = Exclude<keyof FigureDocument, 'schema' | 'name' | 'page' | 'objects' | 'guides' | (typeof OPTIONAL_KEYS)[number]>
const _exhaustive: [_Exhaustive] extends [never] ? true : never = true
void _exhaustive

/**
 * 「把当前画布整份换成单画布节点那一版」的配方——**唯一出处**（ADR 0101）。节点存下的完整画布状态逐字写回。
 *
 * 两个调用方各自包一次 `commit`（⌘Z 一步退回）：时间线恢复（`actions.restoreLayoutVersion`）与
 * 从项目自己的时间线找回的工作副本（`documentRestore.installFromHistory`，ADR 0211）。放在这一层是为了
 * 后者不必 import `actions`（那会多出一个 import 环）。面板 overrides 的身份原样恢复，不按此刻的 manifest 重抄（ADR 0083）。
 */
export function applyCanvasVersion(d: FigureDocument, version: FigureDocument): void {
  d.name = version.name
  d.page = structuredClone(version.page)
  // 升级前存下的检查点里的面板照样按 ADR 0098 迁移（与读档同一个函数）
  d.objects = migrateFigureFrames(structuredClone(version.objects))
  d.guides = structuredClone(version.guides)
  // 布局组跟着对象走：对象身上的 groupId 指向的是**那一版**的组；留白、规范、样式、色键同理都是那一版的
  const target = d as unknown as Record<string, unknown>
  const source = version as unknown as Record<string, unknown>
  for (const key of OPTIONAL_KEYS) {
    const value = source[key]
    // 空数组的 layoutGroups / framePadTight 与缺席同义（`canvasToDoc` 也不写它们）
    if (value === undefined || (Array.isArray(value) && value.length === 0)) delete target[key]
    else target[key] = structuredClone(value)
  }
}
