# CI02 · 精确产物复用和工具准备去重

依赖CI00，与CI01并行对齐producer输出。只有测量显示收益的重复构建才抽离，避免传包比构建更慢。

## 实际实施

1. 列出web应用、MCPwidget、完整插件候选、Playground、wheel、workerd、sidecar各自recipe与平台依赖；识别真正相同的构建，不把同仓库不同target混为一份。
2. 提取最小producer，其职责是构建并校验资源，不先跑长pytest/pnpm全部测试。保持真正的TypeScript项目引用检查：使用现有build中的`tsc -b`或等价已验证命令，不换成空noEmit。
3. 一次构建按精确checkout SHA、run/attempt、recipe/target上传归档及manifest。继承plugin_stage的dotfiles/权限/内容校验，消费前再验；所有消费者使用确切artifact ID/名称范围，不使用latest。
4. 确认不同平台/Node路径嵌入因素。只有平台中性的资源才跨平台复用；必要时由各平台重建并对拍，既有签名/架构资格不因此取消。
5. 开发/PR artifact不直接提升为发行权限输入；最终发行仍按现有可信来源、候选字节、签名策略。sdist应保留用户从源构建能力，不让下载了webartifact才勉强打包成为唯一成功路径。
6. cache键结合OS/arch/toolchain/依赖锁/字体或浏览器身份/信任域。固定锁安装仍执行并校验；global site-packages、活动venv、用户配置和成功测试结果不可作为通用cache。
7. 更新PyInstaller/构建脚本的“已构建输入”消费路径时不得更改产品依赖或移除资源。脱离源码树进行候选资源验证，故意删文件/改hash必须失败。
8. 量化复用前后的构建时间、传输时间、cache命中/失效与总资源分钟。若特定producer抽离负收益，保留同job重用或撤销该抽取，不把更多job当成功标准。

## 验收

实际至少一个consumer复用正确产物；错SHA/错recipe/少文件/dotfile丢失等反例失败。纯web和native目标分工清楚；TypeScript真错误仍能阻止资格。生成物不进源码索引，插件稳定通道未改。
