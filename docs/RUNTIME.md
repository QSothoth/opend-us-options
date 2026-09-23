# 运行时（RUNTIME）

实现：`custody/service.py`（状态机）、`custody/controller.py`（单步推进）、`custody/runner.py`（常驻进程与命令行）、
`custody/broker.py`（OpenD 下单）、`custody/opend.py`（只读行情）。

四种用法的行情来源和订单去向完全不同：

| 命令 | 行情 | 订单 | 用途 |
|---|---|---|---|
| `custody evaluate` | 已冻结的历史数据集 | 离线成交模型 | 评测策略，见 [STANDARD](STANDARD.md) |
| `custody dryrun` | OpenD 实时，只读 | 不发单，本地模拟成交 | 盘中观察信号 |
| `custody run --mode paper` | OpenD 实时 | 发到 OpenD 模拟账户（SIMULATE） | 演练真实下单流程 |
| `custody run --mode live` | OpenD 实时 | 发到真实账户（REAL），只接受 `accepted` 策略 | 实盘 |

## 1. 一个任务（job）

`dryrun` / `run` 启动时用命令行参数建任务：`--symbol`、`--direction`、`--contract` 必填，`--strategy` 缺省为默认策略，`--max-qty` 缺省 1，交易日是美东今天。

- 合约必须当天到期、与标的一致、LONG 对应 CALL / SHORT 对应 PUT。
- 同一账户 + 同一合约 + 同一交易日只能有一个任务：这张合约当天只买一次、卖一次。重跑同一条命令会接着处理同一个任务；同一合约换参数（数量、策略）会被拒绝。
- 同一标的可以同时有多个任务，例如一张 CALL 做多、一张 PUT 做空，各开一个进程，各自独立计时、各自一笔。
- 到达强平时刻就不能再建任务或买入。
- 任务、订单和模式（`dryrun` / `paper` / `live`，按账户绑定）保存在 SQLite，缺省为当前目录的 `custody-<模式>.sqlite`。放在持久目录，当天不要删除或换库，否则会丢失这张合约已经下过单的记录。数据库 schema 为 v4，旧库被拒绝，不自动迁移。

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

其他保证：意图先落库再做券商 I/O；提交结果不明记为 `UNKNOWN`，不会盲目重发；重启后任务和待发意图都保留；
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
python3 -m custody dryrun --symbol US.QQQ --direction LONG --contract "$contract"
```

可选参数：`--strategy <id>`、`--db <路径>`、`--intent-only`、`--once`。推送凭据用环境变量 `CUSTODY_WXPUSHER_SPT`。

- 没有券商对象：服务处于 `dryrun` 模式时 `dispatch_next` 直接拒绝。日志、推送、自动退出和 Ctrl-C 行为与 run 相同（见下节）。
- 默认把每个意图在本地按当时报价标记为**模拟成交**（日志事件 `simulated_fill`，`submitted=false`），这样能看到完整的入场、止损 / 跟踪、强平过程；`--intent-only` 则意图永远不会成交：未成交的买入意图按状态机超时撤销并重试，便于只看信号时点。

## 5. run

先在 OpenD 登录有美股期权交易权限的账户，并用 `--mode paper` 在模拟账户上完整跑通一天：

```bash
python3 -m custody run --mode paper --acc-id <模拟账户 acc_id> \
  --symbol US.QQQ --direction LONG --contract "$contract"
```

- `--acc-id` 必须是 OpenD 里对应环境（paper = SIMULATE，live = REAL）的美股账户，不匹配时报错并列出可用账户。券商主体用 `--security-firm`，缺省 `FUTUSECURITIES`（moomoo 美国为 `FUTUINC`）。
- live **必须**设置环境变量 `FUTU_TRADE_PASSWORD` 或 `FUTU_TRADE_PASSWORD_MD5`（缺省直接拒绝启动）。不要用 `env -u` 剥掉密码后依赖 GUI 解锁：GUI 解锁会过期，`place_order` 随后失败。密码不走命令行参数。
- 订单是美股限价单（`NORMAL`、当日有效、只在常规时段），买入价 = ask，卖出价 = bid。每单的 `remark` 写入客户端订单号；订单号由账户、合约和交易日确定，提交前先在 OpenD 订单列表里查找，已有就沿用，所以超时、重启或换库后同一订单号都不会重复下单。
- 卖出前查询持仓，只卖账户实际持有的多头数量，不会卖出开仓。持仓查询失败时本次卖单记为被拒、下一轮重试；持仓少于要卖的数量时不发单、标记 `RECONCILE_ORDER_STATUS`，按下一条的 2 分钟规则再试。
- 每次 live 提交前会 best-effort 调用 `unlock_trade`；若失败信息像解锁相关且环境变量有密码，再解锁并重试一次 `place_order`。
- `place_order` **明确被拒**（OpenD 返回错误且订单列表无该 client id，含解锁失败）→ 立即 `REJECTED`，attention 为 `ENTRY_ORDER_REJECTED` / `EXIT_ORDER_REJECTED` / `TRADE_UNLOCK_REQUIRED`，并写入 `last_error`；下一笔用**新的** client order id。
- `place_order` **结果不明**（超时等，可能已进簿）→ 订单记为 `UNKNOWN`，attention `RECONCILE_ORDER_STATUS`，同样记录完整错误；不重发同一 id。之后每轮按客户端订单号查找。找到就按真实状态继续；意图创建 2 分钟后刷新订单列表仍找不到，才判定没有下单（`REJECTED`），允许重新入场或重新卖出。
- 订单状态靠每轮轮询 OpenD 订单列表获得。成交时间和入场时的正股价格按发现成交的那一轮记录，最多晚一个轮询间隔（缺省 5 秒）。
- 每次订单状态或成交数量变化记日志 `order`；配置了 WxPusher SPT 时推送到手机（失败不影响循环）。行情或 K 线暂时缺失只记日志（`quote_error` / `frame_error`），循环继续。
- 任务结束（`DONE`）后进程自动退出。Ctrl-C 只停止轮询：OpenD 上的挂单和持仓保持不变，日志 `stopped` 列出未完成订单和持仓；重跑同一条命令即可接着管理。
- 强平时刻取自 OpenD 交易日历：不是全天交易（`WHOLE`）的日子按 13:00 收盘计算。

## 6. 查看与人工停止

```bash
python3 -m custody status --db custody-paper.sqlite                  # 全部任务概要
python3 -m custody status --db custody-paper.sqlite --job <job_id>   # 单个任务，含订单
python3 -m custody stop   --db custody-paper.sqlite --job <job_id>   # 人工停止
```

`stop` 只在数据库里请求退出（撤掉未成交的买单，再卖出持仓，不会假装已平仓），由正在运行的 `dryrun` / `run` 进程在下一轮执行。进程没有运行时，重跑同一条启动命令来执行退出。

## 7. 执行边界

- 只有 `custody/broker.py` 使用 OpenD 交易接口；其他模块不得引用 `OpenSecTradeContext`、`place_order`、`unlock_trade`、`modify_order`（测试扫描）。
- `dryrun` 模式的服务拒绝发单，控制器也不带券商对象；模拟成交只允许在 dryrun 使用。
- `live` 只接受状态为 `accepted` 的策略（见 [STANDARD](STANDARD.md)）。
