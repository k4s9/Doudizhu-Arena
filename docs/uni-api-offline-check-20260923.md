# Uni API 配置与离线验收（2026-09-23）

项目已保留 Uni API 接口，不需要重新实现远程 LLM 适配器。本轮未连接远端网关，没有真实模型调用。

## 现有入口

| 配置项 | 当前值 / 位置 |
| --- | --- |
| 配置文件 | `src/backend/arena/config/agents.yaml` |
| 网关地址 | `https://uni-api.cstcloud.cn/v1` |
| provider | `openai`，表示使用 OpenAI 兼容协议，并不表示调用 OpenAI 官方服务器 |
| 配置中的模型名 | `qwen3.5`、`minimax-m27`，各 5 组策略配置 |
| 密钥环境变量 | `CSTCLOUD_API_KEY` |
| 本地管理入口 | 前端 `/configs`，后端 `/api/v1/configs` |
| 实际请求 | SDK 将 `/chat/completions` 接在网关 `/v1` 之后 |

本次只读检查时，当前进程未设置 `CSTCLOUD_API_KEY`，项目根目录及 `src/backend` 均没有 `.env`，默认 `data/arena.db` 不存在。此前 mock 验收使用独立数据库，因此没有覆盖或删除正式凭据。这里只说明当前工作区状态，不能判断其他机器或其他进程是否仍保存旧凭据。

准备接入时，在本机环境中设置 `CSTCLOUD_API_KEY`，或使用根目录 `.env.example` 中新增的对应字段，然后重启后端。模型名是否仍可用、账户权限和计费口径需要以实际 Uni API 服务为准。若选择通过页面保存密钥，现有接口要求服务端配置 `DOUDIZHU_CREDENTIAL_MASTER_KEY`；该要求不是本轮新增。

启动、加载配置、预检均不等于发起模型推理。可靠性真实实验还需填写实际价格、来源、生效日期及预算，并在页面启动时确认。

## 本轮离线发现与修复

使用真实 OpenAI/Anthropic SDK，将 HTTP 传输替换为 `httpx.MockTransport`；仅使用固定测试密钥和 `.invalid` 域名。初次 18 项测试中 12 通过、6 失败，复现出以下问题：

1. 未解析的 `${CSTCLOUD_API_KEY}` 和纯空白密钥被当作有效凭据。现在配置列表、实验预检、执行前校验和普通比赛启动均正确识别缺失凭据；普通比赛不会在这种情况下排入后台。
2. Claude 工厂和普通比赛启动没有传递 `base_url`。现在与评测入口一样保留自定义网关。此处修复通用 Claude 兼容入口，不代表已经确认 Uni API 提供 Anthropic 协议。
3. 不完整 usage 会构造含 `None` 的 token 记录，或在 Anthropic 路径中把有效文本误判为调用失败。现在保留有效文本，将缺失/非法计量记为未知；明确的零 usage 仍为已知零，避免混淆成本分母。

新增 23 项 SDK/配置/API 契约测试，覆盖请求路径与鉴权头、模型名和消息传递、usage 缺失/不完整/负数/明确零、401/429/500、无 SDK 隐藏重试、错误后清空旧 usage、凭据状态与预检一致、普通比赛地址传递。相关 65 项回归已通过；完整离线回归结果及本轮浏览器结果见 [验收摘要](reviews/20260923-offline/validation-summary.json)。

完整后端测试分四组执行（335 项常规用例 + 3 个独立的耗时比赛/记忆用例）：**338 项全部通过，0 失败，0 跳过，0 次网络连接尝试**。浏览器在新构建上再次通过创建、预检、报告、失败记录、下载、回放、双观众事件一致和终局断线恢复；6 个 mock 任务全部完成，完整性审计通过。JUnit、修复前失败证据、截图、trace、mock 数据及源码指纹均单独归档，未覆盖旧证据。

## 不依赖远端 LLM 的测试入口

项目根目录执行：

```bash
conda run -n doudizhu-arena python -I -B scripts/test_offline.py
```

脚本禁止测试主进程的 socket 连接，并在结束时检查是否出现任何网络尝试；即使调用方捕获了异常，只要出现网络尝试，测试命令仍失败。HTTPX 的进程内模拟不受影响；CLI 回归的子进程固定使用 `--mock`。这不是操作系统级网络隔离，不应用它运行未知外部脚本。

单独检查网关协议：

```bash
conda run -n doudizhu-arena python -I -B scripts/test_offline.py tests/test_gateway_contract.py
```

浏览器 mock 验收需要本地 HTTP/WebSocket 连接，使用独立脚本；它禁用真实 provider：

```bash
conda run -n doudizhu-arena python -I -B scripts/verify_browser.py --output data/browser-offline-01
```

旧的 09-22 归档保留原样。本轮修改了后端源码，旧 manifest 的源码校验会拒绝继续运行，应该创建新的实验计划。
