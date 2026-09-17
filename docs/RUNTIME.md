# 运行时（RUNTIME）

实现：`custody/service.py`（状态机）、`custody/controller.py`（单步推进）、`custody/http.py`（控制 API）、
`custody/dryrun.py`（只读 OpenD 常驻观察）、`custody/opend.py`（只读行情适配器）。

本仓库**没有真实券商下单连接**，也不允许加入（[项目规范](../AGENTS.md#运行与安全)）。

## 1. 一个任务（job）

请求（`POST /v1/jobs` 或 `CustodyService.create_job`，JSON Schema 见 `custody/job_request.schema.json`）：

```json
{"strategy_id": "zero_dte_timing_v4", "symbol": "SPY", "direction": "LONG", "contract": "US.SPY260916C600000", "max_qty": 1}
```

- 必填前四项；`max_qty` 默认 1；`trade_date` 默认美东今天，且必须是今天。
- 合约必须当天到期、与标的一致、LONG 对应 CALL / SHORT 对应 PUT。
- 同一账户 + 同一合约 + 同一交易日只能有一个任务：这张合约当天只买一次、卖一次。完全相同的请求幂等返回同一任务；同一合约换参数（数量、策略）会被拒绝。
- 同一标的可以同时有多个任务，例如一张 CALL 做多、一张 PUT 做空，各自独立计时、各自一笔。
- 到达强平时刻就不能再建任务或买入。
- `live` 模式只接受状态为 `accepted` 的策略。
- 模式（`paper` / `live` / `dryrun`）绑定在数据库上，不能由请求切换。

## 2. 状态机

`IDLE → WATCH → ENTRY → IN → EXIT → DONE`

| 事件 | 处理 |
|---|---|
| 策略帧 `ENTER` | 报价新鲜且点差 ≤ 30% 时生成买入限价意图（价格 = ask） |
| 全天没有入场信号 | 保持空仓；强平时刻结束任务并标记 `NO_ENTRY_SIGNAL`，不兜底买入 |
| 买单 30 秒未成交 | 未发出的本地撤销；已发出的发撤单，等券商终态；无成交则回到 WATCH 重试（`ENTRY_RETRY_PENDING`） |
| 买单被拒 / 撤销且无成交 | 强平时刻之前回到 WATCH 重试；之后结束并标记 `ENTRY_NOT_FILLED` |
| 部分成交 | 仍是当天唯一一笔，不会再开新的完整买单 |
| 策略帧 `EXIT` / 到强平时刻 / 人工停止 | 先撤掉仍在挂的买单并等待终态，再按持仓数量卖出（价格 = bid，只减仓） |
| 卖单 30 秒未成交 | 发撤单改价，撤单确认后只对剩余数量重新卖出 |
| 没有有效报价 | 停在 EXIT 并标记 `EXIT_WAITING_VALID_QUOTE`，不会假装已平仓 |

其他保证：意图先落库再做券商 I/O；提交超时记为 `UNKNOWN`，不会盲目重发；重启后任务和待发意图都保留；
策略帧必须是当天已完成的整分钟 K 线、不超过 15 秒、不能倒序。

默认执行参数（`ExecutionPolicy`）：报价最长 5 秒、帧最长 15 秒、入场 / 出场超时 30 秒、最大相对点差 30%。
这些是工程默认值，没有用历史买卖价校准过。

## 3. 策略帧从哪来

`StrategyFrameSource` 在每个整分钟：取当天开盘到现在的全部正股 1m K 线 → 从头重建策略引擎 →
在第一根收盘时间 ≥ 实际入场成交时间的 K 线之前通知入场成交 → 取最后一根 K 线的决策作为帧。
因此重启后结果不变，并且和 `custody evaluate` 的离线回放是同一段代码。

## 4. dryrun

先按 [OpenD 环境](OPEND_SETUP.md) 配置行情。将 `contract` 替换成上游选好的**今天到期**的实际合约代码：

```bash
contract='<今天到期的 CALL 合约代码>'
python3 -m custody dryrun --symbol US.QQQ --direction LONG --contract "$contract" \
  --db /tmp/custody-dryrun.sqlite
```

可选参数：`--strategy <id>`、`--intent-only`、`--once`。推送凭据用环境变量 `CUSTODY_WXPUSHER_SPT`。

- 只用 `OpenQuoteContext`；服务处于 `dryrun` 模式时 `dispatch_next` 直接拒绝，控制器也没有券商对象。
- 默认把每个意图在本地按当时报价标记为**模拟成交**（日志事件 `simulated_fill`，`submitted=false`），这样能看到完整的入场、止损 / 跟踪、强平过程；`--intent-only` 则意图永远不会成交：未成交的买入意图按状态机超时撤销并重试，便于只看信号时点。
- 每个新意图记日志 `order_intent`；配置了 WxPusher SPT 时推送到手机（失败不影响循环）。
- 行情或 K 线暂时缺失只记日志（`frame_error` / `quote_error`），循环继续。
- 数据库 schema 为 v4；旧库被拒绝，不自动迁移。旧任务先结束、旧库保留审计，再用新的 `--db` 路径启动，不能切库重复处理同一合约当天任务。

## 5. 控制 API

`custody.http.create_app(service, token)` 返回一个 WSGI 应用（导入时不启动监听）：

- `POST /v1/jobs` 建任务；`GET /v1/jobs/{id}` 查状态；`POST /v1/jobs/{id}/stop` 人工停止（撤单或平仓，不会假装已平）；`GET /v1/strategies` 列出策略。
- 全部需要 `Authorization: Bearer <token>`（至少 20 个字符）；未知字段、重复字段一律拒绝。

## 6. 执行接口的边界

`custody/ports.py` 的 `Broker` 是状态机与测试替身使用的执行契约；上文的提交、撤单和对账描述该契约，不代表已连接券商。
`paper` / `live` 是现有服务的模式和校验分支，不能据此启动真实交易。仓库禁止加入真实券商适配器。
