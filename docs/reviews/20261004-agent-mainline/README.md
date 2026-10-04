# Agent 主线验收证据（2026-10-04）

结论及范围见[主线复核与实现](../../agent-mainline-review-20261004.md)。用户选择保留共享首次响应的现有实验，本轮仅补实际可靠性缺口；未调用真实模型。

- [validation-summary.json](validation-summary.json)：验收结果与限制，后端 651 项、前端 11 项全部通过。
- [environment.json](environment.json)、[source-file-sha256.json](source-file-sha256.json)、[source-snapshot.json](source-snapshot.json)：当前未提交工作区的环境、源码哈希及可复查文本快照。快照未包含凭据或运行数据库。
- [backend.xml](backend.xml)、[backend.log](backend.log)、[execution-faults.xml](execution-faults.xml)：完整离线回归及 7 项执行故障专项。专项已包含在全量中，不另加总。
- [frontend-test.log](frontend-test.log)、[frontend-build.log](frontend-build.log)、[browser.json](browser.json)：前端与双观众、断网恢复、记忆编辑验收。
- [deployment-before-restart.json](deployment-before-restart.json)、[deployment-after-restart.json](deployment-after-restart.json)：Nginx 入口和已完成实验的重启一致性。
- [deployment-active-before.json](deployment-active-before.json)、[deployment-active-after.json](deployment-active-after.json)：活动比赛重启、锁释放及下一场结算。
- [fixed-mock-report](fixed-mock-report/)：8 局面、29 次合成调用，8 份报告离线重建逐字节相同；合成结果不构成模型效果证据。
- [SHA256SUMS.json](SHA256SUMS.json)：本目录归档文件摘要。

验收使用独立 Compose 项目 `doudizhu-agent-mainline-20261004`、38080/38000 端口、空密钥与隔离数据目录；测试容器已停止。完整截图、trace 和隔离数据库位于项目的 `data/agent-mainline-20261004/`。没有改动历史真实实验的数据库或报告。

无需 API Key 的固定观察演示（输出目录须尚不存在）：

```bash
conda run --no-capture-output -n doudizhu-arena python -I -B scripts/fixed_effect_study.py prepare \
  --output data/agent-demo-plan-new --development-seeds 1 --test-seeds 1 \
  --seed-namespace agent-demo-20261004
conda run --no-capture-output -n doudizhu-arena python -I -B scripts/fixed_effect_study.py run \
  --plan data/agent-demo-plan-new --output data/agent-demo-run-new \
  --model minimax --split development --mock
conda run --no-capture-output -n doudizhu-arena python -I -B scripts/fixed_effect_study.py audit \
  --run-dir data/agent-demo-run-new/minimax --output data/agent-demo-audit-new
```

源码后续变动会使旧运行的当前源码核验失败；这属于预期边界，应使用对应快照复查。默认 prepare 的旧种子用于复现，新命名空间也不自动保证未曾参与开发。正式模型执行前仍需核对当次模型、账户价格与预算。
