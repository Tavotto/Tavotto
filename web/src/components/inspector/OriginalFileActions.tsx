import type { PanelObject } from '@/types/document'
import { UpdateSourceButton, WriteBackRecordsButton } from './UpdateSourceButton'

/**
 * 「原始文件」组的动作：**一行，对象页与元素页同一份**（2026-09-15 全面打磨 O2）。
 *
 * 权重按后果分：会覆盖磁盘原件的「写回」是 secondary，「写回记录」（只在写回过之后出现）
 * 是 ghost；宽度一律按内容取，不撑满。
 * 2026-10-01（设计稿 C5 / C11）：「历史」改名「写回记录」，打开的是写回窗口的记录页；
 * 「同步修改到…」搬到画布对象的右键菜单（`canvas/ObjectContextMenu`），这一行不再有它。
 */
export function OriginalFileActions({ panel }: { panel: PanelObject }) {
  // 写回与写回记录读的都是「脚本产出的那张原件」：没有脚本时它们无从谈起，不渲染
  if (!panel.script) return null
  return (
    <div className="flex flex-wrap items-center gap-1">
      <UpdateSourceButton panel={panel} />
      <WriteBackRecordsButton panel={panel} />
    </div>
  )
}
