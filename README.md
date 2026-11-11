# Apex 信用卡客服 RAG

基于 PostgreSQL / pgvector 的信用卡政策检索与问答系统，使用向量检索、全文检索、RRF 融合和 Qwen 重排序。支持原文引用、多轮追问、六组检索对比，以及 Iceberg、GraphRAG 和 LLM Judge 扩展。数据集包含150条政策和30道标准评测题。

当前提交保留代码、数据和清理后新生成的实验结果。历史六组对比及扩展报告已从本地项目清理，现有报告见 [报告目录](reports/README.md)；下文列出的 `reports/results.md`、对比图及扩展报告路径是运行相应命令后生成的产物。测试夹具与帮助截图是演示数据，不作为真实实验成绩。

## 快速启动（推荐：Docker 一键运行）

从项目根目录（包含 `docker-compose.yml` 的目录）执行。首次完成 `.env` 配置后，一条启动命令即可准备数据、建立索引并启动前后端。

### 1. 准备环境

- 安装并启动 Docker Desktop（使用 Linux 容器），或 Docker Engine；需要支持 `--wait` 的 Docker Compose v2。
- Windows 使用 PowerShell 5.1 或 PowerShell 7；Linux/macOS 使用 Bash，并安装 `curl`。
- 首次构建需要访问容器镜像源、Python/npm 依赖源；模型调用需要可用的阿里云百炼 / DashScope API Key。
- 纯 Docker 启动不需要在宿主机安装 Conda、Python 或 Node.js。需要修改代码时再使用下方的 Conda 开发方式。

### 2. 创建并填写 `.env`（首次必做）

Windows PowerShell：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Linux/macOS Bash：

```bash
[ -f .env ] || cp .env.example .env
# 使用文本编辑器打开 .env，填写下面的配置。
```

至少检查以下字段；示例占位值需替换为自己的配置：

```dotenv
POSTGRES_USER=apex
POSTGRES_PASSWORD=replace-with-your-password
POSTGRES_DB=apex
POSTGRES_PORT=5432
BACKEND_PORT=8000
FRONTEND_PORT=8080
APEX_DATABASE_URL=postgresql://apex:replace-with-your-password@localhost:5432/apex
DASHSCOPE_API_KEY=replace-with-your-dashscope-key
APEX_MODEL_REGION=beijing
```

`APEX_DATABASE_URL` 用于宿主机 Conda 开发，用户名、密码、数据库名和端口应与上方保持一致；Compose 会自动把容器内的数据库地址改为 `postgres:5432`。DSN 中的密码若包含 URI 保留字符，需进行 URL 编码。

默认使用北京地域的 `text-embedding-v4`（1024维）、`qwen3-rerank` 和 `qwen3.5-plus`，请确认账号具有相应模型权限；需要 Workspace 路由时再填写 `APEX_WORKSPACE_ID`。`.env` 仅保存在本地，不提交到仓库。

### 3. 一键启动

Windows PowerShell 7：

```powershell
pwsh -File ./start.ps1
```

Windows 自带 PowerShell 5.1（仅本次进程绕过脚本执行限制）：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Linux/macOS Bash：

```bash
bash ./start.sh
```

启动脚本依次启动 PostgreSQL 并等待健康、执行 bootstrap（迁移、导入150条政策、生成或复用向量、构建索引）、构建并启动后端与前端，最后检查后端就绪状态和网页。终端输出 `Ready:` 与 `Swagger:` 地址即表示检查成功；任一步失败返回非零退出码。

首次 bootstrap 会调用 Embedding API，可能产生费用；重复执行会复用匹配的已有构建或向量缓存。普通启动不执行30题基准、六组对比或 LLM Judge，实际搜索和问答会调用相应模型。

### 4. 打开网页并检查服务

| 入口 | 默认地址 | 用途 |
| --- | --- | --- |
| 应用首页 | [localhost:8080](http://localhost:8080) | 对话工作台、引用原文与评测页面 |
| Swagger | [localhost:8000/docs](http://localhost:8000/docs) | 查看及调试 `/api/v1/search` 等接口 |
| OpenAPI | [localhost:8000/openapi.json](http://localhost:8000/openapi.json) | 下载接口定义 |
| 后端就绪检查 | [localhost:8000/health/ready](http://localhost:8000/health/ready) | 正常返回 `status: ready` |

若修改 `.env` 中的端口，请按实际端口访问；脚本会读取数字形式的 `BACKEND_PORT` / `FRONTEND_PORT` 并输出地址。若默认端口已被占用，可分别改用 `55434`、`18001`、`18080`，访问地址以自己的 `.env` 为准。

### 后续启动与常用参数

| 场景 | Windows PowerShell 7 | Linux/macOS Bash |
| --- | --- | --- |
| 常规启动，构建代码并检查数据 | `pwsh -File ./start.ps1` | `bash ./start.sh` |
| 已有本项目镜像，不重新构建 | `pwsh -File ./start.ps1 -NoBuild` | `bash ./start.sh --no-build` |
| 已有完整活动索引，跳过数据准备 | `pwsh -File ./start.ps1 -SkipBootstrap` | `bash ./start.sh --skip-bootstrap` |
| 镜像和活动索引都已准备好，快速恢复服务 | `pwsh -File ./start.ps1 -NoBuild -SkipBootstrap` | `bash ./start.sh --no-build --skip-bootstrap` |
| 将服务就绪等待调整为300秒 | `pwsh -File ./start.ps1 -TimeoutSeconds 300` | `bash ./start.sh --timeout 300` |

首次部署使用常规启动。`NoBuild` 适用于已有匹配镜像的环境，本身不准备离线镜像或依赖；`SkipBootstrap` 要求数据库中已经存在完整可用的活动构建，空数据库不能跳过此步。代码修改后应重新构建。

需要覆盖就绪检查地址时，可使用 PowerShell 的 `-BackendUrl` / `-FrontendUrl`，或 Bash 的 `--backend-url` / `--frontend-url`。这些参数只改变检查地址，实际端口映射仍由 `.env` 控制。

### 查看状态、日志与停止服务

```bash
docker compose --profile app ps
docker compose --profile app logs --tail 100 backend frontend
docker compose logs --tail 100 postgres
docker compose --profile app stop
```

`stop` 保留数据库卷、数据和报告；下次使用启动脚本恢复服务。不要用删除数据卷来代替正常停止。

## Conda 手动开发（需要本地修改代码时）

在仓库根目录执行；需要已安装 Conda、Docker Compose v2、Node.js 22。不会自动安装这些工具。

```powershell
conda env create -f environment.yml
conda activate apex-rag
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 编辑 .env，设置密码、DASHSCOPE_API_KEY；核对地域/Workspace/模型权限。
docker compose up -d --wait --wait-timeout 180
python -m apex_rag.cli probe-models
python -m apex_rag.cli bootstrap --config configs/bootstrap.yaml
python -m uvicorn apex_rag.main:app --host 127.0.0.1 --port 8000 --reload
```

另开终端：

```powershell
cd frontend
npm ci
npm run dev
```

Linux 创建配置时使用 `[ -f .env ] || cp .env.example .env`，后续 Python/npm 命令相同。默认开发页 [localhost:5173](http://localhost:5173)，[Swagger](http://localhost:8000/docs)。手动后端 DSN 主机为 localhost，容器主机由 Compose 覆盖为 postgres。若修改密码含 URI 保留字符，需要 URL 编码本地 DSN；建议演示环境使用不含 URI 保留字符的强密码。

若手动后端改用其他端口，还需在 `frontend/.env.local` 中配置 `APEX_PROXY_TARGET=http://127.0.0.1:实际后端端口` 并重启 Vite；根目录的 `BACKEND_PORT` 只控制 Compose 映射，不会改变手动 uvicorn 的监听端口。

## 手动分步启动容器

需要逐阶段启动或定位问题时，可按以下顺序执行；`docker compose up -d` 默认只启动数据库，完整网页需要 bootstrap 和 `app` profile：

```bash
docker compose up -d                                     # 只有 postgres
docker compose --profile tools run --build --rm bootstrap # 核心迁移/数据/向量/索引/切换
docker compose --profile app up -d --build                # 完整应用（需先 bootstrap）
docker compose --profile app stop                        # 保留卷
```

完整网页 [localhost:8080](http://localhost:8080)，后端 [localhost:8000/docs](http://localhost:8000/docs)。手动模式与容器模式的后端默认竞争 8000，择一运行。修改映射端口后启动脚本会读取 `.env` 的数字端口，显式 URL 参数仍可覆盖。

## 一键执行核心评测与性能报告

完成 `.env` 配置后，在项目根目录执行下方命令。评测入口与应用启动入口分开：启动用于打开网页，复现用于完成“数据导入 → 索引构建 → 评测 → 报告”的交付流程。

仅安装 Docker 的环境（推荐）：

```bash
docker compose --profile tools run --build --rm reproduce
```

已安装并激活上文 Conda 环境的开发机：

```bash
python scripts/reproduce.py --config configs/reproduce.yaml
```

核心复现会准备两种分块构建，执行固定窗口 / 政策层级 × Dense / Hybrid / Hybrid+Rerank 六组实验，每组30题，共180次问题检索，并按配置执行索引参数实验。它会调用真实 Embedding / Rerank API，可能产生费用；默认 `configs/reproduce.yaml` 不执行 Iceberg、Graph 或 Judge 扩展，也不启动前端工作台。

| 产物 | 路径 |
| --- | --- |
| 六组对比表、图与分析 | `reports/results.md`、`reports/core-comparison.png` |
| 核心复现汇总 | `reports/core-reproduction.json` |
| 每组配置、指标、逐题排名和模型调用记录 | `reports/runs/<run_id>/` |
| 包装命令执行状态与退出记录 | `reports/bonus-runs/reproduction-command.json` |
| 索引参数对比及原始计时 | `reports/index-benchmark/` |

脚本调用同一后端 reproduce CLI；超时或任一步失败返回非零退出码。已有报告可以直接阅读，无需为了查看结果重复运行。网页中的核心评测同样需要显式估算并确认启动，不由服务启动自动触发。

## 按需执行拓展实验

以下命令在已激活的 Conda 环境、项目根目录中执行，需要完成核心 bootstrap。扩展已核对北京地域官方费率并保存来源，按配置任务量和总预算执行；更换地域/模型或超过输入上限会拒绝沿用该费率。

```bash
python -m apex_rag.cli bonus-iceberg --config configs/bonus-iceberg.yaml
python -m apex_rag.cli bonus-graph-audit --config configs/bonus-graph.yaml
python -m apex_rag.cli bonus-graph --config configs/bonus-graph.yaml
python -m apex_rag.cli bonus-judge --config configs/bonus-judge.yaml
# 复用已保存的60答案；默认仅检查新方法预算计划，不发API请求：
python -m apex_rag.cli bonus-judge-rejudge --config configs/bonus-judge-v2.2.yaml
```

运行后分别生成 `reports/iceberg.md`、`reports/graphrag.md`、`reports/judge.md`，已有产物见[报告目录](reports/README.md)。数据、配置、原始样本和报告由挂载持久化。API 只读查询扩展能力与结果，不通过网页自动运行付费实验。详见 [部署说明](docs/deployment.md)和[扩展说明](docs/bonus.md)。

```bash
python -m pytest backend/tests/extensions -q
```

测试包括真实本地 Iceberg 表/manifest/时间回溯（合成夹具）、150 条源文档图边审计、Judge 结构/预算/失败语义、部署 profile 与子进程契约。扩展测试与官方六组检索评测采用独立口径。

## 启动常见问题

| 现象 | 处理方式 |
| --- | --- |
| 提示 Docker 不可用 | 启动 Docker Desktop 并切换到 Linux 容器；用 `docker info` 和 `docker compose version` 检查引擎及 Compose |
| 提示缺少 `.env` 或数据库密码 | 按快速启动第2步创建并填写配置，再运行启动脚本 |
| Embedding / Rerank / LLM 返回鉴权或模型权限错误 | 检查 `DASHSCOPE_API_KEY`、地域和 Workspace；Conda 环境可运行 `python -m apex_rag.cli probe-models` 诊断，该诊断会调用真实模型 |
| 5432、8000或8080端口被占用或系统拒绝绑定 | 在 `.env` 修改对应 `POSTGRES_PORT`、`BACKEND_PORT`、`FRONTEND_PORT`；手动开发时同步更新 `APEX_DATABASE_URL`，并调整实际 uvicorn 监听端口 |
| 镜像拉取、构建或依赖下载失败 | 先排查镜像源和网络；已有匹配镜像时才使用 `-NoBuild` / `--no-build`，新机器仍需先准备镜像 |
| 后端未就绪或提示没有活动索引 | 查看后端和数据库日志；取消跳过 bootstrap，重新执行常规启动 |
| 浏览器不能访问默认地址 | 以启动脚本输出的 `Ready:` / `Swagger:` 和 `.env` 实际端口为准，并检查 `docker compose --profile app ps` |

## 项目结构

```text
backend/src/apex_rag/   数据导入、存储、检索、问答、评测与扩展
backend/tests/          单元测试与数据库集成测试
frontend/src/          React 工作台与评测界面
frontend/tests/        页面与交互测试
configs/               数据准备、检索及扩展实验配置
data/                  官方数据、图谱挑战题与索引配置
docker/                容器构建与 Nginx 配置
scripts/               启动辅助、复现与验证工具
reports/               评测报告、逐题结果和运行记录
docs/                  架构、部署、扩展与生产路线图
```

## 开发验证

在项目根目录、已激活的 Conda 环境中执行：

```bash
python -m pytest backend/tests -q
python -m ruff check --config backend/pyproject.toml backend/src
python -m mypy --config-file backend/pyproject.toml backend/src
```

数据库集成测试需要指向隔离测试数据库的 `APEX_TEST_DATABASE_URL`；未配置时相应测试会跳过。Mypy 与 Ruff 的检查范围以 `backend/pyproject.toml` 为准。

前端验证：

```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run build
```

## 结果与限制

- [现有实验报告](reports/README.md)：当前保留政策层级 / Dense 的30题单组结果，尚不能作为两种分块、三种模式的完整对比。执行完整复现命令后会生成 `reports/results.md`。数据库与包含在线模型调用的完整检索延迟分开报告；当前完整检索尚未达到100ms。
- [扩展实现与运行说明](docs/bonus.md)：Iceberg 为精确扫描 PoC；Graph 与 Judge 需独立运行后读取报告。历史报告已清理，帮助截图、测试夹具和文档中的历史方法说明不代表本次提交已附上相应真实结果；自动评审也不能替代人工核验。
- 启动脚本支持 PowerShell 与 Bash。已有 Windows / Docker 运行记录；Linux 宿主完整启动及近期从零标准镜像重建仍需在目标环境验证。
- 当前为开发演示应用，尚未提供企业登录与文档级访问控制；生产扩展、安全和数据治理方案见[路线图](docs/roadmap.md)。

更多信息：[架构](docs/architecture.md)、[部署](docs/deployment.md)、[扩展](docs/bonus.md)、[帮助指南维护](docs/help-maintenance.md)。
