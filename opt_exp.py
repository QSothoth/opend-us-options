from futu import *

q = OpenQuoteContext(host="127.0.0.1", port=11111)

for code in ["US.SNDK", "US.SKHY"]:
    print("\n########", code, "EXPIRATIONS ########")
    ret, data = q.get_option_expiration_date(code)
    print(ret)
    if ret == 0:
        print(data.to_string(index=False))
    else:
        print(data)

# kline daily last 90 days
print("\n######## KLINES ########")
for code in ["US.SNDK", "US.SKHY"]:
    ret, data, page = q.request_history_kline(code, start="2026-06-01", end="2026-09-02", ktype=KLType.K_DAY, max_count=1000)
    print("\n===", code, ret, "rows", 0 if ret!=0 else len(data), "===")
    if ret == 0 and not data.empty:
        # last 15 + high/low
        print("max close", data["close"].max(), "on", data.loc[data["close"].idxmax(), "time_key"])
        print("min close", data["close"].min(), "on", data.loc[data["close"].idxmin(), "time_key"])
        print("max high", data["high"].max(), "on", data.loc[data["high"].idxmax(), "time_key"])
        print(data[["time_key","open","high","low","close","volume"]].tail(20).to_string(index=False))
    else:
        print(data)

q.close()
