/**
 * 自动保存把工作副本写进项目的**前端侧开关与节奏**（ADR 0211 第二期 PR 2）。
 *
 * 开关的权威在后端（`layoutdir.WRITER_ENABLED`，经 `GET /api/project` 的 `autosave_into_project` 告知）：
 * 关着时前端**一个字节的行为都不变**——不带 `project_write`、不起多余的定时器。本文件不 import store。
 *
 * 节奏：本机槽位仍是停手 1 s 就写（便宜、抗崩溃）；写进项目的那一笔更稳——同步盘 / git 会盯着
 * `tavottofile/` 变化，每个 1 s 的停顿都落一次只会制造噪声。
 *   - 停手满 `PROJECT_QUIET_MS`（5 s）写一次；
 *   - 一直在改、停不下来时，距离上一次写进项目（或第一次未写的编辑）满 `PROJECT_MAX_MS`（30 s）强制写一次，
 *     最多丢 30 s 的「项目里那份」，而本机槽位那份从不落后于 1 s；
 *   - 手动保存、离开页面、切文档的冲刷一律顺带写项目（用户在乎的时刻）。
 */
export const PROJECT_QUIET_MS = 5000
export const PROJECT_MAX_MS = 30000

let enabled = false
let flagOn = false

/**
 * projectStore 在项目状态变化时调：`on` = 后端开着开关、且这个项目可写（才带 `project_write`）；
 * `flag` = 后端开着开关本身（`autosave_into_project`，与项目是否可写无关）——⌘S 的含义与顶栏保存状态据它切
 * （PR 3：开着 = 「始终保存」，⌘S 给这一版起名字；关着 = 应急回退，行为同改造前）。
 */
export function setProjectWriteCapability(on: boolean, flag = on): void {
  enabled = on
  flagOn = flag
}

export const projectWriteEnabled = (): boolean => enabled

/** 后端开着「自动保存写进项目」（只读文件夹里也是 true：那时本机存、界面说只读） */
export const alwaysSavedEnabled = (): boolean => flagOn

/** 随 `project_write` 发给后端的描述：绑定的文件名与基线修订号；都没有 = 还没起过名 */
export interface ProjectWriteSpec {
  name?: string
  base?: string
}

/** 后端回的 `project_layout`（见 `app._autosave_into_project`） */
export type ProjectLayoutResult =
  | { state: 'saved'; name: string; file: string; revision: string }
  | { state: 'external_change'; name: string; revision: string | null; summary?: unknown }
  | { state: 'read_only' | 'busy' | 'skipped' | 'refused' | 'failed' }
