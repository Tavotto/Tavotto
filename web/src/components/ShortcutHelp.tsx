import { ALT, MOD } from '@/lib/utils'
import { useUiStore } from '@/store/uiStore'
import { Dialog } from './ui/Dialog'

/** 快捷键帮助（按 ? 或从 ⌘K 打开）。分组与实际实现一一对应，不列不存在的键。 */
const GROUPS: { title: string; rows: [string, string][] }[] = [
  {
    title: '通用',
    rows: [
      [`${MOD}K`, '命令面板'],
      [`${MOD}Z / ⇧${MOD}Z`, '撤销 / 重做'],
      [`${MOD}S`, '保存为画布文件'],
      [`${MOD}E`, '导出'],
      ['?', '本帮助'],
    ],
  },
  {
    title: '选择与编辑',
    rows: [
      [`${MOD}A`, '全选'],
      [`${MOD}C / ${MOD}V`, '复制 / 粘贴对象（可跨布局文档）'],
      [`${MOD}D`, '原位复制所选'],
      ['Delete', '删除对象；图内编辑时 = 隐藏元素（可恢复）'],
      ['Enter', '编辑所选：文字进入编辑，可参数化面板进图内编辑'],
      ['Esc', '逐层退出：文字编辑 → 图内元素 → 编辑态 → 清空选择'],
      [`${MOD}↑ / ${MOD}↓`, '上标 / 下标（属性面板的文字输入框内）'],
      ['方向键 / ⇧+方向键', '微调 0.5mm / 5mm'],
    ],
  },
  {
    title: '排列与层级',
    rows: [
      [`${MOD}] / ${MOD}[`, '上移 / 下移一层'],
      [`⇧${MOD}] / ⇧${MOD}[`, '置顶 / 置底'],
    ],
  },
  {
    title: '视图',
    rows: [
      [`${MOD}+ / ${MOD}−`, '放大 / 缩小'],
      [`${MOD}0 / ${MOD}1`, '100% / 适应画布'],
      [`${MOD}+滚轮`, '缩放画布'],
      ['Space+拖动', '平移画布'],
    ],
  },
  {
    title: '工具',
    rows: [
      ['V / T / A / R / O / L', '选择 / 文字 / 箭头 / 矩形 / 椭圆 / 直线'],
      [`${ALT}+拖角点`, '非等比自由拉伸'],
      [`${ALT}⏎ 或 ${MOD}⏎（文字编辑中）`, '插入换行；单按 ⏎ 提交'],
    ],
  },
]

export function ShortcutHelp() {
  const open = useUiStore((s) => s.shortcutHelpOpen)
  const setOpen = useUiStore((s) => s.setShortcutHelpOpen)
  return (
    <Dialog open={open} onOpenChange={setOpen} title="快捷键" size="md">
      <div className="flex flex-col gap-3">
        {GROUPS.map((g) => (
          <div key={g.title}>
            <h3 className="mb-1 text-xs font-medium uppercase tracking-[.06em] text-ink-3">
              {g.title}
            </h3>
            <ul className="flex flex-col">
              {g.rows.map(([key, desc]) => (
                <li key={key} className="flex h-6 items-center gap-3">
                  <span className="w-40 shrink-0 font-mono text-xs text-ink">{key}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-2">{desc}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Dialog>
  )
}
