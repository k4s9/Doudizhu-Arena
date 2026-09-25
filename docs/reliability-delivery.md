# 可靠性第二轮交付与简历评估（2026-09-22 验收，09-23 归档）

> 后续 Uni API 配置核对、网关契约修复与完整离线测试见 [09-23 接入检查](uni-api-offline-check-20260923.md)。[09-24 真实模型测试](uni-api-real-test-20260924.md) 已完成：两模型、三协议、567 次正式调用、12 个终态桌局通过审计，含真实规则反馈恢复案例。下文工程实现与回归数字保留第二轮受测版本；简历缺口已更新。

本轮接续 [修复前评估](reliability-assessment-20260922.md)，完成该文档建议的工程实施。旧评估及 `reviews/20260922/` 保留为修复前证据，本轮结果单独归档到 [验证摘要](reviews/20260922-round2/validation-summary.json)。当前改动尚未提交；HEAD `3bb8d2bbd948f0e5291d3500af1b1ee9ebf34a88` 不能单独代表受测工作区。

## 本轮实现

| 问题 | 交付行为 |
| --- | --- |
| 兜底可被误标为模型成功 | 根据冻结观察重新解析原始输出，核对 attempt、校验、模型提案及提交动作；误改标签不再提高模型成功数 |
| 得分与成本漏检 | 重放动作并重算桌、手、比赛分数；核对调用与预算账本一一对应、冻结价格与实际金额 |
| 取消期限未落实 | 独立计算取消期限；到期隔离执行器，禁止迟到结果继续写库或提交动作；未知 usage 保留预算占额 |
| 执行环境漂移 | 开跑前复验 manifest 自身哈希、源码、依赖规格与 SDK 版本 |
| 失败 CLI 返回成功 | finished 且完整性通过返回 0；failed/审计失败返回 1；cancelled 返回 2 |
| 页面与协议不一致 | 服务端共享模板和预检；单次生成 / 通用重试 / 规则反馈最多 1 / 3 / 3 次，评测关闭反思与记忆 |
| 报告不可展示 | 详情页呈现分母、失败记录、原始输出、校验和报告下载，并链接比赛回放 |
| 终局恢复缺失 | 非活动比赛返回持久化快照、最终分数和 watermark；直接打开已结束比赛也加载牌桌 |
| mock 正常出牌覆盖不足 | 增加正常首答、仅出牌错误场景，同时保留首次错误与流局场景 |
| 环境与门禁缺失 | 持久 conda 环境、可移植启动脚本、浏览器验收脚本及 GitHub Actions 门禁 |

## 验证记录

- 后端全量：313 passed，510.44 秒。随后增加的两个真实 CLI 子进程回归均通过（正常完成、预算耗尽）；即 315 个已通过测试，分别保存全量与追加结果。
- 最后修正 mock 领出提示识别及评测选手显示名称后，受影响的可靠性/API/CLI 测试再次执行：34 passed，46.37 秒（包含新增的两个 CLI 用例）。全量记录早于这两个最终改动，最后复验范围和源码指纹保存在验证摘要中。
- 前端：3 个测试文件、6 项测试通过；Vite 生产构建通过。浏览器单独检验真实页面，组件测试不替代浏览器验收。
- 7 类独立数据损坏均触发审计失败：模型成功误标、原始输出变更、桌分、手分、比赛总分、预算孤儿关联、错误结算金额。
- 取消测试覆盖提前及超过期限的迟到返回：20 ms 配置，run 在 150 ms 测试界限内结束；之后没有新增调用记录或叫牌动作。该界限包含调度/持久化开销，不是对任意负载的 20 ms 实时保证。
- `pip check`、`bash -n start.sh`、`git diff --check` 通过。
- 浏览器：在生产构建上完成页面创建→预检→启动→报告/失败记录→下载→回放；双观众的 10 条共同实时事件逐条一致；观众断网期间比赛结束后，重连和刷新均拿到终局快照，分数与 REST 一致，页面异常为 0。
- 本轮出牌错误 mock：6 个任务、12 个桌局全部正常完成；732 次合成调用及预算关联全部审计通过。三组各有 232 次出牌决策，单次组 12 次兜底，两组重试各 12 次恢复。这是明确注入的行为，不是模型提升证据。
- 启动脚本：隔离数据库下前后端健康检查通过；退出后两个端口均释放。

证据：[mock 报告](reviews/20260922-round2/mock-report/report.md)、[完整性审计](reviews/20260922-round2/mock-report/integrity.json)、[浏览器结果](reviews/20260922-round2/browser/result.json)、[报告截图](reviews/20260922-round2/browser/evaluation-report.png)、[回放截图](reviews/20260922-round2/browser/replay.png)、[终局重连截图](reviews/20260922-round2/browser/terminal-reconnect.png)。同目录保留 Playwright trace、压缩 mock 数据库、完整失败案例、JUnit 及逐文件 SHA-256，归档约 2.7 MB。

最终后端源码指纹：`a1c6482470fe967a3b151773e1ecaf1191c5e41d6ce3c2563896f4cd13a230e2`。受测前端、测试、启动脚本和 CI 配置的逐文件指纹见 [code-fingerprints.json](reviews/20260922-round2/code-fingerprints.json)。

CI 已配置同一 conda 环境、完整后端回归、前端测试/构建和 Chromium mock 验收，上传 JUnit、截图和 trace。本次没有推送或在 GitHub 执行，不能称为“远端 CI 已通过”。

## 本机环境与复现

本机已创建 `/home/guozy/miniconda3/envs/doudizhu-arena`，安装 Python 3.12.13、Node 22.23.2、后端依赖、开发测试依赖、Playwright Chromium；前端依赖已安装。`conda env list` 可看到该环境。系统注入的 Ascend Python 路径会污染依赖检查，因此启动与验收使用 `-I` 隔离解释器；`-B` 避免继续生成字节码。

新机器在项目根目录执行 README 的 `conda env create -f environment.yml` 与 `npm ci`。环境规格规定版本范围，运行 manifest 记录实际 SDK 版本；尚未交付跨平台完整依赖锁。

本机启动（项目根目录）：

```bash
DOUDIZHU_ENV_PREFIX=/home/guozy/miniconda3/envs/doudizhu-arena bash start.sh
```

访问 `http://localhost:5173/evaluations/new`，选择「本地 mock 演示」「出牌错误与重试」→ 运行预检 → 创建运行计划 → 启动 mock。完成后查看可靠性报告、展开失败记录、下载 `integrity.json`，然后进入对应比赛回放。该页面默认每场 1 手、2 个种子、3 个变体，共 6 个任务；无需 API Key。

自动浏览器验收（项目根目录，输出目录须不存在）：

```bash
conda run -n doudizhu-arena python -I -m playwright install chromium --only-shell
conda run -n doudizhu-arena python -I -B scripts/verify_browser.py --output data/browser-demo-01
```

Linux 若缺少浏览器系统库，首次安装可使用 `playwright install --with-deps chromium --only-shell`。脚本先构建前端，再使用 Vite preview、新 SQLite 数据库、空模型配置并禁用真实 provider，只启动自己的临时本地服务；测试结束关闭服务，保留数据、截图与 trace。合成 provider 每次增加 60 ms 延迟以便观察，不能把这里的延迟作为模型性能数据。

后端及前端回归：

```bash
cd src/backend
conda run -n doudizhu-arena python -I -B -m pytest -q -p no:cacheprovider
cd ../frontend
conda run -n doudizhu-arena npm test
conda run -n doudizhu-arena npm run build
```

纯 CLI mock（项目根目录，换用新的 DB/输出路径保留每次证据）：

```bash
conda run -n doudizhu-arena python -I -B -m arena.evaluation.run \
  --spec src/backend/evaluation/experiments/mock-v2.yaml --mock \
  --db data/mock-demo-01.db --output data/mock-demo-01
```

此 YAML 默认 `initial_error`，刻意覆盖流局和零分母。要验证所有变体的出牌阶段，使用页面中的正常完成或出牌错误场景。源码/依赖变化后旧 manifest 会被拒绝执行；应新建运行计划，保留旧证据，不能通过修改哈希强行续跑。

## 简历项目标准：已经具备什么，还缺什么

以 **LLM 应用后端 / Agent 可靠性工程项目** 定位，已具备可展示的核心实现与工程回归证据。可讲述状态机、异步双桌、结构化输出校验、失败恢复、事件一致性、计量分母与审计，内容足以支撑一次技术面试。若定位为 **证明规则反馈改善真实模型表现的实验项目**，关键证据仍缺失。

| 优先级 | 缺口 | 达标证据 |
| --- | --- | --- |
| 已完成 | 真实模型 smoke 与真实失败案例 | [09-24 报告](uni-api-real-test-20260924.md)：Qwen / Minimax 各三协议，567 次正式调用、10 个正常结束和 2 个流局全部通过审计；保留自然发生的非法输出→反馈→恢复证据。每模型仅 1 个 seed，不提供统计提升结论 |
| P0 | 发布与独立复现 | 整理当前未提交改动，固定一个可追溯提交；干净检出运行 CI 与演示；核对个人贡献；清理仓库中历史跟踪的缓存/构建产物须另按删除规则处理 |
| P1 | 公平对照与统计结论 | 通用重试和规则反馈相同调用/输出预算；固定观察与闭环比赛分开；开发/测试按来源 seed 与 match 隔离；按独立 seed 做配对统计/区间，报告失败率、成本、延迟和样本量 |
| P1 | 固定观察真实执行管线 | 当前固定观察 CLI 仅开放 mock；真实调用还需接入统一 manifest、预算账本、usage 与取消保障，不能绕过现有执行限制直接跑真实模型 |
| P1 | 面试呈现 | 3 分钟演示、架构和关键取舍、一个故障前后案例；明确自己负责哪些模块及如何验证。当前回放链接到比赛，精确跳转失败动作可继续改进 |
| P2 | 服务化证据 | 并发观众/比赛压测、资源与延迟数据、进程重启演练、部署验收；目前单进程事件分发和 SQLite，未验证多副本一致性或生产 SLA |

首轮真实 smoke 已按最终冻结规格完成：每模型 1 个固定 seed、三协议、每次最多 8192 输出 token、每模型调用上限 2000、决策超时 600 秒，按用户确认的免费账户计价。下一步优先固定受测提交并做干净检出/CI；需要研究反馈效果时，再制定固定观察、多 seed 和实际返回模型版本的对照方案。

可采用的简历表述（需核对实际个人分工）：

> 构建基于 FastAPI、Vue 和 SQLite 的 LLM 斗地主可靠性评测平台，实现双桌复制赛、规则反馈重试、合法兜底及决策—调用—动作关联；通过持久化事件序号与权威快照支持多观众观战和终局恢复。
>
> 设计冻结实验输入、调用预算预留、取消后写入隔离与语义完整性审计，通过故障注入验证成功率、计分和成本证据，提供无需密钥的 mock 演示与自动化回归。
>
> 完成 Qwen 与 Minimax 三协议真实 smoke，567 次正式调用、12 个终态桌局通过动作重放与计分审计，留存非法输出、规则反馈、重试恢复及兜底的完整证据。

目前可以写“真实模型 smoke 完成”，不能写“规则反馈提升成功率 X%”“达到生产级稳定性”或“已完成足量样本的真实模型对照实验”。mock 成功率和重试收益由合成 provider 设计决定；本轮单 seed 自对弈也不能证明可推广的反馈提升或策略竞技优势。

## 保证边界与证据保存

- 取消期限约束运行终态与后续持久化权限；Python 无法强制终止任意永远忽略取消的协程。供应商端请求或计费可能继续，未知成本保留占额；尚无独立进程强制隔离。
- 哈希用于追溯与一致性检查，不是第三方签名。完整性通过表示规定的检查通过，不能证明不存在所有可能的数据问题。
- 备份沿用本日 `/tmp/doudizhu-arena-backups/daily-2026-09-22/project`，7.5 MB；旧指令的 `/home/k4s9` 路径在本机不存在。`/tmp` 不保证长期保留，关键验收结果另归档入文档目录；本轮未删除文件。
- 09-23 会话开始后，按本机实际用户目录新增持久备份 `/home/guozy/.codex/backups/doudizhu-arena/daily-2026-09-23/project`，189 MB，包含已安装的前端依赖。没有删除任何旧备份。
- 09-22 验收无真实模型调用，演示及测试全部为合成 provider 或独立故障注入。09-24 后续真实调用及边界见上方更新。
