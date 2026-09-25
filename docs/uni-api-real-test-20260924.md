# Uni API 真实模型测试（2026-09-24）

**已完成并复核。** 两模型各 3 个任务全部结束，共 567 次正式对局调用、538 个决策、12 个终态桌局（10 个正常结束、2 个无人叫分流局）。Qwen 于北京时间 12:06:58 完成，Minimax 于 11:55:00 完成。续接会话使用原始数据库完成只读审计，未追加真实 API 调用。

本轮用户授权使用已有 `agents.yaml` 进行真实模型测试，并说明 Uni API 账户免费、可延长超时。沿用现有 conda 环境和未提交工作区；日备份为 `/home/guozy/.codex/backups/doudizhu-arena/daily-2026-09-24/project`，201 MB。

## 测试配置

- 使用 `Qwen-Balanced / qwen3.5` 与 `Minimax-Balanced / minimax-m27`；地址均为 `https://uni-api.cstcloud.cn/v1`，凭据从现有配置解析。
- 每模型独立运行，1 个固定种子，单次生成 / 通用重试 / 规则反馈三组，每组 1 手、2 桌；关闭反思、记忆、加赛。自对弈 smoke 不提供模型实力排名或统计显著性结论。
- 每次最多 8192 输出 token；temperature=0、top_p=1，不传 sampling seed。两种重试协议均最多生成 3 次，单次生成组为 1 次。最多同时 4 个请求在途。
- 叫分 / 出牌决策均为 600 秒（含重试），团队时间池 14400 秒，耗尽后仍为 600 秒，每任务上限 14400 秒。SDK 禁止自动重试，使用已有 1800 秒 HTTP 超时，外层决策先到期。
- 每模型最多 2000 次调用，输入占额 65536 token。按用户本轮提供的免费账户信息冻结输入 / 输出单价及预算为 0 USD；这不是供应商公开标价或账单核验结果。未知 usage 仍单独保留，不能伪造为已知零 token。
- 原始数据库位于 `data/real-smoke-20260924/{qwen,minimax}/arena.db`，与正式应用数据库隔离。报告不含 API 密钥；数据库中的配置凭据沿用本机主密钥加密。

## 实测发现与修复

1. 原执行预检强制真实模型输出单价大于零，无法使用免费账户。现在支持有来源、生效日期的零价格和零预算，仍拒绝缺失计价、负数 / 非有限预算、零预算配付费单价；调用次数上限继续生效。前端区分显式填 0 与留空。
2. Minimax 探测返回内联 `<think>…</think>`，正文并非纯 JSON。旧解析器可能读取思考过程中的示例动作。现在先跳过完整的开头思考块，再解析最终答案；未结束的思考、缺失或非法最终答案仍失败，原始响应保留在证据中。解析和重放审计使用相同入口。
3. 简单探测中 Qwen 返回 `Qwen3.5-397B-A17B`，耗时 7.482 秒；Minimax 返回过 `minimax-m27-eic` 和 `minimax-m27`，耗时 4.090 / 3.229 秒。别名实际解析可能变化，完整对局保留每次返回的模型名。

证据：[`probe.json`](reviews/20260924-real-models/probe.json)、[`probe-minimax-format.json`](reviews/20260924-real-models/probe-minimax-format.json)、[`parser-before-after.json`](reviews/20260924-real-models/parser-before-after.json)。探测的 `ok` 检查严格纯 JSON，所以 Minimax 为 false；这是响应格式结果，并非远端鉴权失败。其最终 JSON 经修复后的应用解析器验证成功。

## 回归验证

本轮 121 项针对性后端回归通过（两组 49 + 72，测试身份无重复），0 次网络连接尝试；8 项前端测试及生产构建通过。不是对历史全部测试重新验收。沙箱内初次异步测试受套接字限制，中断后在网络拦截仍开启的沙箱外通过；新增解析测试首次使用了错误的牌面符号，修正后通过。

摘要及 JUnit：[`validation-summary.json`](reviews/20260924-real-models/validation-summary.json)。

## 复现

先运行本地预检；输出路径应使用新名称，避免覆盖证据：

```bash
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/preflight_uni_api.py --spec src/backend/evaluation/experiments/uni-api-qwen-smoke-20260924.yaml
```

下列命令会调用真实 API，输出目录必须不存在：

```bash
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/run_real_smoke.py --spec src/backend/evaluation/experiments/uni-api-qwen-smoke-20260924.yaml --output data/real-smoke-next/qwen
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/run_real_smoke.py --spec src/backend/evaluation/experiments/uni-api-minimax-smoke-20260924.yaml --output data/real-smoke-next/minimax
```

入口使用正式 runner、预算账本、语义审计及报告导出，并在独立数据库中加载既有 YAML。每 30 秒输出脱敏进度；SIGINT / SIGTERM 走 runner 的取消流程。每次创建新 manifest，不修改旧哈希绕过源码校验。

## 完整对局结果

下表合并叫分与出牌；“首次成功”和“重试成功”只统计模型动作。兜底是本次决策调用失败后的系统动作，托管是在某座位连续 3 次失败后直接使用系统动作，不再调用模型。四类互斥，相加等于决策数。

| 模型 | 协议 | 决策 | 调用 | 首次成功 | 重试成功 | 兜底 | 托管 | 正常 / 流局 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Qwen | 单次生成 | 105 | 105 | 104 | 0 | 1 | 0 | 2 / 0 |
| Qwen | 通用重试 | 96 | 98 | 94 | 2 | 0 | 0 | 2 / 0 |
| Qwen | 规则反馈 | 87 | 89 | 85 | 2 | 0 | 0 | 2 / 0 |
| Minimax | 单次生成 | 61 | 46 | 38 | 0 | 8 | 15 | 1 / 1 |
| Minimax | 通用重试 | 126 | 154 | 106 | 17 | 3 | 0 | 2 / 0 |
| Minimax | 规则反馈 | 63 | 75 | 54 | 9 | 0 | 0 | 1 / 1 |

Qwen 共 292 次调用、288 个决策：283 次首答成功、4 次重试成功、1 次兜底。通用重试组的一次空正文和一次 `stream timeout`（约 180 秒）均恢复；单次生成组的一次上游连接终止直接兜底。两次供应商错误的 usage 未知，完整调用覆盖为 **290/292**。

Minimax 共 275 次调用、250 个决策，其中 235 个决策调用了模型：198 次首答成功、26 次重试成功、11 次兜底、15 次托管。供应商请求全部返回，usage 覆盖为 **275/275**。单次生成组的 15 次托管不能计为模型成功，也不能从全部决策分母中隐去。规则反馈组恢复了 9/9 个首答无效决策，通用重试组恢复了 17/20 个；这是本次闭环对局的描述性结果。

出牌阶段模型成功率（分母为实际调用模型的决策）分别为：Qwen 单次 **99/100**、通用重试 **91/91**、规则反馈 **83/83**；Minimax 单次 **32/40**、通用重试 **117/120**、规则反馈 **57/57**。Minimax 单次组另有 15 次托管，全部出牌决策为 55 次；对应兜底率为 8/55、托管率为 15/55。叫分、出牌的完整分母和各项指标见归档报告。

| 模型 | 调用延迟 P50 / P95 / 最大值 | 已知输入 token | 已知输出 token | 实际返回模型名 |
| --- | --- | ---: | ---: | --- |
| Qwen | 19.083 / 89.977 / 180.234 秒 | 354481 | 352666 | `Qwen3.5-397B-A17B` × 290；错误无返回模型名 × 2 |
| Minimax | 17.814 / 39.941 / 84.808 秒 | 321752 | 176184 | `minimax-m27` × 127；`minimax-m27-eic` × 148 |

延迟包含失败调用，P95 采用 nearest-rank；一次决策含重试时可能更长。已知费用按用户确认的免费账户计价为 0 USD，565 条已结算、2 条 usage 未知，仍保留未知状态；这不是供应商账单核验。567 次仅包含正式对局，之前的连通性/格式探测另见探测记录。

规则反馈组的[真实恢复案例](reviews/20260924-real-models/rule-feedback-example.json)：Minimax 首次输出 `♣A ♦A ♠Q ♠3`，并非合法牌型；收到具体规则错误后，第二次生成 `♠Q ♥Q ♦Q ♦4`，通过校验并提交。文件包含原始输出、反馈后的实际提示词、校验事件、提交动作及状态哈希；错误没有人为注入。

## 最终审计与归档

| 模型 / 完成的 run | 汇总报告 | 只读复核 |
| --- | --- | --- |
| Qwen `781a96ead0c7432bb68a90968d0387ac` | [报告与分阶段指标](reviews/20260924-real-models/final/qwen/report/report.md) | [audit.json](reviews/20260924-real-models/final/qwen/audit.json) |
| Minimax `33eac844f2e244c8a1b8898164b358e7` | [报告与分阶段指标](reviews/20260924-real-models/final/minimax/report/report.md) | [audit.json](reviews/20260924-real-models/final/minimax/audit.json) |

两模型均通过数据库完整性/外键、manifest 自身与冻结规格哈希、源码/依赖/SDK 一致性、决策语义、动作及事件关联、全部终局重放/计分、调用与预算账本审计。新导出的报告与原报告一致；0 次网络连接尝试。复核沿用引擎重新执行动作，独立于存储的成功标签，但不是另一套规则引擎或第三方审计。

归档包含冻结 manifest、指标 CSV、seed 指标、12 个终态哈希和 **42 个失败/恢复决策的完整证据**（Qwen 5 个、Minimax 37 个），每模型提供 `SHA256SUMS.json`。原始数据库继续留在隔离的 `data/real-smoke-20260924/`；包含本机加密凭据，不随文档归档。全批机器可读摘要见 [completion-summary.json](reviews/20260924-real-models/final/completion-summary.json)。

本次受测后端源码 SHA-256 仍为 `67f0d30788a4a92cfdcbc87de146dac66206716676ba9e57fa22eb09dbaf9ac8`，与两份运行 manifest 及此前 121 项针对性回归记录相同。续接仅添加只读审计脚本、归档证据和更新文档，没有更改模型运行逻辑。

在项目根目录使用现有 conda 环境，可离线重复复核（输出目录必须不存在）：

```bash
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/audit_real_smoke.py --run-dir data/real-smoke-20260924/qwen --output data/real-smoke-audit-next/qwen
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/audit_real_smoke.py --run-dir data/real-smoke-20260924/minimax --output data/real-smoke-audit-next/minimax
```

## 可以得出的结论与后续缺口

真实 API → 模型输出 → 规则拒绝 → 重试/反馈 → 提交或兜底 → 终局 → 离线审计这一链路已完成验证，并留存了自然出现的恢复案例，可用于项目演示和面试。可以描述为“完成两模型、三协议真实 smoke，567 次正式调用及 12 个终态桌局通过审计”。

每模型只有 1 个独立 seed。各协议会走入不同局面，流局和托管也改变了后续样本；Minimax 别名还返回了两个模型名。不能据此声称规则反馈带来可推广的提升百分比、模型实力排名或统计显著性。后续若研究反馈效果，需要固定观察对照、更多独立 seed、版本控制和统计区间。工程上仍需固定可追溯提交、干净检出与远端 CI；本轮未提交或推送。
