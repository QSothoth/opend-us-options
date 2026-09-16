# 本地 OpenD

`custody freeze` 和 `custody dryrun` 需要一个已登录、有美股期权行情权限的 Futu / moomoo OpenD：

- 行情端口默认 `127.0.0.1:11111`，可用环境变量 `FUTU_HOST` / `FUTU_PORT` 或命令行 `--host` / `--port` 覆盖；
- Python 依赖：`pip install futu-api`（评测和单元测试不需要）；
- 只使用 `OpenQuoteContext`。本仓库不允许出现 `OpenSecTradeContext`、`unlock_trade`、`place_order`、`modify_order`（测试静态扫描）。

配额提醒：

- 历史 K 线接口限速 60 次 / 30 秒，`freeze` 自带 50 次 / 30 秒的限速；
- 期权历史按 (标的, 到期日) 计入配额，过期周权的历史很快就取不到，所以每个交易日收盘后当天就要 `freeze`。

不要把 OpenD 程序、配置、登录凭据提交进 git。
