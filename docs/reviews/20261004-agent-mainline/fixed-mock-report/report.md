# 固定局面规则反馈效果实验：fixed-study-mock

完整性审计：通过。
审计源码与冻结版本：一致。
实际调用 29 次；usage 已知 29/29。

三协议共享首答；仅失败时分别执行最多两次通用重试和规则反馈重试。

| 阶段 | 协议 | 成功/样本 | 首答非法后恢复 | 逻辑调用 | 平均调用耗时合计 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| all | single_generation | 1/8 | 0/7 | 8 | 0.9 |
| all | generic_retry | 1/8 | 0/7 | 22 | 2.6 |
| all | rule_feedback | 8/8 | 7/7 | 15 | 1.8 |
| bidding | single_generation | 1/1 | 0/0 | 1 | 0.0 |
| bidding | generic_retry | 1/1 | 0/0 | 1 | 0.0 |
| bidding | rule_feedback | 1/1 | 0/0 | 1 | 0.0 |
| playing | single_generation | 0/7 | 0/7 | 7 | 1.0 |
| playing | generic_retry | 0/7 | 0/7 | 21 | 3.0 |
| playing | rule_feedback | 7/7 | 7/7 | 14 | 2.0 |

逻辑调用包含各协议引用的共同首答，不能相加当成实际消费。零分母表示 N/A。

主指标（出牌）：规则反馈减通用重试为 **100.000 个百分点**，
按 1 个独立来源种子配对 bootstrap 的 95% 区间为 **[100.000, 100.000]**。
区间跨过零时，本次样本不足以确认稳定的增益或劣化。

总体区间描述本次请求模型别名/端点的实际返回组合，不能当作某个固定权重版本的效果。

返回身份分组只描述元数据一致的配对子样本；这些分组是在观察响应之后形成的，不另作因果或显著性结论。

| 返回模型名（出牌） | 指纹 | 元数据范围 | 配对数 | 来源 seed 数 | 成功率差 pp（描述性） |
| --- | --- | --- | ---: | ---: | ---: |
| fixed-study-mock | 未提供 | name_only_fingerprint_unavailable | 7 | 1 | 100.000 |

因返回名称/指纹缺失或变化而未进入上述分组的出牌配对：{}。

## 解释边界

- Frozen deterministic rule-policy states with fixed category quotas; not representative human play or competitive strength.
- Shared first responses correlate protocols deliberately; do not count three independent baseline samples or sum logical calls as actual expenditure.
- One response realization per state/model; inference clusters by source seed. It does not cover all provider-time/version variability.
- Call-time estimates exclude queue/branch scheduling; they are not measured live-game end-to-end latency.
- Requested aliases may resolve to different returned names; names/fingerprints cannot prove weight-version identity.
- The overall interval describes the requested alias/endpoint's observed response mixture, not a fixed model revision. Metadata groups are post-observation descriptive subsets, not separate causal estimates.
- Single-returned-name pairs require a name on every physical call. Identity groups also exclude changed or partially missing fingerprints; all fingerprints absent is explicitly name-only evidence.
- Zero prices follow the previously user-confirmed free account, not an audited provider bill; unknown usage remains unknown.
- A degenerate zero bootstrap interval does not establish equivalence or absence of rare failures.
- The validator and source replay reuse the project engine; this is not an independent third-party rules audit.
