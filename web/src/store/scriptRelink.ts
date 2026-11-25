import { msg } from '@/i18n'
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
 */
export type RelinkOutcome = 'linked' | 'gated' | 'not_linked' | 'superseded'

export async function relinkScript(panel: PanelObject, target: string): Promise<RelinkOutcome> {
  const pj = currentProjectId()
  await useScriptRunStore.getState().run(target)
  if (currentProjectId() !== pj) return 'superseded'
  const st = useScriptRunStore.getState().byScript[target]
  if (st?.error) return isGatePhase(st.phase) ? 'gated' : 'not_linked'
  // `force`：注册表刚写过，不复用写之前发出的在途请求
  await refreshAssetsAndSync({ force: true })
  if (currentProjectId() !== pj) return 'superseded'
  const cap = useAssetStore.getState().byId[panel.fileId]?.capability
  if (cap?.status !== 'editable' || cap.script !== target) return 'not_linked'
  requestRender(panel, true)
  useUiStore.getState().setStatus(msg('relink.linked', { script: target }, 'inspector'), 'done')
  return 'linked'
}
