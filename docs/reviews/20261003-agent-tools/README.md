# 两个纯算法 Agent 工具：验收记录

日期：2026-10-03（Asia/Shanghai）。使用方法、算法证明边界、性能说明及面试讲述见[设计文档](../../agent-tools-design-and-interview.md)。

## 交付范围

- `compare_hand_plans`：比较 1–3 个合法候选出法后的静态剩牌分组，可约束保留炸弹；返回可行分组、最优性上下界和搜索完成标记。
- `analyze_threat`：依据公开信息检验另一活跃牌手能否压牌或一手走完，返回 `possible`、`ruled_out` 或 `unknown`，可行时附假设见证。
- 共用完整牌型生成器、当前决策的观察快照和有界注册表；OpenAI/Claude 原生调用、总调用预算、日志和创建比赛开关均已接入。
- 修复飞机带翼识别忽略多余牌的问题，规则版本为 `duplicate-four-seat-v3-complete-airplane`。

## 最终验证

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 后端完整回归 | 614 passed，215.59 秒，0 次网络连接尝试 | [backend.xml](backend.xml) |
| 前端测试 | 5 个测试文件、11 项通过 | 同日上一聊天的 `conda run -n doudizhu-arena npm test` 成功输出 |
| 前端生产构建 | 成功，71 个模块 | 同日已生成 `/tmp/doudizhu-agent-tools-build-20261003/index.html` |
| 算法性能 | 19 类场景，各 100 次；0 次网络连接和 DNS 尝试 | [benchmark.json](benchmark.json) |
| 基准源码一致性 | 脚本、三个算法模块、正式规则共 5 个文件的 SHA-256 全部匹配 | 对照 `benchmark.json` 的 `source_sha256` |
| 差异格式 | `git diff --check` 通过 | 本次最终检查 |

后端覆盖小规模独立穷举 oracle、15 类牌型、花色等价、底牌容量约束、隐藏信息隔离、参数与计算预算、取消后不写入、供应商原生协议及预算账本。完整复式对局测试使用真实本地算法与 mock 模型，验证两个工具的结果、最终动作和持久化证据贯通。

本次还修正了三个既有 M4 测试的测试配置：它们检验数据库、复盘和总结，却因相同 mock 策略打平，额外执行默认最多 101 副加赛。现明确关闭这些测试的加赛，并将副数断言收紧为精确值；独立加赛测试仍在全量回归中执行。单独验证见 [backend-m4.xml](backend-m4.xml)。

沙箱内执行时，SDK 和文件响应在异步工作线程完成后的事件循环唤醒处挂起。最终全量回归获准在沙箱外执行，仍通过 `scripts/test_offline.py` 拦截网络连接。上次聊天中全量进程异常退出的直接原因未独立确认；以此次成功完成的 `backend.xml` 为最终结果。[integration-final.xml](integration-final.xml)为同日此前的 60 项专项报告，不额外累加进 614 项总数。

## 复现

在项目根目录运行：

```bash
conda run --no-capture-output -n doudizhu-arena python -I -B scripts/test_offline.py --junitxml=/tmp/agent-tools-backend.xml --durations=10
conda run --no-capture-output -n doudizhu-arena python -I -B scripts/benchmark_agent_tools.py --output /tmp/agent-tools-benchmark.json
conda run --no-capture-output -n doudizhu-arena npm --prefix src/frontend test
conda run --no-capture-output -n doudizhu-arena npm --prefix src/frontend run build -- --outDir /tmp/agent-tools-frontend-build
```

本机默认系统 Node 版本不足以启动当前 Vitest/Vite，以上前端命令使用项目 conda 环境中的 Node。异步测试需要支持线程唤醒的执行环境。

## 性能结论的范围

20 张手牌比较三个方案的 P95 为 60.788 ms；正常威胁场景中最高的场景 P95 为 1.140 ms。拆牌搜索的 2,100 个候选中，2,054 个完成最优性证明，其余 46 个返回有效分组和上下界。

上述数据只包含本地算法。未调用真实 LLM 做同任务耗时对照，未验证真实网关、对战胜率或整体决策加速；不能给出 LLM 加速倍数。工具增加的模型往返应在后续同预算对照实验中单独计量。
