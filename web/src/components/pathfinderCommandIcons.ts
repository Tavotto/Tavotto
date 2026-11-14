import {
  ArrowLeftRight,
  CompoundPath,
  OffsetPath,
  OutlineStroke,
  PathDivide,
  PathExclude,
  PathIntersect,
  PathMinusFront,
  PathUnite,
  Ungroup,
} from '@/components/ui/icons'
import type { PathfinderCommand } from '@/store/pathfinderActions'

/** 路径查找器命令的图标（属性栏、右键菜单、命令面板同一张表；命令本身在 `store/pathfinderActions.PATHFINDER_COMMANDS`） */
export const PATHFINDER_COMMAND_ICONS: Record<PathfinderCommand['id'], typeof PathUnite> = {
  'pathfinder-unite': PathUnite,
  'pathfinder-minus-front': PathMinusFront,
  'pathfinder-intersect': PathIntersect,
  'pathfinder-exclude': PathExclude,
  'pathfinder-divide': PathDivide,
  'make-compound': CompoundPath,
  'release-compound': Ungroup,
  'offset-path': OffsetPath,
  'outline-stroke': OutlineStroke,
  'reverse-path': ArrowLeftRight,
}
