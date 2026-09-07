/**
 * 当前项目**叫什么**——`projectStore.project.name` 的一份投影（审计 T04）。
 *
 * 「最近文档」的索引住在 `documentStore`（本机 localStorage，跨项目共用一份），
 * 而权威在 `projectStore`；让 documentStore 去 import projectStore 会造出
 * store 之间的循环依赖（projectStore 本来就 import documentStore）。所以由
 * **持有权威的那一侧**在认领项目时写进来，读的一侧只读。
 *
 * 这是运行时投影，不落盘、不进文档：写进 `RecentDoc` 的那一份才是持久的，
 * 而它记的是「写下那一刻这个项目叫什么」——项目后来改了名，旧条目照实显示
 * 当时的名字，不去追认。
 */
import { t } from '@/i18n'

let label: string | null = null

export function currentProjectLabel(): string | null {
  return label
}

export function setCurrentProjectLabel(next: string | null | undefined): void {
  label = next || null
}

/**
 * 这条最近文档属于**别的**项目时该显示的那句话；属于当前项目、或压根没记过
 * 归属（旧条目）时回 `null`。
 *
 * 「最近文档」的索引跨项目共用一份（localStorage 一个键），别的项目的文档
 * 不标出来的话，用户会把它当成本项目的一份版本打开——而它引用的素材在这个
 * 项目里根本不存在（审计 T04）。
 *
 * **三档不许压成两档**：`projectId` 缺席是「不知道」。当成「别的项目」会给
 * 每一条旧条目挂上一个假标记，当成「当前项目」则是替它编一个归属。
 */
export function foreignProjectLabel(
  entry: { projectId?: string; projectName?: string },
  current: string | null,
): string | null {
  if (!entry.projectId || entry.projectId === current) return null
  return entry.projectName
    ? t('topbar.fromProject', { ns: 'workspace', name: entry.projectName })
    : t('topbar.fromOtherProject', { ns: 'workspace' })
}
