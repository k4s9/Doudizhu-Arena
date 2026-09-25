# 部署路径统一与验收（2026-09-25）

此前可靠性实现与真实模型证据已固定在本地提交 `27a56f8`。本文对应其后的部署修复及验收记录，推送由用户手动完成。归档中的版本状态记录的是验收当时的工作区状态。

## 修复内容

- 后端容器采用 `/app/src/backend` 仓库布局，统一由 `arena/config/paths.py` 定位项目根、配置和实验资源；修复旧 `/app/arena` 布局中评测路由的父目录越界。
- 数据库、日志、代理配置及评测 CLI 的数据库和输出相对路径均以项目根为基准。SQLite 绝对 URL 使用 `sqlite:////app/data/arena.db`。
- 后端镜像包含实验模板、seed、提示词和依赖指纹所需文件，并使用命名 conda 环境；明确安装 `python-dotenv`。
- Compose 挂载数据库/报告、日志和代理配置，显式传入 Uni API、OpenAI、Anthropic 凭据与加密主密钥，并等待后端健康检查后启动 Nginx。
- 两个镜像均从仓库根目录构建，构建上下文排除 `.env`、本地数据、日志和缓存。`.env.example` 使用空凭据，并列出宿主端口及持久目录配置。
- 新增 `scripts/verify_deployment.py` 和独立的 Compose CI job，覆盖页面创建 mock 实验、报告下载、回放、WebSocket 及后端重启。

路径及配置优先级见 [README](../README.md#路径与持久化约定)。容器中的自定义评测输出应写到 `/app/data` 下；调用记忆训练 CLI 时可显式指定 `--output data/memory-artifacts/<name>.json`，以便保存在挂载目录。

## 已完成的验证

| 验证 | 结果 |
|---|---|
| 部署路径、Uni API 预检、阶段 4、可靠性与记忆训练回归 | **39 项通过，0 失败/跳过；测试主进程外部连接尝试 0** |
| 迁移目录回归 | 与 Docker 相同的后端目录树；进程环境优先级、配置加载、凭据加密/解密、API 实验、CLI 输出与新解释器重启通过 |
| Compose 解析、启动脚本语法、CI YAML、Python 语法 | 通过 |
| 本地迁移目录下的实际进程与生产前端构建 | 通过；前端使用 Vite preview 转发到迁移后的 Uvicorn |
| 页面创建与启动 mock、报告及回放、WebSocket | 6 个任务全部完成，审计通过，6 种报告文件可下载，页面异常 0 |
| 终止后端后从不同工作目录重新启动 | 原 run、manifest、任务计数、终态得分与事件水位均保持一致；报告和回放仍可读取 |

浏览器验收 run 为 `d6496971b1784955a5aeaa5a8767fdb7`。所有模型均为 mock，临时服务中的真实凭据为空。它验证部署行为，不构成模型效果或性能测量。

本机 Chromium 曾在页面创建前报 `Target crashed`；诊断重试定位到 `V8 process OOM (Failed to reserve virtual memory for CodeRange)`。首次创建及完整流程已在默认 Chromium 下成功，最终重启检查使用 `--chromium-arg=--js-flags=--jitless` 通过。CI 保持默认 Chromium 设置。原始失败目录仍保留在 `data/deployment-20260925/local-deployment/` 和 `local-deployment-02/after-restart/`，没有覆盖为成功结果。

可提交的证据位于 [reviews/20260925-deployment](reviews/20260925-deployment/)：

- `backend.xml`：本轮 39 项回归结果。
- `before-restart.json` / `after-restart.json`：成功的浏览器验收及重启对比。
- `manifest.json` / `integrity.json`：此次 mock 的冻结输入与审计结果。
- `validation-summary.json`：验证范围、源码指纹与容器验收状态。
- `SHA256SUMS.json`：归档文件摘要。

本机详细截图、trace、报告和隔离数据库保存在 `data/deployment-20260925/local-deployment-02/`。

## 容器实测状态与运行方式

**本机未完成 Docker 镜像构建或 Nginx 实测。** 上轮基础镜像拉取未获授权，本轮沿用该边界；本地 Vite preview 验收不代替容器验收。新的 CI job 尚未在远端执行，推送含本次部署修复的提交后才能得到结果。

已有 Docker 服务和 conda 开发环境时，可在项目根运行以下验收；输出目录必须尚不存在：

```bash
docker compose up --build -d --wait
conda run --no-capture-output -n doudizhu-arena python -I -B scripts/verify_deployment.py \
  --base-url http://127.0.0.1 --output data/deployment-before-restart

docker compose restart backend
conda run --no-capture-output -n doudizhu-arena python -I -B scripts/verify_deployment.py \
  --base-url http://127.0.0.1 \
  --resume-from data/deployment-before-restart/result.json \
  --output data/deployment-after-restart
```

脚本需要 Playwright Chromium。CI 会安装它；在本机首次准备时，可使用 `conda run -n doudizhu-arena python -I -m playwright install chromium --only-shell`。上述命令会在目标服务中创建一个 mock 实验，重启检查复用同一实验，不重复创建。

CI 使用独立的 `doudizhu-ci` Compose 项目、18080/18000 端口、`data/ci-docker` 数据目录以及空凭据。首次验收和重启检查均走 Nginx 地址；测试失败也会上传日志及已产生的证据，然后停止测试服务。
