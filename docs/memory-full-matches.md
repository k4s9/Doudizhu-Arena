# 完整对局记忆对照

该独立入口补充固定局面评测的对局得分，沿用生产 MatchRunner、LLMAgent 和规则随机对手，不改变首轮 reliability 协议。

每个测试 seed 对 `no_memory`、`fixed_initial`、`frozen_memory` 各执行两场双桌比赛：被测队先后作为红方和蓝方。四个被测牌手使用同一模型与预声明记忆槽位；四个对手为按 seed/稳定槽位重建的 RandomAgent。三组使用相同发牌种子、座位安排、通用重试和预算上限，执行顺序冻结并打乱。初始策略与冻结经验均通过相同长期记忆字段注入；测试中关闭反思、总结和长期记忆更新，保存初始记忆快照用于审计。

报告提供被测队积分、对手积分及有方向的积分差（被测队减对手）。生产引擎每副将 A/B 双桌差分积分记到获胜队，另一队得 0 分；保留双方累计积分能区分 0 分输局与平局。报告同时提供胜/平/负及胜率、首答/最终合法性、重试、兜底/托管比例、token、延迟、已知费用与未知用量的保守预算占用。按 seed 合并红蓝两场后再比较三组；这些相关决策和同 seed 的两场不能当作独立训练增益样本。实际比赛中的兜底行为会影响得分，应同时查看其比例。

离线验收（每个输出目录必须不存在）：

```bash
PYTHONDONTWRITEBYTECODE=1 /home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/memory_match_study.py prepare --mock --output /tmp/ddz-memory-match-plan
PYTHONDONTWRITEBYTECODE=1 /home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/memory_match_study.py run --mock --plan /tmp/ddz-memory-match-plan --output /tmp/ddz-memory-match-run
PYTHONDONTWRITEBYTECODE=1 /home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/memory_match_study.py audit --run-dir /tmp/ddz-memory-match-run --output /tmp/ddz-memory-match-audit
```

默认 1 个测试 seed，每场 1 副，合计 6 场双桌比赛和 12 个桌副。mock 不读取记忆内容，因此相同 seed/颜色下三组得分一致；这只能证明流程和对照可用，不能证明真实模型学习增益。正式实验应在查看测试结果前声明更多独立 seeds 和足够副数，固定弱随机对手也不能代表人类或更强模型。

真实准备接受与[固定局面协议](memory-evaluation.md)相同的 `--model-json`、`--memory-artifact`、`--initial-strategy`、`--test-seed-file`、`--memory-slot`。另用 `--hands` 固定每场副数、`--max-calls` 限制整个实验调用数，`--budget-usd` 必须覆盖所有调用配额的保守预留。产物声明 real 来源并校验 schema、训练 seeds、模型/endpoint、源码指纹；训练和测试 seed 不得重叠。`prepare` 不调用模型，`run --real-models` 才读取 `DDZ_MEMORY_EVAL_API_KEY`。全程不保存密钥，源码变更必须重新准备计划，失败实验保留数据库并使用新目录重新实验。

`run` 为每次实验建立独立数据库；该审计入口不接应用共享数据库。`audit` 只读原数据库，重放逐桌叫分和出牌，重算终局及双桌差分分，检查双方座位、随机策略、冻结提示词、原始输出解析、预算账本和记忆前后不变。数据库里未归属于该实验的额外比赛或调用也会导致失败。报告审计失败时 `complete=false` 并返回非零退出码，不能把不完整比赛计为成功实验。
