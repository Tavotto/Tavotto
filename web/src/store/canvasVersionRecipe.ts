import { migrateFigureFrames } from '@/lib/figureFrame'
import type { FigureDocument } from '@/types/document'

/**
 * 「把当前画布整份换成单画布节点那一版」的配方——**唯一出处**（ADR 0101）。
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
  // 布局组跟着对象走：对象身上的 groupId 指向的是**那一版**的组。只换对象
  // 不换组，恢复出来的对象会挂在当前排版里不存在（或成员不同）的组上
  if (version.layoutGroups?.length) d.layoutGroups = structuredClone(version.layoutGroups)
  else delete d.layoutGroups
}
