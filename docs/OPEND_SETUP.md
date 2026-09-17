# 本地 OpenD

`custody freeze`、`custody dryrun`、`custody run` 需要一个已登录、有美股期权行情权限的 Futu / moomoo OpenD：

- 端口默认 `127.0.0.1:11111`，可用环境变量 `FUTU_HOST` / `FUTU_PORT` 或命令行 `--host` / `--port` 覆盖；
- Python 依赖：`pip install futu-api`（评测和单元测试不需要）；
- `freeze` / `dryrun` 只用 `OpenQuoteContext`。只有 `custody run` 通过 `custody/broker.py` 使用 `OpenSecTradeContext` 下单，账户需要美股期权交易权限；其他模块不得出现 `OpenSecTradeContext`、`unlock_trade`、`place_order`、`modify_order`（测试静态扫描）。
- `run --mode paper` 用 OpenD 模拟账户，不需要解锁；`--mode live` 用真实账户，需要 `FUTU_TRADE_PASSWORD` / `FUTU_TRADE_PASSWORD_MD5` 环境变量或在 OpenD 界面解锁交易。账户 id 填 `--acc-id`，填错时报错信息会列出 OpenD 里的可用账户。

配额提醒：

- 历史 K 线接口限速 60 次 / 30 秒，`freeze` 自带 50 次 / 30 秒的限速；
- 期权历史按 (标的, 到期日) 计入配额，过期周权的历史很快就取不到，所以每个交易日收盘后当天就要 `freeze`。

不要把 OpenD 程序、配置、登录凭据和交易密码提交进 git。
