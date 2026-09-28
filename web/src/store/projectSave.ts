/**
 * ⌘S 写回项目里的排版文件（ADR 0096）。
 *
 * | 这份排版 | ⌘S |
 * | --- | --- |
 * | 绑定了当前项目里的 `tavottofile/<名>.json` | 本机自动保存照存，再原子写回那个文件 |
 * | 开着项目、还没有项目文件 | 弹「存进项目」命名框（与另存为同一个表单），存成之后绑定 |
 * | 没开项目 | 只存本机，提示里说「存在本机」 |
 *
 * 自动保存**只**写本机槽位——没按保存就不动项目里的文件。项目文件被别处改过时
 * （git pull、另一台电脑、另一个窗口），绑定里记着的修订号对不上，后端 409，这里
 * 把用户带到另存为那一屏的同一个冲突岔口，不静默覆盖。
 */
import { msg } from '@/i18n'
import { ApiError, REVISION_ABSENT, backendErrorText, saveLayout, type DiskDocumentSummary } from '@/lib/api'
import { rememberLayoutRevision } from '@/lib/layoutRevision'
import { readProjectFile } from '@/lib/projectFile'
import {
  activeProjectFile,
  projectFileSnapshot,
  setProjectFile,
  useDocumentStore,
} from './documentStore'
import { reportSaveSkipped, stillCurrent, type SaveContext } from './saveContext'
import { useUiStore } from './uiStore'

/**
 * 写回队列里的一项。`started` 之前同一个保存上下文再按 ⌘S 都并进它（它开始时现读绑定与内容）。
 */
type QueuedWrite = { key: string; started: boolean; promise: Promise<boolean> }
/** 全局串行的队尾：不论哪份排版，同一时刻至多一次在路上 */
let tail: Promise<unknown> = Promise.resolve()
/** 每个保存上下文最近排进队列的那一项（落定后删掉） */
const lastByContext = new Map<string, QueuedWrite>()
/** 队列里还没落定的项数：0 = 空闲，这次当场开始（同步读绑定与内容，不等一个微任务） */
let pending = 0

const keyOf = (ctx: SaveContext) => JSON.stringify([ctx.documentId, ctx.loadSeq, ctx.pj, ctx.file])

/**
 * 写回 `ctx` 那一刻绑定的项目文件。返回是否写成；**没写成时一定已经把话说完**（状态条 /
 * 冲突弹窗）——这是用户的显式保存，静默不写等于骗他「存好了」。
 *
 * 基线是**绑定里的修订号**（这份工作副本基于项目文件的哪一版），不是本窗口读到过
 * 什么——刷新之后它照样在，外部修改照样挡得住。不知道（`null`）就发 `absent`：
 * 磁盘上真有一份的话让用户点头（ADR 0024 §3b，基线缺席 = 写之前先确认）。
 *
 * **串行，按保存上下文合并**（ADR 0096 评审 P2）：⌘S 连按两下时，两次都读到同一个旧绑定、
 * 并发发出去的话，先到的那次把修订号推进了，后到的那次带着旧基线撞上 409——而「别处」
 * 就是这个窗口自己。所以写入串行：排着的那一次**现读**绑定（拿到刚推进的修订号、刚改过
 * 的内容）再写；同一个上下文还没开始的那次，再按几次都并进它；同一个上下文前一次没写成就
 * 不再排（冲突岔口已经打开）。**合并只在同一个上下文之内**：A 还有一次排着时切到 B 按 ⌘S，
 * B 排在 A 后面自成一项。
 *
 * await 点（`saveContext.ts` 的清单）：① 排队等前一项 → 开写前 `stillCurrent(ctx)`，否则报「没写成」；
 * ② 同上下文前一次的结果 → 同 ①；③ `saveLayout` → 已写成的事实（修订号、那份排版的绑定）记在
 * `ctx` 名下，冲突岔口只在 `stillCurrent(ctx)` 时打开。
 */
export function writeBoundProjectFile(ctx: SaveContext): Promise<boolean> {
  const key = keyOf(ctx)
  const prevSame = lastByContext.get(key)
  if (prevSame && !prevSame.started) return prevSame.promise
  const entry: QueuedWrite = { key, started: false, promise: Promise.resolve(false) }
  const run = async (): Promise<boolean> => {
    entry.started = true
    // ② 同一个上下文前一次没写成：用户正在裁决（冲突岔口 / 已报错），这次不带着旧基线再撞一次
    if (prevSame && !(await prevSame.promise.catch(() => false))) return false
    return writeOnce(ctx)
  }
  // ① 前一项抛了也照样轮到这一项：队尾一旦停在 rejected 上，之后所有保存都会被跳过
  const started = pending === 0 ? run() : tail.then(run, run)
  pending += 1
  entry.promise = started.finally(() => {
    pending -= 1
    if (lastByContext.get(key) === entry) lastByContext.delete(key)
  })
  lastByContext.set(key, entry)
  tail = entry.promise.catch(() => undefined)
  return entry.promise
}

async function writeOnce(ctx: SaveContext): Promise<boolean> {
  // 轮到时已经切走了：不把此刻开着的那份写进按 ⌘S 时那份的文件——但要说出来
  const binding = stillCurrent(ctx) ? activeProjectFile() : null
  if (!binding) {
    reportSaveSkipped(ctx)
    return false
  }
  // 现读：排队期间前一次写成推进了修订号（同一个上下文 = 同一个文件）
  const ui = useUiStore.getState()
  const edited = projectFileSnapshot()
  const pd = useDocumentStore.getState().buildProject()
  try {
    const res = await saveLayout(binding.name, pd, binding.revision ?? REVISION_ABSENT, {
      target: 'project',
    })
    // ③ 写成了是事实：修订号与那份排版的绑定记在 ctx 名下，不管此刻开着的是谁
    const name = res.name ?? binding.name
    const file = res.file ?? binding.file
    rememberLayoutRevision(name, res.revision, ctx.pj)
    // 写的途中这份排版被另存为改绑到了别的文件：新绑定是用户更晚的决定，这次的回执
    // 只属于旧文件——整份写回去的话，之后的 ⌘S 会静默地又写回旧文件（#674 评审 P2）
    const now = readProjectFile(ctx.documentId)
    if (now && now.projectId === ctx.pj && now.file === binding.file) {
      // 写的途中又改过：项目里这份已经落后了，圆点不能灭
      setProjectFile({ ...now, name, file, revision: res.revision, dirty: edited() }, ctx.documentId)
    }
    ui.setStatus(msg('save.doneProject', { file }, 'workspace'))
    return true
  } catch (e) {
    const revision =
      e instanceof ApiError && e.status === 409 && e.body.code === 'external_change'
        ? e.body.revision
        : null
    // 冲突岔口只在入口那一刻的上下文还开着时打开：切走之后再弹，「仍然覆盖」会带着此刻的
    // pj 把 A 的 409 hash 发到 B 的同名文件上
    if (typeof revision === 'string' && stillCurrent(ctx)) {
      ui.setStatus(msg('save.projectConflict', { file: binding.file }, 'workspace'), 'error')
      ui.setLayoutOpen(true, 'saveToProject', {
        name: binding.name,
        conflict: {
          name: binding.name,
          revision,
          summary: ((e as ApiError).body.summary as DiskDocumentSummary | null) ?? null,
        },
      })
    } else if (typeof revision === 'string') {
      ui.setStatus(msg('save.projectConflict', { file: binding.file }, 'workspace'), 'error')
    } else {
      ui.setStatus(
        msg('save.projectFailed', { file: binding.file, reason: backendErrorText(e) }, 'workspace'),
        'error',
      )
    }
    return false
  }
}
