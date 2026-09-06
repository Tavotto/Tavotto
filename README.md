# Tavotto Codex 插件 · 发行分支 `plugin-stable`

**机器维护，不要手工提交，不合回 main。** 这条分支上的每个提交是一份经过
发布链验证的完整插件（含内嵌画布），由 `scripts/plugin_publish.py` 从固定的
源码 commit 构建并投影到这里。源码在 `main`。

当前：插件 0.13.0，源码 ``，内容摘要 `035b5b6b82e3…`（详见 `plugin-release.json`）。

安装：

```sh
codex plugin marketplace add Tavotto/Tavotto --sparse .agents/plugins
codex plugin add tavotto@tavotto
```

旧客户端（不支持 `git-subdir` 来源）的后路：

```sh
codex plugin marketplace add Tavotto/Tavotto --ref plugin-stable --sparse .agents/plugins --sparse codex-plugin
```
