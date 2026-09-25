# Doudizhu-Arena 当前项目评估（2026-09-22）

> 本文记录第二轮实施前的缺口与原始证据。修复后的结果、当前环境和简历差距请见 [第二轮交付说明](reliability-delivery.md)，不要将下面的旧测试结果视为当前状态。

**结论：后端已具备可运行的 mock 可靠性评测闭环，但当前工作区还不能作为已验收的可靠性平台交付。** 主要缺口是完整性审计漏检、实验页面与执行协议不一致、终局重连未闭环，以及取消期限未落实。项目值得继续完成，现阶段适合描述已经实现和验证的工程能力；尚无真实模型可靠性提升的证据。

本次接续任务 `01a0c1ea-eca3-7d71-8ab6-a0a4d39eb647`，评估当前工作区，新增评估文档与复现证据。原任务中的实现改动仍未提交。基线 HEAD 为 `3bb8d2bbd948f0e5291d3500af1b1ee9ebf34a88`；不能用这个提交号单独代表本次受测代码。

受测后端源码指纹：`3335daf2b5a09fd444c5a041f42fb44b0e1c3df6afe4e9d99f4080ba4eec7e33`。

## 1. 本次实际验证

| 验证 | 结果 | 证据与解释 |
| --- | --- | --- |
| 后端全量 pytest | **298 通过，2 失败**；300 项，481.267 秒 | [JUnit 原始结果](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/backend-tests.xml)。两个失败的原因见第 3 节，不能报告全绿 |
| mock 完整赛程与导出 | 6 个 task 全部 finished；12 个 table-hands，其中 8 个正常终局、4 个流局；420 次合成调用 | [报告](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/mock-report/report.md)，五类结果文件和 manifest 已归档。现有审计返回 complete=true，但保障边界见下文 |
| 固定观察对照 | 按来源 seed 分成开发集 8 条、测试集 8 条；测试集三组共 24 次决策 | [结果摘要](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/fixed-observation-summary.json)。这是合成样本流程验证 |
| 领出 pass 与广播修复 | 在 HEAD 导出的基线及当前代码上各执行一次，确认前后行为变化 | [修复前](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/leader-pass-before.json)、[修复后](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/leader-pass-after.json)。当前解析器/引擎均拒绝领出 pass；两个观众都收到 `[0,1]` |
| 数据完整性故障注入 | 发现三类漏检 | 只修改专用数据库副本，原始 mock 数据保持独立；详见第 2 节 |
| 取消期限 | 50 ms 的有效配置未约束任务退出，约 261 ms 后才 cancelled | mock provider 在收到取消后执行 250 ms 清理；到期时 run 仍 running |
| 预算停止 CLI | `max_calls=1` 导致 run failed，但进程退出码为 0 | [验证摘要](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/validation-summary.json) |
| 前端逻辑与 API 协议 | 提取并执行实际 `spec()` / `handleEvent()`，结合后端预检和 WebSocket 路由复现缺口 | [前端状态证据](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/frontend-boundary-results.json)、[预检证据](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/frontend-preflight-results.json)。隔离的逻辑验证，不等于浏览器 E2E |
| 前端构建 / Vitest | **未完成** | Node 20.11.1 低于当前 Vite 8 要求；离线安装又因 `ws-8.21.0.tgz` 无缓存失败。不能据此判断前端构建通过或代码构建失败 |

mock run：`ac0ccc037ba6471c8e83d3f12c78fd7c`。manifest SHA-256：`35756aa2858e1b3273e750e25bbd59c2561d1d8f715f1f11d5460e25d9e2ce21`。

## 2. 优先修复的问题

### P1：审计未验证“模型成功”的语义，也未覆盖终局分数与成本关联

[report.py:111](/home/guozy/Doudizhu-Arena/src/backend/arena/evaluation/report.py:111) 检查调用存在、attempt 连续、验证记录数量；[report.py:145](/home/guozy/Doudizhu-Arena/src/backend/arena/evaluation/report.py:145) 直接根据 resolution 统计模型成功。没有确认对应 attempt 真正通过规则验证，或最终模型动作与提交动作一致。

在从正常 mock run 复制的三个独立数据库中，分别注入下列错误：

| 注入错误 | 当前审计表现 | 影响 |
| --- | --- | --- |
| 将一个有 3 条非法输出记录的 `system_fallback` 改为 `model_first` | complete=true、issues=[]；模型成功数由 312 增至 313 | 错误分类可以污染核心成功率而不被审计发现 |
| 将正常终局的 winner_team 改为 `invalid-team`，桌分/比赛总分改为 99999 | complete=true、issues=[] | [replay_audit](/home/guozy/Doudizhu-Arena/src/backend/arena/evaluation/report.py:55) 验证动作状态及剩余手牌，却未比对持久化胜方、计分与总分 |
| 将一条预算结算的 call_id 改为不存在的调用 | complete=true，cost_coverage 仍为 420/420 | [成本汇总](/home/guozy/Doudizhu-Arena/src/backend/arena/evaluation/report.py:204) 用行数与求和代替调用和结算的对应审计 |

这些是**主动故障注入的结果**，证明审计保障不足；本次没有观察到正常 mock 自动把兜底记为成功。[原始证据](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/boundary-results.json)。

建议：按 decision → attempt → validation → proposal → committed action 校验终态；重算并比对桌级/手级/比赛级得分；验证调用与预算账本的一一对应、状态与价格计算。验收应要求上述三个错误分别使审计失败，而原始 mock 继续通过。

### P1：实验创建页无法创建当前协议允许的实验

[EvaluationCreate.vue:16](/home/guozy/Doudizhu-Arena/src/frontend/src/views/EvaluationCreate.vue:16) 仍固定提交“无反馈 / 规则反馈 / 复盘记忆”，没有 `generic_retry`，没有模型价格，也没有只读记忆制品字段。

使用页面实际 `spec()` 生成请求，并提供存在的 mock 模型配置后，[API 预检](/home/guozy/Doudizhu-Arena/src/backend/arena/api/routes/evaluation.py:41) 返回“只读记忆变体必须指定冻结的 memory_artifact_path”。即使补上制品，[执行校验](/home/guozy/Doudizhu-Arena/src/backend/arena/evaluation/spec.py:366) 仍禁止本轮的 reflection/memory。页面没有调整这组固定变体的入口。

页面默认会规划 300 个 task（100 seeds × 3 variants，每个 task 20 hands），本次只执行预检，未启动这些任务。页面默认值不构成真实调用授权。

建议：创建页、预检、CLI 和 runner 共用本轮的三组协议与校验；提供显式模型价格、调用数、预算和 mock 模式；用小规模规格作为演示起点。详情页还需接入已有 `/report` 和 artifacts API，显示指标、完整性问题、失败 decision，以及对应比赛回放链接。目前 [EvaluationDetail.vue](/home/guozy/Doudizhu-Arena/src/frontend/src/views/EvaluationDetail.vue:1) 仍只有任务状态/进度。

### P1：取消期限是配置字段，尚未成为执行保证

[spec.py:87](/home/guozy/Doudizhu-Arena/src/backend/arena/evaluation/spec.py:87) 定义 `cancellation_deadline_seconds`，执行路径没有使用它。[runner.py:128](/home/guozy/Doudizhu-Arena/src/backend/arena/evaluation/runner.py:128) 的 cancel 仅设置标志并调用 worker.cancel；`asyncio.wait_for` 本身还会等待协程完成取消清理。

复现：设置期限 0.05 秒，provider 收到取消后用 0.25 秒清理再抛出 CancelledError；期限到达时 run 仍 running，约 0.261 秒后才结束。若 provider 长时间不退出，当前执行器也不能兑现配置期限。

建议：落实独立的取消截止时间，期限后隔离未退出任务并禁止它继续提交动作，正确终结 run/task/decision 并释放占有权；无法确认 usage 的调用保留预算占额。验收覆盖正常取消、延迟清理、忽略首次取消、取消后的迟到结果及恢复。

### P2：比赛结束后重连会停在旧的进行中状态

评测 runner 结束后从 active_matches 移除比赛。此时 [ws.py:202](/home/guozy/Doudizhu-Arena/src/backend/arena/api/ws.py:202) 只发送 `replay_available`，没有终局状态、分数或 watermark。[MatchView.vue:118](/home/guozy/Doudizhu-Arena/src/frontend/src/views/MatchView.vue:118) 的消息处理不包含这个类型，重连时也不会重新读取 REST 比赛结果。

执行实际 WebSocket 路由确认首条消息为 `replay_available`；将此消息交给页面实际 handleEvent 后，状态仍 running、牌桌仍 playing。触发条件是观众断线期间比赛结束，随后恢复连接。

建议：对非活动比赛返回持久化终态快照，或由客户端处理 replay_available 后重新读取权威状态并进入回放。验收需涵盖“断线期间结束”，并核对最终分数和桌状态，而不仅测试事件队列广播。

### P2：失败实验的 CLI 退出码为成功

[run.py:45](/home/guozy/Doudizhu-Arena/src/backend/arena/evaluation/run.py:45) 在 runner 返回后打印 run 状态，却没有据此返回非零退出码。使用一调用上限的 mock 规格，程序输出 `status=failed`，shell 退出码仍为 0。脚本和 CI 若只检查退出码，会把未完成实验当作执行成功。

建议：为 failed / cancelled / integrity failure 定义清楚的非零退出码。`integrity_complete` 应继续与“任务成功完成”区分：失败实验也可以拥有完整的失败证据；不能单独把 complete=true 当作实验成功。

## 3. 两个现存测试失败的判断

1. [test_agent.py:188](/home/guozy/Doudizhu-Arena/src/backend/tests/test_agent.py:188) 的 `test_pass` 没传 current_trick，却期待 pass 成功，与本轮领出规则冲突。应使用一个需要跟牌的场景测试允许 pass，并保留独立的领出拒绝测试。没有理由为让旧测试通过而放宽引擎规则。
2. [test_reliability_v2.py:73](/home/guozy/Doudizhu-Arena/src/backend/tests/test_reliability_v2.py:73) 比较无 ORDER BY 的查询与调用顺序。复核得到两条不同 decision 的调用都存在，第三次进入托管；查询计划使用 `idx_llm_logs_decision` 覆盖索引，返回顺序不代表时间顺序。[证据](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/timeout-order-results.json)。若测试关联完整性，应比较集合及数量；若测试时间顺序，应显式定义排序键。

因此，本次的两个红测不足以证明领出规则或调用关联实现有回归，但需要修正测试契约后才能恢复可用的回归门禁。

## 4. 原六阶段目标目前完成到哪里

| 原目标 | 当前判断 | 尚缺的验收 |
| --- | --- | --- |
| 统一 decision、attempt、resolution、实际动作 | 主链路已实现并有运行证据 | 终态语义审计、上述红测修正、迟到结果隔离 |
| 多观众、序号、快照、断线恢复、回放 | 广播和后端回放校验已运行 | 终局重连；实际浏览器双观众/慢客户端/重连组合测试 |
| 冻结输入、独立 run/task attempt、预算、取消恢复 | 核心结构与部分边界已实现 | 取消硬期限、账本关联审计；运行前复验依赖和 SDK 指纹 |
| 五类报告与 mock 完整比赛 | 制品已生成，可重复运行 | 审计漏检修复；报告内容接入 UI；真实 smoke/pilot |
| 三组对照、固定观察与闭环分离 | mock CLI 和固定观察函数已运行 | 页面协议同步、真实固定观察运行的预算/usage/manifest 集成、足量独立 seed 的统计设计 |
| 失败案例、演示与贡献描述 | 已有执行过的工程回归案例 | 真实模型失败案例、演示说明与详情页下钻；原计划指向的 reliability-delivery.md 尚不存在 |

补充边界：

- 当前 mock 的 single_generation 组全部流局，出牌阶段分母为 0；两组重试则进入正常出牌。这说明流程处理了流局与零分母，但这批 E2E 样本未覆盖 single_generation 的正常出牌全流程。应增加首个输出合法和仅出牌阶段注入错误的场景。
- 固定观察的通用重试和规则反馈在当前合成数据上各成功 6/8，single_generation 为 0/8。这由 mock 行为构造决定，不能作为反馈提升或真实模型优劣的结论。
- 源码复核显示 `validate_execution` 比对 spec/source 指纹，但未复验依赖指纹、SDK 版本或 manifest 自身哈希一致性。探针修改 manifest 的 dependency/sdk 元数据后仍被接受；这不是一次真实依赖升级实验。
- 同策略自对弈、极少 seed 和相关的连续决策，均不能支持策略间竞技优势或总体提升百分比。

## 5. 建议的下一轮实施顺序

1. 修复两个测试契约，并把三类审计故障注入转为必要的回归测试；让“完整性通过”真正约束成功、终局和成本证据。
2. 落实取消截止时间、迟到结果处理及失败 CLI 退出码，再验证取消/预算耗尽后恢复。
3. 同步实验创建与详情页，补终局重连，完成 Node 22.12+ 环境下的前端构建、Vitest 和实际浏览器演示。
4. 补齐 mock 场景、演示说明和冻结规格。随后以实际 provider/model、计价来源和生效日期、明确预算和调用上限定义真实 smoke，再保留现有真实调用确认机制。

现阶段可以据实描述的贡献：**实现自定义双桌赛制的 LLM 决策执行与规则反馈、决策/调用/动作关联、独立观战订阅和持久化事件序号、冻结实验输入及 mock 报告管线，并通过故障注入识别计量与恢复边界。** 不应写“已证明模型可靠性提升 X%”“真实实验完成”或“全部测试通过”。个人贡献仍需结合实际开发分工核对。

## 6. 环境、运行与制品

本机的旧 `/home/k4s9` 路径不存在；沿用上一项任务建立的 `/tmp/doudizhu-conda/envs/doudizhu-arena` 环境（Python 3.12.13）。今日项目备份已存在于 `/tmp/doudizhu-arena-backups/daily-2026-09-22/project`，大小 7.5 MB；/tmp 不保证长期保留。未执行文件删除或真实模型调用。

后端全量测试（在 `/home/guozy/Doudizhu-Arena/src/backend` 运行）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/doudizhu-conda/envs/doudizhu-arena/bin/python -B -m pytest -q -p no:cacheprovider --durations=8
```

mock 演示（同一目录；指定一个新数据库路径以保留各次执行证据）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/doudizhu-conda/envs/doudizhu-arena/bin/python -B -m arena.evaluation.run \
  --spec evaluation/experiments/mock-v2.yaml --mock \
  --db /tmp/doudizhu-review-repeat.db \
  --output /tmp/doudizhu-review-repeat-report
```

本次完整工作数据位于 `/tmp/doudizhu-assessment-20260922`，包括原始 mock DB、独立故障副本、固定观察 corpus 和报告。关键文本证据已复制到 [docs/reviews/20260922](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/validation-summary.json)，无需依赖旧任务的聊天记录。

[probe_boundaries.py](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/probe_boundaries.py) 和 [probe_frontend.mjs](/home/guozy/Doudizhu-Arena/docs/reviews/20260922/probe_frontend.mjs) 保留本次取证代码及本机路径；前者依赖本次 `/tmp` 下的 mock.db，复跑前需先恢复对应输入，且只能使用规定的 conda 环境。所有注入操作只针对明确命名的评估数据库副本。归档的 mock report 引用的逐条 failure-cases 可在原始 `/tmp` 报告或同 run 的 `data/evaluations` 制品中查看。
