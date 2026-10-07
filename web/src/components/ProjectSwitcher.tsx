import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Folder, House } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { Button, IconButton } from './ui/Button'

/**
 * 顶栏左上角的项目名：点一下开 / 关左栏的「工作区」抽屉（`left/WorkspaceList.tsx`）。
 *
 * 以前这里自己弹一份菜单（当前 · 已打开 · 最近六条 · 浏览 / 新建……）。抽屉做出来
 * 之后那就成了同一份列表的第二个入口、而且是截断的那一份——两处各显示一遍，
 * 用户要记两个地方、我们要维护两套判据。所以这里只剩「去那一处」。
 * 开合语义与左轨那颗钮完全相同（`railClick`：开着再点就收起）。
 */
export function ProjectSwitcher() {
  const { t } = useTranslation('project')
  const project = useProjectStore((s) => s.project)
  const expanded = useUiStore((s) => s.leftOpen && s.leftTab === 'workspace')

  if (!project?.open) return null

  return (
    /* 面包屑的两颗钮是同一件事，只能有一副壳（2026-09-15 打磨 T1）：与旁边的文档钮
       同为 `Button size="md"` */
    <Button
      size="md"
      className="min-w-0 max-w-56 shrink text-ink-2"
      aria-label={t('switcher.trigger', { name: project.name })}
      aria-expanded={expanded}
      data-project-switcher
      onClick={() => useUiStore.getState().railClick('workspace')}
    >
      <Folder size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />
      <span className="truncate">{project.name}</span>
    </Button>
  )
}

/**
 * 顶栏最左端的「回到项目列表」：离开编辑器、回 Project Picker。
 *
 * 走的是设置「切换项目」/ 桌面菜单「打开项目」同一个 `showPicker`——离开前的收尾
 * （连续编辑、自动保存冲刷）与浏览器历史那一格都在那里，这里不另写一份。项目本身
 * 不关：Picker 上「返回当前项目」或再点一次同一个项目，文档原样还在。
 */
export function HomeButton({ children }: { children?: ReactNode } = {}) {
  const { t } = useTranslation('project')
  const switching = useProjectStore((s) => s.switching)
  const go = () => useProjectStore.getState().showPicker()
  // 顶栏把品牌并进这颗钮（2026-10-07 设计审计 §10.1 P2）：给了 children 就是一颗带字的胶囊（品牌标 + 产品名），
  // 可达名仍是「回到项目列表」——同一颗钮、同一个动作，只是不再在旁边另摆一个不能点的品牌
  if (children) {
    return (
      <Tip label={t('switcher.home')}>
        <Button
          size="md"
          data-home-button
          aria-label={t('switcher.home')}
          disabled={switching}
          onClick={go}
          className="shrink-0 gap-2 px-2 text-ink"
        >
          {children}
        </Button>
      </Tip>
    )
  }
  return (
    <IconButton
      label={t('switcher.home')}
      data-home-button
      // 切换进行中 showPicker 本来就什么都不做（见它的注释）；灰掉，别让这一下像是没点上
      disabled={switching}
      onClick={go}
    >
      <House size={ICON_SIZE.md} />
    </IconButton>
  )
}
