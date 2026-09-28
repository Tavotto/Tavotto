/**
 * 一次显式保存 / 打开的**上下文**：入口处取一次，整条异步链只用这份捕获值（ADR 0096 §四之四）。
 *
 * 同一家族前后三次评审意见（请求途中切项目、排队途中切排版、`saveNow()` 途中切排版）都是
 * 「await 之后现读此刻的 documentId / 项目 / 绑定」——读到的已经是用户切过去的那一份，
 * 于是 A 的显式保存落到 B 头上，或者 B 被写、被弹一个没人要的「存进项目」。逐个 await 补
 * 守卫补不完，所以换成结构：
 *
 * - 入口（⌘S 的 `runManualSave`、对话框的另存为 / 打开）调 `captureSaveContext()`；
 * - 链上**每一个 await 之后**，要继续做「作用在此刻界面上」的事之前，先问 `stillCurrent(ctx)`；
 *   不是了就 `reportSaveSkipped(ctx)`（显式保存没写成必须说出来），不再往下走；
 * - 已经发生了的事（后端写成了的修订号、那份排版的绑定）照样记在 `ctx` 名下——那是事实，
 *   不因为用户切走了就不记。
 *
 * 判据三维，各自有别的维度替代不了的那一刻：载入代次（`loadSeq`：全局单调，换排版必然换代次，
 * 同一份排版被重新载入也算换了——所以不再单比 `documentId`）、项目（没绑定的排版切项目时，
 * 只有它看得见）、入口那一刻绑定的项目文件（另存为改绑之后，旧的 ⌘S 不再是用户要的那次写）。
 *
 * **链上的 await 点清单**（新增 await 就在这里加一行；`projectSave.test.ts` 的参数化用例逐个切走）：
 *
 * | 入口 | await | 之后 |
 * |---|---|---|
 * | ⌘S `runManualSave` | `saveNow()` | `stillCurrent` 否 → `reportSaveSkipped`，不写项目、不弹「存进项目」 |
 * | ⌘S `writeBoundProjectFile` | 排队等前一项（`tail`） | `writeOnce` 开头 `stillCurrent` 否 → 报「没写成」 |
 * | 同上 | 同上下文前一次的结果 | 前一次没写成 → 不再写（那次已经说过了） |
 * | 同上 | `saveLayout` | 修订号 / 绑定记在 `ctx` 名下；冲突岔口只在 `stillCurrent` 时开 |
 * | 另存为 `doSave` | `saveLayout` 成功 | 事实（修订号、别名、绑定）记在 `ctx` 名下；状态条照说写到了哪（点名文件）；关对话框走 `ifStillCurrent` |
 * | 同上 | `saveLayout` 失败 | 冲突岔口 / 错误进对话框走 `ifStillCurrent`；切走了改在状态条上说 |
 * | 打开 `doLoad` | `fetchLayout` 成功 | 修订号记在 `ctx` 名下；`stillCurrent` 否 → 不打开、不碰对话框，状态条报 `layout.openSkipped` |
 * | 同上 | `fetchLayout` 失败 | 错误进对话框走 `ifStillCurrent`；切走了改在状态条上说 |
 * | 同上 | `openLayoutDocument` | 只关对话框（换排版是它自己做的，此刻的上下文就是它换来的） |
 *
 * 写成之后**立刻**发「排版写成了」（`emitLayoutSaved`，带写出去那份的快照）：在上表任何「之后」的步骤
 * 之前，时间线的「保存」点不受后续步骤成败影响（ADR 0101 §7）。
 *
 * 不受上下文约束的只有两样：状态条（全局通知，话里点名了是哪个文件）与对话框自己的 `busy` /
 * 在路上标记（描述的是这个组件发出的请求，请求结束就该复位，不复位对话框会永远转圈）。
 * 另存为成功后原先还有一次 `fetchLayoutNames()` 刷新名单（0.1.0 起）——对话框紧接着就关了，
 * 下次打开会重取，这次刷新没人看得见，连同它的 await 点一起删掉。
 */
import { msg } from '@/i18n'
import { currentProjectId } from '@/lib/session'
import { captureMoment, type MomentSnapshot } from '@/lib/timelineCheckpoint'
import { activeProjectFile, useDocumentStore } from './documentStore'
import { useUiStore } from './uiStore'

export interface SaveContext {
  readonly documentId: string
  readonly loadSeq: number
  readonly pj: string | null
  /** 入口那一刻绑定的项目文件（相对项目根）；没绑定 = `null` */
  readonly file: string | null
  /**
   * 同一刻的**排版时间线快照**（`captureMoment()`：时间线上下文 + 这份文档 + 面板图源）。
   * 写成之后发「排版写成了」时带它：「保存」点只打给被存的那一份、拍的是写出去的那份内容
   * （ADR 0101 §7；Codex #679）。本机保存与另存为在入口就序列化，所以就是这一份；⌘S 写回
   * 项目文件排队之后才 `buildProject()`，在那一刻另取（`projectSave.writeOnce`）。
   *
   * 时间线上下文与上面三维**不是同一个判据**：节点归档在排版 id 名下，同一份排版被重新载入
   * （`loadSeq` 变）或改绑了文件（`file` 变）之后，节点仍属于它——那两维管的是「还要不要
   * 继续写 / 弹框」；项目那一维时间线用项目代际（`timelineStore.gen`），与 pj 同一时刻变。
   * 两份在入口**同一次**捕获里取，不各算各的。
   */
  readonly moment: MomentSnapshot
}

export function captureSaveContext(): SaveContext {
  const s = useDocumentStore.getState()
  return {
    documentId: s.documentId,
    loadSeq: s.loadSeq,
    pj: currentProjectId(),
    file: activeProjectFile()?.file ?? null,
    moment: captureMoment(),
  }
}

/** 此刻开着的还是不是入口那一刻的那份排版、那个项目、那个绑定 */
export function stillCurrent(ctx: SaveContext): boolean {
  const s = useDocumentStore.getState()
  return (
    s.loadSeq === ctx.loadSeq &&
    currentProjectId() === ctx.pj &&
    (activeProjectFile()?.file ?? null) === ctx.file
  )
}

/**
 * await 之后作用于**此刻界面**的一步（对话框的开关、名单、错误、冲突岔口）：上下文还在才做。
 * 返回做没做——没做的话调用方负责把结果换个地方说（状态条），不静默。
 */
export function ifStillCurrent(ctx: SaveContext, apply: () => void): boolean {
  if (!stillCurrent(ctx)) return false
  apply()
  return true
}

/** 按下保存之后切走了、这次没写进项目：说出来（error 色调），不静默 */
export function reportSaveSkipped(ctx: SaveContext): void {
  useUiStore
    .getState()
    .setStatus(
      ctx.file
        ? msg('save.projectSkipped', { file: ctx.file }, 'workspace')
        : msg('save.skippedSwitched', undefined, 'workspace'),
      'error',
    )
}
