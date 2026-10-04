# 历史复现与性能原始证据

方法、复现命令、测量环境与解释边界见[复现与性能记录](../../reproduction-and-performance-20261004.md)。

- `historical-reproduction.json`：使用对应历史源码重建 16 份报告，全部逐字节一致；Qwen 原协议失败保留，源数据库未改变。
- `historical-reproduction-tests.xml`：12 项复现专项通过。
- `algorithm-benchmark.json`：19 个场景、1,900 次算法测量，含逐次未舍入样本、输入／输出哈希与可重算的分位数。
- `realtime-result.json`：采样器 v2 的最终五观众／二十次重连／背压测试结果，包含源码哈希、浏览器参数、校时误差、资源阶段、采样开销和实际间隔。
- `realtime-verification.json`：独立读取浏览器、SQLite 和资源原始数据的复算结果。
- `realtime-*.json.gz`：完整浏览器原始与已接收事件、服务端发布与快照、延迟和资源样本；启动、正常、压力及收尾阶段均保留。
- `realtime-realtime.db.gz`：本轮独立 mock 数据库；SQLite 完整性检查通过，凭据为空。所有采样文件都是合成对局的真实运行观测。
- `raw-files.json`：压缩文件解压后的字节数与 SHA-256，支持验证无损归档。
- `realtime-initial-instrumentation.json`：旧采样器完整运行的诊断记录；因全机采样开销过高而替换，不纳入最终性能统计。
- `source-file-sha256.json`：本轮源码、测试、脚本及构建输入的文件摘要。
- `SHA256SUMS.json`：本目录归档文件摘要，不包含其自身。

正常发布至回调 P95 为 7.039 ms；恢复 P95 为 1,011.500 ms，其中含默认的 1 秒重连等待。20 次恢复全部发生在 running 状态且确有遗漏自然事件；5 页最终完整快照一致。慢订阅真实触发 10,000 容量边界，5 个正常观众完整收到全部 10,001 个持久化探针。

实时测试使用独立 loopback FastAPI/Uvicorn 与真实 Chromium、生产 socket 客户端，决策模型为 mock；不覆盖 Vue DOM 渲染、真实模型等待、真实慢网络或生产并发容量。CPU 100% 表示一个逻辑核心；浏览器 RSS 为含共享页面重复计算的进程树总和。最终采样器仍有自身开销，已独立披露。

原始未压缩数据保留在 `data/reproduction-performance-20261004/realtime-final/`。历史恢复源码和两模型完整重建报告保留在 `data/fixed-reproduction-20261004/`，与既有公开报告相同，不重复归档。
