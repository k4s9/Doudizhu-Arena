# 新会话交接：Doudizhu-Arena 测试与 Uni API 接入

> **09-24 最新状态**：[真实模型 smoke 已完成并复核](uni-api-real-test-20260924.md)。两模型各三协议，共 567 次正式调用、12 个终态桌局，原始报告与只读重建报告一致。已有用户真实调用授权；无需按下文旧状态重新探测或重复询问。日备份为 `/home/guozy/.codex/backups/doudizhu-arena/daily-2026-09-24/project`（201 MB）。后续重点是固定提交/独立复现，或另行设计多 seed 对照；下文为 09-23 历史交接。

> 09-23 后续更新：根 `.env` 两项凭据已通过本地检查；新增禁止联网的预检脚本，修正真实模板超时与空预算，配置页显示凭据状态。34 项后端针对性回归、7 项前端测试和构建通过，未调用真实模型。最新状态、证据和待确认预算/计价见 [后续预检记录](uni-api-preflight-20260923.md)。下文保留交接时的历史状态，338 项为此前全量验收。

## 用户目标与当前边界

用户希望继续完善、测试项目，并评估其是否足以作为简历项目。第二轮工程实施、离线回归和演示已完成；用户正在自行准备 Uni API 的 LLM 接入，要求其余内容先测试。最近询问了 `.env` 的具体内容，并要求准备交接 prompt。

继续在现有工作区推进，不要从头重新实现或重复已完成的测试来代替进展。读取本文后先简要确认状态，再检查当前配置；用户可能已在两次会话之间填写 `.env`。仅检查凭据是否存在/能否解析，不输出密钥或整个 `.env`，不要让用户在聊天中粘贴密钥。

本轮没有发起任何真实模型调用。添加密钥不应自动触发整个评测矩阵；真实测试需要结合新会话用户授权、明确模型和调用/费用边界。已有明确授权时不要重复询问。缺少真实实验信息时，先完成可审阅的规格和其他独立检查。

## 工作区与安全规则

- 项目：`/home/guozy/Doudizhu-Arena`。
- 必须先读根 `AGENTS.md`。禁止递归强制删除及批量删除；不要删除文件、重置改动或替用户清理工作区。
- 工作区有大量上一轮及本轮未提交修改，HEAD 为 `3bb8d2bbd948f0e5291d3500af1b1ee9ebf34a88`。它不能单独代表最新受测版本。历史跟踪的 `.pyc`/egg-info 改动也不要擅自还原或删除。
- 09-23 日备份已完成：`/home/guozy/.codex/backups/doudizhu-arena/daily-2026-09-23/project`，189 MB。旧规则中的 `/home/k4s9` 在本机不存在，因此按实际用户目录备份。同一天不必重复备份；新的一天按规则处理。旧临时备份仍保留。
- 没有提交、推送或执行远端 GitHub Actions；没有创建 PR。

## 运行环境

- 持久 conda 环境已安装：`/home/guozy/miniconda3/envs/doudizhu-arena`。
- Python 3.12.13、Node 22.23.2、pytest、httpx、Playwright 与 Chromium headless shell、前端依赖均已安装；`python-dotenv` 1.2.2 可用。
- 所有项目 Python 必须通过该 conda 环境执行。使用 `-I -B`，避免宿主 Ascend 的 Python 路径污染和新增字节码。
- 不需要重新创建环境或重新安装整套依赖。
- 本机启动，在项目根目录执行：

```bash
DOUDIZHU_ENV_PREFIX=/home/guozy/miniconda3/envs/doudizhu-arena bash start.sh
```

## Uni API 与 .env

现有 `src/backend/arena/config/agents.yaml` 已配置：

- `provider: openai`，通过 OpenAI 兼容协议调用远程网关。
- `base_url: https://uni-api.cstcloud.cn/v1`。
- 模型名 `qwen3.5` 与 `minimax-m27`，各有 5 组策略配置。这里只确认配置中的名称，未确认远端当前可用性。
- `api_key: ${CSTCLOUD_API_KEY}`。
- 前端 `/configs` 和后端 `/api/v1/configs` 提供配置管理。

交接准备时，两处 `.env` 均不存在，当前进程无 `CSTCLOUD_API_KEY`，默认 `data/arena.db` 不存在。之前演示使用独立 mock 数据库。新会话需要重新检查这些状态，不能假定用户没有更新。

已建议用户只在项目根目录 `/home/guozy/Doudizhu-Arena/.env` 创建一份：

```dotenv
CSTCLOUD_API_KEY=<用户的 Uni API 密钥>
DOUDIZHU_CREDENTIAL_MASTER_KEY=<本机生成并长期保存的随机主密钥>
LOG_LEVEL=INFO
```

`CSTCLOUD_API_KEY` 用于网关鉴权。`DOUDIZHU_CREDENTIAL_MASTER_KEY` 用于本地 SQLite 凭据加密，页面保存 API 密钥时要求配置；可用 `openssl rand -hex 32` 生成。加密数据写入后应保留该主密钥。不要替用户写入固定示例值作为真实主密钥。Uni API 的地址和模型已经在 YAML 中，无需另填 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY`。

后端启动时自动加载 `.env`。现有进程环境优先，然后是 `src/backend/.env`，最后根 `.env`（dotenv 默认不覆盖已有变量）；推荐只保留一个配置位置以免混淆。填写后重启后端。`.env` 已在 `.gitignore` 中。

## 已完成的实现

1. 决策、attempt、校验、模型提案与提交动作关联；领出 pass 拒绝；模型成功、兜底、托管、取消分开计量。
2. 原始输出重新解析的语义审计；桌/手/比赛计分重算；调用与预算账本一一对应。
3. manifest 自身、源码、依赖规格和 SDK 指纹复验；冻结提示词、参数、端点和种子。
4. 独立取消截止时间与迟到写入隔离；未知 usage 保留预算占额。失败 CLI 返回非零。
5. 多观众事件广播、持久化序号、缺口重取快照、终局重连和刷新；回放按动作序号重建。
6. 实验创建/详情页采用共享三组协议：单次生成、通用重试、规则反馈，最多生成 1/3/3 次，关闭反思与记忆。报告、失败记录及下载入口已接入。
7. mock 的 `initial_error` / `valid_first` / `play_error` 场景，正常首答场景断言无兜底；出牌错误场景断言单次兜底和重试恢复。
8. 持久环境、可移植 `start.sh`、浏览器验收脚本和 CI。
9. 本轮 Uni API 离线检查新增的修复：未解析环境占位符/空白不再被判为可用密钥；Claude 工厂与普通比赛入口保留自定义 `base_url`；缺失/不完整/非法 usage 保持未知且不丢失有效文本，明确零 usage 仍计为已知零。

主要新增入口：

- `scripts/test_offline.py`：禁止 pytest 主进程的 socket 连接，出现连接尝试即使被业务代码捕获也让命令失败。CLI 子进程测试固定使用 `--mock`，这是测试工具而非系统级沙箱。
- `scripts/verify_browser.py`：构建前端并用 Vite preview，启动隔离数据库/空配置的本地后端，禁用真实 provider，完成双观众、断网期间结束、终局重连/刷新、报告和回放。输出目录必须不存在。
- `src/backend/tests/test_gateway_contract.py`：真实 OpenAI/Anthropic SDK + HTTPX MockTransport，用假密钥和 `.invalid` 域名验证请求和配置契约，禁止真实网络。
- `src/backend/arena/security/credentials.py:has_usable_credential`、`src/backend/arena/llm/base.py:LLMUsage.from_counts`。

## 已有验收证据，不要误读旧结果

最新完整后端：**338 项全部通过，0 失败、0 跳过**。分成 335 项常规测试和 3 个单独的慢测试并行运行，JUnit 测试身份无重复，合计 338。四组均为 **0 次网络连接尝试**。

新增网关契约共 23 项；相关 65 项回归也已通过。修复前的首次契约探针为 12 通过、6 失败，单独留存为前后对照，不能当作当前失败。

前端上一轮 6 项单元测试通过；本轮浏览器脚本重新执行生产构建和完整 mock 流程，通过，无页面异常。6 个任务、12 个正常桌局、732 次合成调用，审计完整。两个观众有 6 条共同实时事件逐条一致，终局重连和刷新通过。合成 provider 人为延迟 60 ms，不能用于模型延迟结论。

最新关键文件（全部位于项目下）：

- `docs/uni-api-offline-check-20260923.md`：当前配置与本轮修复说明。
- `docs/reviews/20260923-offline/validation-summary.json`：最新验收摘要。
- `docs/reviews/20260923-offline/{main,full-match,reflection,summary}.xml`：完整 338 项 JUnit。
- 同目录 `gateway-before.xml`、`affected-tests.xml`、`code-fingerprints.json`、`SHA256SUMS.json`。
- 同目录 `browser/`：结果、截图、trace、压缩 mock DB；`mock-report/`：manifest、报告、指标和完整失败案例。
- 当前受测后端源码 SHA-256：`c9bb8b902a1d076cee263a98929d9ab886ee3714dd9bd713c0dcc49f723730f9`。
- 当前 mock run：`6bb8ab7304684d918568901ef696845d`。
- `docs/reliability-delivery.md`：第二轮交付和简历差距，已链接本轮后续检查。
- `docs/reliability-assessment-20260922.md` 与 `docs/reviews/20260922/` 是修复前材料；`docs/reviews/20260922-round2/` 是上一版修复证据，均应保留。

## 推荐下一步

1. 根据用户新消息，先检查 `.env`/进程是否已提供 Uni API 凭据及加密主密钥，仅输出布尔状态；检查配置能加载、页面正确显示，先不启动真实比赛。
2. 如用户准备进行真实测试，复核当前网关、实际模型、采样参数、usage/计费口径、预算和调用上限，准备具体可审阅的小规格。当前真实模板仍继承 mock 的短超时（叫分/出牌 2 秒、任务 60 秒），需要根据真实推理延迟审阅调整后再运行，避免把配置超时当模型失败。
3. 原提议 smoke：1 模型 × 1 seed × 3 变体 × 每场 1 手，3 个任务/6 个桌手；1/3/3 次生成、每次最多 512 输出 token、总调用上限建议 2000。这只是计划，实际预算、价格及真实调用授权尚未给出；不要把上限当作应消耗的调用数。也可以先准备更小的单次连通性检查，再执行完整 smoke。
4. 源码修改后必须创建新 manifest。旧 run 可查看和审计，但不能改哈希绕过运行前检查。
5. 只有新改动、失败或未解决问题需要复验时再运行相应测试，避免反复全量测试已验证代码。完整离线命令在项目根目录：

```bash
conda run -n doudizhu-arena python -I -B scripts/test_offline.py
```

浏览器命令（使用新的输出目录）：

```bash
conda run -n doudizhu-arena python -I -B scripts/verify_browser.py --output data/browser-next-session-01
```

## 仍未验证的简历与工程边界

当前适合描述为有回归证据和演示的 LLM 可靠性工程项目。尚缺真实模型 smoke、真实失败恢复案例、足量独立 seed 的公平对照和统计区间、固定提交后的干净检出/远端 CI、个人贡献梳理。不能把 mock 收益写成真实模型提升百分比。

取消期限保证运行终态与写入隔离，不能强制杀死任意不响应取消的 Python 协程，供应商端计费可能继续；未知 usage 保留预算占额。事件广播和 SQLite 为单进程实现，没有多副本/生产 SLA 证据。固定观察 CLI 目前只开放 mock，真实版本仍需接入统一预算、usage 和 manifest 管线。不要擅自扩展成大规模实验、多人 Agent 框架或多副本部署。
