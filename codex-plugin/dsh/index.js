/**
 * Tavotto 的 DSH 胶水插件（ADR 0104）：只算路径，不实现任何工具。
 *
 * DSH 的 bundle 补丁里没有「本包目录」这个变量，而 MCP 客户端要一个绝对路径的
 * command。这里按 import.meta.url 定位包根，把启动参数作为普通 Cordis 服务
 * `tavotto` 提供出去；补丁里的 mcp-client 行 inject 它，再从 `ctx.tavotto` 取值
 * ——与 dsh-web-app 的 `webStartup` 同一个做法。
 *
 * 启动器与超时都不另写一份：启动器是包里那一对 `mcp/launch`（sh）/ `mcp/launch.cmd`
 * （批处理，#266），超时读 `.mcp.json` 的 `tool_timeout_sec`（插件里唯一的出处）。
 */

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/** Cordis 插件名（加载诊断里显示）。 */
export const name = 'tavotto'

/** 补丁行 inject 的服务名。 */
export const SERVICE = 'tavotto'

/** 包根：本文件在 `<包根>/dsh/index.js`。 */
export const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)))

/**
 * 由包根推出 MCP 启动参数与技能目录。
 * @param {string} root 包根的绝对路径
 * @param {string} platform `process.platform`
 * @returns {{command: string, args: string[], toolCallTimeoutMs: number, skillsDir: string}}
 */
export function launchSpec(root, platform = process.platform) {
  const server = join(root, 'mcp', 'server.py')
  const entry = JSON.parse(readFileSync(join(root, '.mcp.json'), 'utf8')).mcpServers.tavotto
  // POSIX 上交给 sh 解释，不靠 pnpm 保不保留执行位。Windows 上 command 直接是 launch.cmd：
  // dsh-mcp-client 经 MCP SDK 的 StdioClientTransport 用 cross-spawn 起进程，它看到非 .exe
  // 的文件会自己拼 `cmd.exe /d /s /c "<整条>"`、^ 转义全部元字符并逐字传给 CreateProcess。
  // 反过来把 cmd.exe 当 command 传参数，参数由 libuv 按 MSVCRT 规则加引号，路径一带空格
  // cmd 就切错（`\"` 它不认），怎么包载荷都修不了。cross-spawn 会先读首行找 shebang，所以
  // launch.cmd 首行不能是 `#!`（#720 起是 `@echo off`）。两边都与 Codex 起的是同一对启动器。
  const [command, args] = platform === 'win32'
    ? [join(root, 'mcp', 'launch.cmd'), [server]]
    : ['/bin/sh', [join(root, 'mcp', 'launch'), server]]
  return {
    command,
    args,
    toolCallTimeoutMs: entry.tool_timeout_sec * 1000,
    skillsDir: join(root, 'skills'),
  }
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 */
export function apply(ctx) {
  ctx.provide(SERVICE, launchSpec(packageRoot))
}
