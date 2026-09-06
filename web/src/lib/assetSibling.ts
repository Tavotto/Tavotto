import type { PanelInfo, RuntimeAssetInfo } from '@/lib/api'

/**
 * runtime 素材与磁盘图的**同源关系**：同一脚本产出、同一 stem。
 *
 * 后端清单本该让有原件的图只以 FileAsset 出现（`runtimeasset.list_assets`），
 * 但它只在项目根一层找原件，而注册表按 stem 匹配任意子目录里的文件——子目录
 * 里的 PDF 与它的「尚未运行」runtime 条目于是会同时出现（UI 审计 T06）。素材库
 * 不隐藏任何一条（各有各的动作），只把关系说出来：相邻显示 + 「同源：X.pdf」。
 */
export function runtimeSiblingOf(
  asset: RuntimeAssetInfo,
  panels: readonly PanelInfo[],
): PanelInfo | null {
  return panels.find((p) => p.script === asset.script && p.name === asset.stem) ?? null
}
