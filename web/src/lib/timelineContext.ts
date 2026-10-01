import { useDocumentStore } from '@/store/documentStore'
import { useTimelineStore } from '@/store/timelineStore'
import { askConfirm, useUiStore } from '@/store/uiStore'

/**
 * 时间线的**上下文**：项目代际（`timelineStore.gen`，每次换项目 +1）+ 排版 id。
 * 时间线抽屉（含命名输入）里一切本地状态都属于某一个上下文（Codex #679）。
 */
export const timelineCtxKey = (gen: number, docId: string) => `${gen}:${docId}`

export const currentTimelineCtx = () =>
  timelineCtxKey(useTimelineStore.getState().gen, useDocumentStore.getState().documentId)

/**
 * **await 之后改本地状态的唯一出口**（Codex #679）：在 await 之前调它，拿到的守卫
 * 只在上下文没变时才执行回调；换了项目 / 排版才回来的完成一律不动任何本地状态。
 * 已经发生的服务端事实（节点建好了、删掉了）照常——由新上下文的列表重取反映，
 * 切回来时也一样。
 *
 * 用到它的完成点（新增一处 await 之后改状态的，加进这张清单，并在
 * `VersionDialog.test.tsx` 的「换走之后才回来」参数化用例里加一行）：
 *
 * - 抽屉 `reload`：列表 / 预算（另有请求序号挡交叠的重取）；旧上下文的闭包连请求都
 *   不发——行操作（命名、取消命名、复制、删除、改名）做完调的就是它；
 * - 抽屉 `saveNamed`：清空名字框；
 * - 行 `commitName`：发请求前（失焦可能晚于换上下文；jsdom 里卸载不触发失焦，这一条
 *   没有用例能走到）；
 * - 行 `remove`：确认框回答之后清预览、发删除；
 * - `restoreNode`：确认框之后切画布、「恢复前」节点之后写入排版 / 清预览 / 状态条
 *   （这两处改的是全局文档：换了排版才回来的话，A 的节点会被写进 B）；
 * - 命名输入 `save`：收起输入。
 *
 * 用户正在编辑的输入（抽屉名字框、行内改名）也按上下文记账：草稿记着
 * 它属于哪个上下文，换了就当作空的——提交发生在切换**之后**，这个守卫管不到。时间线的
 * 确认框经 `askTimelineConfirm`，换了上下文当场收起。
 *
 * 错误**既按上下文记账又经这个守卫**：抽屉的 `setError` 与浮层的 `failure` 记着发出它的
 * 上下文、只显示此刻的那一份；错误槽只有一个，所以旧上下文的完成连写都不写（否则会把
 * B 自己的错误顶掉或清空）。
 *
 * 同一家族、但只用**按上下文记账**的：busy（记着是哪个上下文在忙，
 * 完成时只摘自己挂的标记——不摘的话 A → B → A 回来 A 会一直在忙）、列表本身。
 * 不经任何守卫的两处，理由写在这里免得被当成漏网：抽屉 `select` 取正文——换项目的
 * `clear()` 与换排版的 effect 都会清掉预览，它自己的「还是不是当前预览」判据已经是假；
 * 预览对话框 `restore` 的 busy——对话框随预览一起卸载，状态无处可落。
 */
export function afterAwait(ctx: string = currentTimelineCtx()) {
  return <T>(fn: () => T): T | undefined => (currentTimelineCtx() === ctx ? fn() : undefined)
}

/**
 * 时间线自己的确认框（删除节点、跨画布恢复）：**换了上下文就当场收起、按「取消」作答**。
 * 确认框说的是 A 的节点，留在 B 的画面上会让人以为在确认 B 的事；`afterAwait` 只保证
 * 答了也不执行，这里连问都不再挂着（Codex #679）。订阅两个 store，同步收起，不等 effect。
 */
export async function askTimelineConfirm(req: Parameters<typeof askConfirm>[0]): Promise<boolean> {
  const ctx = currentTimelineCtx()
  const answer = askConfirm(req)
  const mine = useUiStore.getState().confirm
  const dismissIfStale = () => {
    if (currentTimelineCtx() === ctx) return
    const ui = useUiStore.getState()
    if (mine && ui.confirm === mine) {
      ui.setConfirm(null)
      mine.resolve(false)
    }
  }
  const stops = [useDocumentStore.subscribe(dismissIfStale), useTimelineStore.subscribe(dismissIfStale)]
  try {
    return await answer
  } finally {
    stops.forEach((stop) => stop())
  }
}
