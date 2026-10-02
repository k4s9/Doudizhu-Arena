# 完整对局记忆对照评测

来源：mock；审计通过：True；物理调用：288。

每个 seed 的三个记忆组分别执红、执蓝，对手是固定随机基线。小样本结果仅作描述性比较；mock 不证明学习收益。

| 组别 | 比赛数 | 胜 / 平 / 负 | 胜率 | 平均差分分 | 预算占用 USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| no_memory | 2 | 0 / 0 / 2 | 0.0% | -6.000 | 0.0 |
| fixed_initial | 2 | 0 / 0 / 2 | 0.0% | -6.000 | 0.0 |
| frozen_memory | 2 | 0 / 0 / 2 | 0.0% | -6.000 | 0.0 |

胜率分母包含平局；差分分为被评测队伍累计差分积分减去对手累计差分积分，已按执红/执蓝方向统一。

各组合法率、重试、实际兜底/托管、token、时延、费用详见 summary.json 的 metrics；只统计被评测玩家。

逐 seed 配对结果见 paired-seed.csv，差值见 summary.json 的 paired_by_seed。

This audit requires a dedicated experiment database containing exactly one run and no unaccounted matches or model calls.
Fixed seeded RandomAgent baseline only; results do not establish strength against human or competitive agents.
Small-sample descriptive results; seeds are paired clusters and decisions are not independent samples.
Mock results verify the pipeline only; they do not show learning gains.
Each arm uses a fresh four-player team and plays both colors; all four evaluated players receive the same predeclared memory slot.
Memory is frozen during test matches; reflection, summary and persistent learning are disabled.
Team scores accumulate nonnegative duplicate points; diff_score is evaluated_score minus opponent_score after color rotation.
Win rate counts wins divided by all matches, with draws retained in the denominator.
Reliability, retries, fallback/autoplay, usage, latency and cost cover evaluated players only; baseline actions are excluded.
Unknown usage retains reserved cost; known token and cost totals are lower bounds when coverage is incomplete.
Source and artifact hashes detect drift; self-declared real provenance is not cryptographic provider attestation.
Incomplete audits invalidate study conclusions; retained rows are diagnostic evidence only.

审计问题：
无。
