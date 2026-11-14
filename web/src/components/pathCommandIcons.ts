import { AlignCenterHorizontal, Link2, Minus, Plus, Replace, Spline, SquareMousePointer, Unlink2 } from '@/components/ui/icons'
import type { PathCommand } from '@/store/pathActions'

/** 路径命令的图标（命令面板与右键菜单同一张表；只是外观，命令本身在 `store/pathActions.PATH_COMMANDS`） */
export const PATH_COMMAND_ICONS: Record<PathCommand['id'], typeof Plus> = {
  'pen-tool': Spline,
  'edit-anchors': SquareMousePointer,
  'add-anchor': Plus,
  'delete-anchor': Minus,
  'convert-anchor': Replace,
  'cut-path': Unlink2,
  'join-paths': Link2,
  'average-anchors': AlignCenterHorizontal,
}
