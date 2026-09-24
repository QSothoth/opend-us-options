# custody：美股期权日内单笔交易（0DTE）

> 全仓库规则见 [../AGENTS.md](../AGENTS.md)。

## 边界

上游选好标的、方向和合约；本项目只为这张当天到期的期权做日内择时，放大盈亏比：方向对时拿住涨幅，方向错时尽早止损。

- 一个 case = `symbol + contract + trade_date`，最多一笔：有信号才买，收盘前卖；无信号可以不交易，不为完成率兜底。部分成交也算这一笔。
- 永远是买方：LONG 买 CALL，SHORT 买 PUT；不选标的 / 方向 / 合约，不卖出开仓；同一标的不同合约互相独立。
- 策略输入只有：当天已完成的正股 1m K 线、方向、行权价、开收盘时间。禁止期权价格、前日及更早数据、日线、场景标签、未完成或未来的 bar、随机数、跨实例状态。
- 择时线（`zero_dte_timing`）入场不依赖固定时刻或价格；收盘前 15 分钟（`flatten_before_close_minutes`）必须退出、禁止买入。
- **保证入场线**（`open_hold_*`，2026-09-20 用户决定）：开盘 1 小时内必须买入一单，时刻与方式可调（固定时刻 `open_hold`，或择时 + `must_enter_by_minute` 兜底）。不适用「无信号可以不交易」，也不按「优于对照组」的门槛（G3 / G5 / G7–G10 / G12）判定，看自身盈亏平衡命中率（[STANDARD](../docs/STANDARD.md)）。
- 盈亏只按期权成交价算；禁止用正股收益折算、删除失败 case、看完结果改标准。

## 数据

- 只用 [DATA](../docs/DATA.md) 登记的数据集；每个 case 是真 0DTE（expiry = trade_date），有同日完整常规时段的正股 1m 和期权 1m，文件由 SHA256 固定，不一致直接报错。
- 新数据集每个（标的, 交易日）必须同时有 **CALL 和 PUT**（同一行权价：开盘第一根 1m 开盘价最近的挂牌行权价），不按收益、成交量或事后标签挑方向。只有单边的数据集只能做诊断，结论最高 PROVISIONAL。
- 每个交易日收盘后当天 `custody freeze`（过期周权补不回来）；发布 Release 前必须通过 `python3 -m custody check`。

## 策略与评测

- 策略 = `engines/<engine>.py` + 不可变参数文件。改参数用新 `strategy_id` 和新文件，在 [strategies/index.json](strategies/index.json) 登记 SHA256；小改动升小版本（v6 → v6.1）。
- 状态只写在 `index.json`，只有评测 ACCEPT 才能改 `accepted`。不留废弃策略、旧报告或兼容分支；历史查 Git，不复活已删除的策略。
- 候选用完整标准（含 G8–G10）评测：先比未通过门槛数，再比综合分（判为优于基线还要求上游命中率 60% 时综合分也更高）；选择做留一天交叉验证并报告样本外估计；全部候选与失败记入 [STRATEGY](../docs/STRATEGY.md)。
- 唯一评测入口：`python3 -m custody evaluate --strategy <id> --dataset <目录> --out reports/<id>/<dataset>/`。主口径方向对称加权、主指标盈亏比；结论只看门槛；比较版本用综合分（收益分 80%、完成分 20%，见 [STANDARD](../docs/STANDARD.md) 第 10 节）；整体比较保留全部 case，未入场按 0 收益计。
- 两边不全、场景不足或样本外交易日不足 20 个都不能 ACCEPT。

## 运行

`custody dryrun` 只读、只记意图和模拟成交；`custody run` 下单（`paper` 模拟、`live` 真钱且只接受 `accepted` 策略）。细节见 [RUNTIME](../docs/RUNTIME.md)。

## 修改

- `futu-api` 只在 freeze / dryrun / run 需要。
- 改策略行为或参数：重新生成受影响的报告一起提交。改评测口径（常量、成交模型、标签、权重、门槛）：同步 `evaluate.py`、[STANDARD](../docs/STANDARD.md) 和测试，重跑所有已注册策略的报告。只改文案不重跑。
