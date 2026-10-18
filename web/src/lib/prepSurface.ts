/**
 * 「此刻可见的准备面」——准备界面只有一个焦点（`projectPreparationStore.focus`），谁在屏幕上由这一处说了算：
 * 聚焦的条目存在、没被教程 / 引导整个藏起来、`uiStore.guideCard` 不是收起，就是 `{ key, mode: 'card' | 'pill' }`；否则 `null`。
 *
 * 三个消费者共用它，不各写一遍：`PreparationCard`（`hidden` / 角标）、`NativeSessionCards`（桌面会话让位给可见的准备面）、
 * 自动进入编辑的守卫（落地前「用户还在看这张展开的卡」）。纯函数、只吃数据：store 里的守卫可以直接用，不引入 store 之间的 import 环。
 */
import type { PrepEntry } from '@/store/projectPreparationStore'
import type { GuideCardMode } from '@/store/uiStore'

export interface PrepSurfaceInputs {
  focus: string | null
  entries: Readonly<Record<string, PrepEntry>>
  guideCard: GuideCardMode
  /** 教程项目 / 新手引导进行中：准备界面整个不显示 */
  hidden: boolean
}

export function visiblePrepSurface(i: PrepSurfaceInputs): { key: string; mode: 'card' | 'pill' } | null {
  if (i.hidden || i.guideCard === 'closed' || !i.focus || !i.entries[i.focus]) return null
  return { key: i.focus, mode: i.guideCard }
}
