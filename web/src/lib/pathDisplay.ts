/**
 * 界面上怎么显示一条磁盘路径（审计 T34）。
 *
 * 备份目录在 macOS 上是 `/Users/<你>/Library/Application Support/Tavotto/cache/
 * original_backups` 这种长度。把它整条铺进一句话里，用户得读完才看得见「备份」
 * 两个字——而那正是他要的信息。所以正文只显示末级目录，全路径与复制入口收进
 * 展开项。全路径**不许消失**：它是用户去文件管理器里找回原件的唯一线索。
 */

/** 目录的末级名；末尾的分隔符不算一级。没有分隔符时原样返回，绝不回空串。 */
export function dirTail(dir: string): string {
  return dir.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || dir
}
