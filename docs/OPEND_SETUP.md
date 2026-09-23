# 本地 OpenD

`custody freeze`、`custody dryrun`、`custody run` 需要一个已登录、有美股期权行情权限的 Futu / moomoo OpenD：

无桌面 Linux 用**命令行 OpenD**（`FutuOpenD`），不要装 GUI AppImage。

- 程序目录 `/opt/futu-opend`，配置 `/opt/futu-opend/FutuOpenD.xml`，入口命令 `FutuOpenD`
- API 默认 `127.0.0.1:11111`，telnet 验证码口 `127.0.0.1:22222`
- 启动后走交互登录；首次通常要手机验证码，用 `input_phone_verify_code -code=......` 打进 telnet
- 不要把账号密码写进 xml 或 git；记住密码后下次可用 `FutuOpenD -login_account=账号 -login_by_remember=1`
- Python：`/opt/futu-opend/venv/bin/python`（已装 `futu-api`）；评测和单元测试不需要。`custody freeze` / `dryrun` / `run` 用这个解释器。端口也可用 `FUTU_HOST` / `FUTU_PORT` 或 `--host` / `--port` 覆盖。

- `freeze` / `dryrun` 只用 `OpenQuoteContext`。只有 `custody run` 通过 `custody/broker.py` 使用 `OpenSecTradeContext` 下单，账户需要美股期权交易权限；其他模块不得出现 `OpenSecTradeContext`、`unlock_trade`、`place_order`、`modify_order`（测试静态扫描）。
- `run --mode paper` 用 OpenD 模拟账户，不需要解锁；`--mode live` 用真实账户，**必须**提供 `FUTU_TRADE_PASSWORD` / `FUTU_TRADE_PASSWORD_MD5`（启动时校验；进程内在每次下单前会再 unlock，解锁相关失败会重试一次）。不要依赖 GUI 解锁长期有效。账户 id 填 `--acc-id`，填错时报错信息会列出 OpenD 里的可用账户。

配额提醒：

- 历史 K 线接口限速 60 次 / 30 秒，`freeze` 自带 50 次 / 30 秒的限速；
- 期权历史按 (标的, 到期日) 计入配额，过期周权的历史很快就取不到，所以每个交易日收盘后当天就要 `freeze`。

不要把 OpenD 程序、配置、登录凭据和交易密码提交进 git。
