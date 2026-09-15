# CI04 · 小规模独立 runner 池，而非直接复用可信 lab

依赖CI00；与其他阶段并行。只有在CI03证明任务隔离后扩生产并行。参见03_RUNNERS_AND_TRUST。

## 代码与运维交付

1. 管理员确认host/VM实际容量、网络可达与组织runner访问控制。记录原16/32资格VM的用途和基线，不默认拆毁它。没有真实授权只生成计划与dry-run检查。
2. 从额外资源或明确重新规划的资源创建一个执行槽试点。独立磁盘/账号/网络，镜像记录tool版本；runner注册在启动时完成，不把注册态或token烘焙进镜像。
3. 使用一次job一次VM/JIT/ephemeral组合，并由外部控制面销毁/恢复磁盘。执行VM无hypervisor控制凭据、宿主mount、Docker socket、真实科研数据或发布key。未具备清洁保证仍保持hosted默认。
4. PR无法通过改workflow要求可信lab资源：验证runner group或独立infra隔离在仓库外有效。仅repo label/作者名/allow-forks=false不构成充分保护。必要时PR池只接受有外部可信准入绑定的精确候选，剩余任务继续hosted；不使用特权事件checkout任意PR。
5. 先跑固定公开合成benchmark，与同recipe的hosted对拍正确性、启动、下载、测试、清理和网络。现网网络比hosted差时先解决供应可用性或只让本地跑适合任务，不强制迁移全部job。
6. 开第二个槽，验证并发和异常隔离，再决定第三个。明确标签/可承载任务、矩阵max-parallel、保留容量；跨PR不保证自动公平，需要通过资源组/有限dispatch控制而不是想象。
7. 保持lab资格基准独占条件。lab_preflight/cleanup共享根和flock不沿用到新PR池；同host性能测试需阻止并行噪声或重建可比较基线。
8. 按官方规则维护runner升级与镜像轮换。试验runner故意离线、停止接单、job取消、token过期，确认任务不会悄悄丢失，也不会因为新route没有实体而永久排队。
9. 生产route在部署验证后才启用。退出本地route回到同等hosted任务并重新取得资格；不在失败后把旧结果拷成成功，也不声称runs-on自己会fallback。

## 验收

`runner_pool_ready`需要真实正反测试：隔离、一次性身份/磁盘、并发有效、版本与网络、可信资源不可达、故障回退。未实际部署只交付admin手册与not_run。此状态不阻止已有hosted上的CI01–03或U00继续。
