import { currentProjectId } from '@/lib/session'

/**
 * 「这个画布文件名下，本窗口读到 / 写成功的最后一份内容是什么」。
 *
 * 与 `documentStore` 里的 `diskRevision` 是同一件事的第二个键空间：那边按
 * `documentId` 记自动保存槽位，这边按**画布文件名**记命名画布文件。两边共用
 * 后端那一份 `_revision_conflict`（`base_revision` + `absent` 哨兵）。
 *
 * **只活一个窗口的生命周期，不进 localStorage。** 它回答的是「我这次会话读到
 * 的是哪一份」，持久化等于把上一次开机时的观察当成这一次的基线——而这中间
 * 别人改过磁盘的话，那正是这条判据要挡的事。
 *
 * 拿不出条目 = 本窗口从没确认过这个名字下的磁盘状况。此时的基线是
 * `REVISION_ABSENT`（「我以为那里没有我的内容」），后端于是把「磁盘上真有
 * 一份我从没读过的内容」判成冲突，交给用户点头——ADR 0024 §3b：
 * **基线缺席 = 写之前先确认，没有例外**。
 *
 * **键里必须带项目 id。** 命名画布文件落在**当前项目**的 `tavottofile/` 下
 * （`app.project_layout_dir()`），两个项目各有一张「Fig 1」是再正常不过的事。
 * 只按名字记的话，在 A 里存过的那份 hash 会被当成 B 里同名画布的基线——
 * 后果不是静默覆盖（hash 对不上，后端一律 409，这条判据两边都钉住了），
 * 而是**一次没有道理的「仍然覆盖」提示**。那更坏一点点：反复弹一个用户看不
 * 懂的确认框，教会的是无脑点确认，而真该拦的那一次也就跟着被点过去了。
 */
const revisions = new Map<string, string>()

/** `pj::name`。`null` 项目（未打开项目时退回数据目录 layouts/）也是一档。 */
const keyOf = (name: string) => `${currentProjectId() ?? ''}::${name}`

export const knownLayoutRevision = (name: string): string | undefined => revisions.get(keyOf(name))

export function rememberLayoutRevision(name: string, revision: string | null): void {
  if (revision) revisions.set(keyOf(name), revision)
  else revisions.delete(keyOf(name))
}

/** 仅供用例：模块级 Map 会跨用例活下来 */
export const forgetLayoutRevisions = (): void => revisions.clear()
