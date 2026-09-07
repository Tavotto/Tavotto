/**
 * 「新建项目」那个名字输入框的判据：它必须是**一个平台安全的路径分量**。
 *
 * 为什么这条判据存在：新建流程分两步——先由系统选择器（桌面）或目录浏览器
 * （浏览器模式）选好**上级目录**，再让用户输入一个名字，两者拼成
 * `parent/name` 发给 `/api/projects/open?create=true`。后端的
 * `mkdir(parents=True, exist_ok=True)` 会把这条路径老老实实解析掉，于是
 * `..` / `../other` 会把项目建到对话框上写着的那个目录**之外**（甚至把一个
 * 已经存在的上级目录当成新项目初始化），`nested/name` 会顺手建出中间目录。
 * 名字栏收的是叶子名，不是路径。
 *
 * **规则不新写一份**：路径分量合不合法，仓库里已经有唯一权威
 * `lib/exportName.checkFilename`——它按最严的平台（Windows）写，且与
 * `engine/exportreq.check_filename` 由 `tests/golden/filename_vectors.json`
 * 逐条钉在一起。目录名与文件基名在这件事上是同一条规则：`/` `\` 归
 * `illegal_char`、`.` 与 `..` 归 `dot_only`、`CON`/`COM1` 归 `reserved_name`、
 * 结尾的点归 `trailing_dot`、首尾空白归 `whitespace_edge`、控制字符归
 * `control_char`。这里只给它一个说得出用途的名字，并把「项目名为什么也归它
 * 管」写在能被读到的地方。
 *
 * 界面挡住不是安全边界：后端在 `app.api_projects_open` 里另有一道**只守越界**
 * 的判据（路径里不许出现 `.` / `..` 分量，末位分量过同一个 `check_filename`）。
 * 两边刻意不是镜像关系——后端看不见「用户选的上级目录是哪一个」，它能守的
 * 只有越界本身。
 */
import { checkFilename, type FilenameReason } from './exportName'

/** 项目名不合法的原因。闭集，与 `FilenameReason` 同一套取值 */
export type ProjectNameProblem = FilenameReason

/**
 * 这个名字能不能当成一级目录名。合法回 `null`，否则回原因码。
 *
 * **不 trim**：判的就是将要被拼进路径的那个字符串本身。先 trim 再判的话
 * `"figs "` 会被悄悄改成 `"figs"`，用户在磁盘上看到的名字与他输入的不一样；
 * 而 `whitespace_edge` 这一档也会永远取不到（`String.trim()` 恰好剥掉的就是
 * 它认的那批字符），一条判不出东西的分支比没有更坏。
 */
export function checkProjectName(name: string): ProjectNameProblem | null {
  return checkFilename(name)
}
