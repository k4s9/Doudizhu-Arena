# 记忆对照评测

完整对局的差分分、胜率和固定随机对手对照另见[完整对局记忆对照](memory-full-matches.md)。以下固定局面协议用于独立测量输出合法性与成本，两类结果分别报告。

这是独立于首轮规则反馈实验的固定局面协议。它在完全相同的可见局面上比较三组：

| 组别 | 追加到公共提示词的内容 |
| --- | --- |
| `no_memory` | 不追加策略记忆 |
| `fixed_initial` | 实验前声明的固定初始策略文本 |
| `frozen_memory` | 训练产物中预先选定的一个牌手槽位，默认 `player-0` |

每组独立调用模型，使用相同的模型参数、通用重试规则、调用次数上限和输入/输出上限。首答合法时提前结束该决策，因此实际调用次数可能不同。组别顺序按固定 seed 打乱。记忆只读，不执行反思、总结、更新或对局；因此报告**没有胜率、对局得分或棋力提升结论**。不得根据测试结果更换槽位、记忆或提示词。

## 无密钥验收

在项目根目录执行，输出目录必须尚不存在：

```bash
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/memory_effect_study.py prepare --mock --output /tmp/ddz-memory-plan
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/memory_effect_study.py run --mock --plan /tmp/ddz-memory-plan --output /tmp/ddz-memory-run
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/memory_effect_study.py audit --run-dir /tmp/ddz-memory-run --output /tmp/ddz-memory-audit
```

默认使用 2 个测试 seed、每个 seed 8 个局面，共 48 个独立决策。mock 对三组使用完全相同的合成行为：叫分首答合法，出牌首答失败、通用重试后合法，共 90 次物理调用。合成记忆明确标注为 fixture，**不是训练结果；mock 通过只说明流程可用，不证明记忆收益**。

`run/report/` 包含 `report.md`、`summary.json`、逐决策证据索引和 `paired-seed.csv`。数据库保留完整提示词、原始输出、调用参数关联、token/延迟及预算账本。`audit` 只读数据库，重新解析原始输出和重建各组提示词，核验结果、重试次数及调用/账本一对一关联，不仅检查 JSON 是否存在。

## 使用已有训练产物

训练继续使用 `arena.evaluation.train_memory` 的顺序训练与 checkpoint；新协议没有另造一套训练器。冻结产物需包含 `schema_version: 1`、显式 `provenance`（`real` 或 `mock`）、`artifact_sha256`、训练 seeds 及 seed-set 哈希、`model_snapshot`、训练 run/plan 标识、源码版本及内容哈希、依赖指纹和 `memories`。真实评测拒绝 mock 或未声明来源的旧产物；模型 provider/name/parameters/base_url 必须与训练产物一致，还检查训练 runtime 配置与模型快照、提示词哈希一致，且包含全部八个牌手槽位和训练副数。训练与测试 seed 必须严格不交叉。哈希用于发现内容改变，来源字段是可追踪声明，不是外部训练过程的独立认证；应一起保留训练数据库。

训练完成一项任务与保存八个牌手槽位的 checkpoint 使用同一事务；恢复时检查完成任务是计划中的连续前缀，且与 checkpoint 对应。checkpoint 写失败不会留下“任务已完成但记忆未保存”的状态。恢复直接读取数据库保存的完整原始 manifest，核验源码、依赖、SDK、spec、种子文件和训练顺序；模型行为配置被冻结，允许更新凭据。缺少完整 manifest 的旧 run 不能冒充可重现恢复。已存在的产物在执行前及文件创建时都拒绝覆盖，导出成功后才将训练 run 标记为完成。

真实训练必须声明 USD 定价及来源/日期，`budget_limit_usd`、`max_calls`、`max_input_tokens`、模型 `max_tokens` 对叫分、出牌、反思和总结共用一个持久化预算账本。恢复训练仍计入之前失败任务的调用和未知用量预留；预算耗尽不会导出“训练完成”产物。`task_timeout_seconds` 限制整项训练任务，`cancellation_deadline_seconds` 后撤销任务写入权限，迟到结果不能追加调用证据或提交 checkpoint；未知用量保留预留额度。若完整训练超出预声明预算，应建立新实验，不能在恢复时改变原 spec。

准备以下文件后才能进行真实评测：

- `model.json`：现有 `ModelSpec` 格式，包含 `config_name`、`provider`、`model`、显式 `base_url`、`parameters`（如 `max_tokens`、`temperature`、`top_p`、`seed`）和带来源/生效日期的 USD `pricing`。不得包含密钥；`system_prompt` 留空，公共提示词来自冻结 corpus。
- `initial-strategy.txt`：实验前声明的固定初始策略；应说明与训练初始配置的关系，不从测试结果提炼。
- 已有冻结训练产物，以及可选的测试 seed JSON（`{"seeds":["held-out-001", "held-out-002"]}`）。默认 seed 固定，不按模型输出筛选。

```bash
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/memory_effect_study.py prepare --model-json /tmp/model.json --memory-artifact /tmp/trained-memory.json --initial-strategy /tmp/initial-strategy.txt --budget-usd 5 --output /tmp/ddz-memory-real-plan
```

`prepare` 不调用模型。它冻结 corpus、模型参数、策略、记忆、源码/依赖/SDK 指纹以及提示词哈希，并检查所有重试输入长度。预算必须覆盖预声明的最坏调用上限：局面数 × 3 组 ×（1 + 重试次数）；每次按完整输入/输出配额保守预留。免费价格也必须提供来源与生效日期。

实际调用必须另行显式执行 `run --real-models`，密钥从 `DDZ_MEMORY_EVAL_API_KEY`（或 `--api-key-env` 指定变量）读取，不写入实验配置或数据库：

```bash
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/memory_effect_study.py run --real-models --plan /tmp/ddz-memory-real-plan --output /tmp/ddz-memory-real-run
```

缺少密钥、模式不符、训练/测试 seed 重叠、产物被改动、输入超限或预算不足都会在调用前拒绝。源码/依赖发生变化也必须重新 prepare；已失败的评测保留证据，新实验使用新目录，不续跑或覆盖旧结果。

## 指标含义

首答/最终合法率分别使用首个输出及重试链最后一个输出；按全部局面、叫分和出牌分别统计。报告还包括平均重试、需要兜底率、已知 token、未知用量次数、模型调用延迟以及每组的已知费用 `known_cost_usd` 和保守预算占用 `accounted_cost_usd`。**“需要兜底”表示重试耗尽，不是实际执行了兜底；实际兜底动作始终为 0。**未知用量不按零成本记账，保留预留额度；CSV 与逐 seed 对照同时提供成本，审计核验实际调用顺序符合冻结的组别顺序。

`paired-seed.csv` 将相同 seed 的三组结果并排提供；JSON 中附冻结记忆相对两组对照的逐 seed 合法率差值。这是描述性比较，不把同局面内的多个决策当成独立对局样本，也不做显著性或因果宣称。一次测试仅使用预声明槽位，规则策略生成的局面不代表人类真实打法。即使参数固定，真实服务端随机性仍可能导致输出无法逐字复现。

针对该流程的离线测试：

```bash
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/test_offline.py tests/test_memory_effect_study.py tests/test_memory_training.py
```
