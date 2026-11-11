# 部署与运维

## 标准启动

配置 `.env` 后，在项目根目录执行：

```powershell
pwsh -File ./start.ps1
```

```bash
bash ./start.sh
```

前置环境、配置字段、端口和常用参数见 [README](../README.md)。启动顺序为数据库健康检查、bootstrap、应用启动、HTTP就绪检查。启动不执行基准或 Judge；首次数据向量化和正常问答会产生模型调用。

## 容器与数据

默认 Compose 只启动 PostgreSQL；`app` profile 包含后端与前端，`tools` 包含 bootstrap 与完整复现。默认地址为网页8080、后端8000、数据库5432，宿主端口只绑定本机回环地址。

容器内数据库地址固定为 `postgres:5432`，宿主 Conda 方式通过 `.env` 中的 `APEX_DATABASE_URL` 连接。`configs/` 只读挂载，`data/` 和 `reports/` 可写挂载，数据库保存在命名卷中。Linux 宿主需确保容器 uid 10001 有权写入 data/reports，不要开放全局写权限。

```bash
docker compose --profile app ps
docker compose --profile app logs --tail 100 backend frontend
docker compose logs --tail 100 postgres
docker compose --profile app stop
```

停止保留卷和报告。已有匹配镜像与活动索引时，可用 `-NoBuild -SkipBootstrap` 或 `--no-build --skip-bootstrap` 恢复服务。新机器仍需标准构建与数据准备。

## 任务与重启

后端使用一个 Uvicorn worker，评测执行器全局并发上限1；未实现分布式租约前不要扩展多个执行实例。核心任务按每次模型请求预留最坏费用，使用持久化状态、幂等键、超时和取消边界。

服务重启不会自动重放付费任务。失去执行归属的任务标记为 interrupted；已发出请求可能仍产生费用，取消不能撤销供应商侧执行。线上验证优先使用只读就绪与报告接口；中断边界测试使用隔离数据库。

## 验证范围

已有 Windows / Docker 应用启动和核心六组复现记录。Linux 宿主完整启动与近期干净环境标准构建仍需目标环境验证。镜像源故障应排查网络或镜像服务；已有运行容器不等于新环境可复现。

当前默认无企业登录，正式处理客户数据前应实施 [生产路线图](roadmap.md) 中的授权、PII和审计措施。
