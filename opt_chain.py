from futu import *
import pandas as pd

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", 200)

q = OpenQuoteContext(host="127.0.0.1", port=11111)

def chain(code, dates, spot, lo, hi):
    frames = []
    for d in dates:
        ret, data = q.get_option_chain(code, start=d, end=d, option_type=OptionType.PUT)
        print(f"\n-- {code} {d} ret={ret} n={0 if ret!=0 else len(data)} --")
        if ret != 0:
            print(data)
            continue
        if "strike_price" in data.columns:
            sub = data[(data["strike_price"] >= lo) & (data["strike_price"] <= hi)].copy()
        else:
            sub = data.copy()
            print("cols", list(data.columns))
        frames.append(sub)
        print("chain cols", list(data.columns))
        print(sub.head(3).to_string(index=False) if not sub.empty else "empty")
    if not frames:
        return
    allc = pd.concat(frames, ignore_index=True)
    codes = allc["code"].tolist() if "code" in allc.columns else []
    print(f"snapshots {len(codes)}")
    # batch snapshots 80 at a time
    snaps = []
    for i in range(0, len(codes), 80):
        chunk = codes[i:i+80]
        ret, s = q.get_market_snapshot(chunk)
        if ret == 0:
            snaps.append(s)
        else:
            print("snap fail", ret, s)
    if not snaps:
        return
    s = pd.concat(snaps, ignore_index=True)
    keep = [c for c in [
        "code","name","last_price","bid_price","ask_price","bid_vol","ask_vol",
        "volume","option_open_interest","option_implied_volatility",
        "option_delta","option_gamma","option_theta","option_vega",
        "strike_price","option_expiry_date_distance","option_type",
        "option_net_open_interest","turnover"
    ] if c in s.columns]
    # merge strike from chain
    if "strike_price" in allc.columns:
        s = s.merge(allc[["code","strike_price","strike_time"] if "strike_time" in allc.columns else ["code","strike_price"]], on="code", how="left", suffixes=("","_c"))
    cols = [c for c in [
        "code","strike_price","strike_time","last_price","bid_price","ask_price",
        "bid_vol","ask_vol","volume","option_open_interest",
        "option_implied_volatility","option_delta","option_theta","option_vega"
    ] if c in s.columns]
    s = s[cols].sort_values([c for c in ["strike_time","strike_price"] if c in s.columns])
    print(s.to_string(index=False))

dates = ["2026-09-18","2026-09-25","2026-10-16","2026-11-20"]
print("######## SNDK PUTS around 1524 ########")
chain("US.SNDK", dates, 1524, 1300, 1650)
print("\n######## SKHY PUTS around 160 ########")
chain("US.SKHY", dates, 160, 140, 175)
q.close()
