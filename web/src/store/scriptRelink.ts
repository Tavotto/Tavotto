import { msg } from '@/i18n'
import { relinkFinish, relinkPrepare } from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { refreshAssetsAndSync } from '@/store/liveSync'
import { useAssetStore } from '@/store/assetStore'
import { requestRender } from '@/store/renderScheduler'
import { isGatePhase, useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'

/**
 * 把一张图接回挪走的脚本（ADR 0211 第一期 6）。**先验证、跑成了才改登记**：接回就是对选中的那份脚本做一次现有的
 * 试运行登记（`scriptRunStore.run` → `/api/registry/probe`，失败时注册表一个字节不动；数据找不到 / 运行目录 / 依赖
 * 这类「还差一个决定」照旧弹各自的框，不算失败）。跑成了再看这张图的归属是不是真的换到了它——图名对得上才算接上，
 * 然后按原来的图内修改重画（面板身份、overrides 都不动）。没接上就不改登记，原来那张错误卡留着。
 *
 * 只给磁盘上的图（`fileId` 不是 `runtime:`）用：运行时图的身份里带着脚本路径，换脚本等于换了一张图。
 *
 * **参数随接回带走**（维护者 2026-11-26 拍板）：试跑之前请后端把旧脚本键下的运行配置 / 答案 / 输入转录复制到新脚本键下
 * （`relinkPrepare`，待办），试跑用带过来的默认配置；接上了 `relinkFinish(ok)` 让它们留下，没接上原样撤销——新键下不留残留。
 * 还差一个决定（门）时不收尾：用户在弹框里定完、重跑成没成，后端下一次按注册表的事实收尾。运行许可、解释器信任照旧不带。
 */
export type RelinkOutcome = 'linked' | 'gated' | 'not_linked' | 'superseded'

export async function relinkScript(panel: PanelObject, from: string, target: string): Promise<RelinkOutcome> {
  const pj = currentProjectId()
  const prepared = await relinkPrepare(from, target, pj)
  if (currentProjectId() !== pj) return 'superseded'
  await useScriptRunStore.getState().run(target, prepared?.runConfig ?? undefined)
  if (currentProjectId() !== pj) return 'superseded'
  const st = useScriptRunStore.getState().byScript[target]
  if (st?.error) {
    if (isGatePhase(st.phase)) return 'gated'
    await relinkFinish(from, target, false, pj)
    return 'not_linked'
  }
  // `force`：注册表刚写过，不复用写之前发出的在途请求
  await refreshAssetsAndSync({ force: true })
  if (currentProjectId() !== pj) return 'superseded'
  const cap = useAssetStore.getState().byId[panel.fileId]?.capability
  const linked = cap?.status === 'editable' && cap.script === target
  await relinkFinish(from, target, linked, pj)
  if (!linked) return 'not_linked'
  requestRender(panel, true)
  useUiStore.getState().setStatus(msg('relink.linked', { script: target }, 'inspector'), 'done')
  return 'linked'
}

/**
 * 一键按钮上写什么（维护者 2026-11-26 拍板，不截断）：同名、只是换了文件夹 → 「接回 scripts/」（目标文件夹的相对路径，
 * `/` 结尾；在项目根就是「接回 项目根目录」）；改了名 → 「接回 new_name.py」。文件夹路径特别长时只写最后一级。
 * 完整的项目相对路径放在悬停提示里（调用方的 `title`）。
 */
export const RELINK_DIR_MAX = 24

export function relinkTarget(from: string, to: string): { kind: 'dir' | 'root' | 'file'; text: string } {
  const base = (p: string) => p.split('/').pop() ?? p
  if (base(from) !== base(to)) return { kind: 'file', text: base(to) }
  const dir = to.split('/').slice(0, -1).join('/')
  if (!dir) return { kind: 'root', text: '' }
  const shown = dir.length > RELINK_DIR_MAX ? dir.split('/').pop()! : dir
  return { kind: 'dir', text: `${shown}/` }
}
