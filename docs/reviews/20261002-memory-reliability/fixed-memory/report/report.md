# 固定局面记忆对照评测

来源：mock；审计通过：True；物理调用：90。

本报告只衡量固定可见局面的输出合法性和成本，没有完整对局分数或胜率。mock 不证明学习增益。

| 组别 | 决策数 | 首答合法率 | 最终合法率 | 平均重试 | 需要兜底率 | 预算占用 USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| no_memory | 16 | 12.5% | 100.0% | 0.88 | 0.0% | 0.0 |
| fixed_initial | 16 | 12.5% | 100.0% | 0.88 | 0.0% | 0.0 |
| frozen_memory | 16 | 12.5% | 100.0% | 0.88 | 0.0% | 0.0 |

“需要兜底”只表示模型重试耗尽，本实验实际执行的兜底动作数为 0。逐 seed 对照见 paired-seed.csv。

Mock results verify the pipeline only; they do not show learning gains.
No full games are executed: no win rate, score or playing-strength estimate.
fallback_required marks exhausted model output; no fallback action is executed.
One predeclared frozen memory slot; no reflection or memory updates during evaluation.
Rule-policy corpus and seed-paired descriptive outcomes; not representative human play.
Known token totals exclude unknown usage; unknown cost retains its reserved allowance.
Provider randomness can remain despite frozen inputs; exact output reproduction is provider-dependent.
