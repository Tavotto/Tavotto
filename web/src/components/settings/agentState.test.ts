/** 一级列表上的版本号：只有数字部分；抽不出来就不显示（ADR 0038）。 */
import { describe, expect, it } from 'vitest'
import { agentVersionLabel } from './agentState'

describe('版本号只显示数字部分', () => {
  it('`codex-cli 0.151.0` → `0.151.0`；`2.0.14 (Claude Code)` → `2.0.14`', () => {
    expect(agentVersionLabel('codex-cli 0.151.0')).toBe('0.151.0')
    expect(agentVersionLabel('2.0.14 (Claude Code)')).toBe('2.0.14')
    expect(agentVersionLabel('1.2.3-beta.1')).toBe('1.2.3-beta.1')
  })

  it('抽不出版本号就不显示——shim 的报错行带着完整路径，一级页面一个字都不能出', () => {
    // 真机抓到的形状：`--version` 的第一行是 bash 的报错
    expect(agentVersionLabel('/Users/x/.claude_env/bin/claude: line 13: /var/folders/…: No such file')).toBeNull()
    expect(agentVersionLabel('')).toBeNull()
    expect(agentVersionLabel(null)).toBeNull()
  })
})
