from futu import *
import pandas as pd
from datetime import datetime, timezone, timedelta

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", 300)
pd.set_option("display.max_colwidth", 80)

ACC = 281756478582875671
HOST, PORT = "127.0.0.1", 11111

print("==== TS", datetime.now(timezone(timedelta(hours=8))).isoformat(), "CST ====")

q = OpenQuoteContext(host=HOST, port=PORT)
print("\n=== 1. GLOBAL STATE ===")
ret, state = q.get_global_state()
print("ret", ret)
print(state)

print("\n=== 2. SNAPSHOT US.SNXX US.SNDK ===")
ret, s = q.get_market_snapshot(["US.SNXX", "US.SNDK"])
print("ret", ret)
if ret == 0:
    cols = [c for c in [
        "code","name","stock_owner","last_price","open_price","high_price","low_price",
        "prev_close_price","bid_price","ask_price","bid_vol","ask_vol","volume","turnover",
        "suspension","listing_date","stock_type","option_type","strike_price",
        "option_open_interest","price_spread"
    ] if c in s.columns]
    print(s[cols].to_string(index=False))
    print("\nall snapshot cols:", list(s.columns))
    for _, r in s.iterrows():
        print(f"NAME {r['code']}: {r.get('name')}")
else:
    print(s)

print("\n=== 3. SNXX EXPIRATIONS ===")
ret, exp = q.get_option_expiration_date("US.SNXX")
print("ret", ret)
if ret == 0:
    print(exp.to_string(index=False))
    dates = [str(x)[:10] for x in exp["strike_time"].tolist()] if "strike_time" in exp.columns else []
    print("dates", dates)
    target = "2026-09-18"
    if target in dates:
        expiry = target
        print("FOUND target", expiry)
    else:
        # nearest
        from datetime import date
        td = date.fromisoformat(target)
        nearest = min(dates, key=lambda d: abs((date.fromisoformat(d)-td).days)) if dates else None
        expiry = nearest
        print("NEAREST to 2026-09-18:", expiry)
else:
    print(exp)
    expiry = "2026-09-18"

print("\n=== 4. SNXX PUT CHAIN", expiry, "===")
ret, chain = q.get_option_chain("US.SNXX", start=expiry, end=expiry, option_type=OptionType.PUT)
print("ret", ret, "n=", 0 if ret != 0 else len(chain))
if ret != 0:
    print(chain)
    chain = pd.DataFrame()
else:
    print("chain cols", list(chain.columns))
    if "strike_price" in chain.columns:
        print("strikes", sorted(chain["strike_price"].unique().tolist()))
        sub = chain[(chain["strike_price"] >= 8) & (chain["strike_price"] <= 18)].copy()
        print("nearby 8-18:\n", sub.to_string(index=False))
    else:
        print(chain.head(20).to_string(index=False))
        sub = chain.copy()

    codes = chain["code"].tolist() if "code" in chain.columns else []
    print("n codes", len(codes))
    snaps = []
    for i in range(0, len(codes), 80):
        r2, snap = q.get_market_snapshot(codes[i:i+80])
        print("snap chunk", i, r2, 0 if r2 != 0 else len(snap))
        if r2 == 0:
            snaps.append(snap)
        else:
            print(snap)
    if snaps:
        snap = pd.concat(snaps, ignore_index=True)
        merge_cols = [c for c in ["code","strike_price","strike_time","option_type"] if c in chain.columns]
        snap = snap.merge(chain[merge_cols], on="code", how="left", suffixes=("","_c"))
        keep = [c for c in [
            "code","name","strike_price","strike_time","last_price","bid_price","ask_price",
            "bid_vol","ask_vol","volume","option_open_interest","option_implied_volatility",
            "option_delta","option_gamma","option_theta","option_vega","option_type",
            "option_net_open_interest","turnover"
        ] if c in snap.columns]
        snap = snap[keep].sort_values([c for c in ["strike_price"] if c in snap.columns])
        print("\n=== FULL PUT SNAPS ===")
        print(snap.to_string(index=False))

        print("\n=== 12.00 PUT + NEARBY 10/11/12/13/14 ===")
        want = {10.0, 11.0, 12.0, 13.0, 14.0}
        for _, r in snap.iterrows():
            sp = r.get("strike_price")
            if pd.isna(sp):
                continue
            if float(sp) not in want and abs(float(sp)-12.0) > 0.01:
                # still print 12 specifically later
                pass
            if float(sp) in want:
                bid, ask, last = r.get("bid_price"), r.get("ask_price"), r.get("last_price")
                try:
                    spread = (ask - bid) if pd.notna(bid) and pd.notna(ask) else None
                except Exception:
                    spread = None
                mid = None
                if pd.notna(bid) and pd.notna(ask) and bid and ask:
                    mid = (bid+ask)/2
                quote = mid if mid and mid > 0 else last
                flags = []
                if spread is not None and spread > 0.10:
                    flags.append("SPREAD>0.10")
                if quote is not None and pd.notna(quote) and quote < 0.15:
                    flags.append("QUOTE<0.15")
                print(
                    f"STRIKE={sp} CODE={r['code']} last={last} bid={bid} ask={ask} "
                    f"spread={spread} mid={mid} vol={r.get('volume')} OI={r.get('option_open_interest')} "
                    f"IV={r.get('option_implied_volatility')} delta={r.get('option_delta')} "
                    f"FLAGS={flags or 'ok'}"
                )

        print("\n=== EXACT 12P MATCH ===")
        m12 = snap[snap["strike_price"].apply(lambda x: abs(float(x)-12.0)<1e-6 if pd.notna(x) else False)]
        if m12.empty:
            print("NO 12.00 PUT in chain")
        else:
            print(m12.to_string(index=False))

print("\n=== 7. SNDK last/high (already in snap) ===")

q.close()

print("\n=== 8. ACCOUNT CASH / POWER ===")
trd = OpenSecTradeContext(
    filter_trdmarket=TrdMarket.NONE,
    host=HOST,
    port=PORT,
    security_firm=SecurityFirm.FUTUSECURITIES,
)
print("=== ACC LIST ===")
ret, accs = trd.get_acc_list()
print("ret", ret)
if ret == 0:
    print(accs.to_string(index=False))
    row = accs[accs["acc_id"]==ACC]
    if not row.empty:
        print("target acc:\n", row.to_string(index=False))
        print("dict", row.iloc[0].to_dict())

ret, info = trd.accinfo_query(trd_env=TrdEnv.REAL, acc_id=ACC, currency=Currency.USD)
print("\n=== USD ACCINFO ret", ret, "===")
if ret == 0:
    r = info.iloc[0]
    print("cols", list(info.columns))
    for k in info.columns:
        print(f"  {k}: {r[k]}")
else:
    print(info)

print("\n=== 9. OPEN ORDERS (read only) ===")
ret, orders = trd.order_list_query(trd_env=TrdEnv.REAL, acc_id=ACC)
print("ret", ret)
if ret == 0:
    if orders.empty:
        print("empty")
    else:
        print("order cols", list(orders.columns))
        print(orders.to_string(index=False))
else:
    print(orders)

print("\n=== POSITIONS ===")
ret, pos = trd.position_list_query(trd_env=TrdEnv.REAL, acc_id=ACC)
print("ret", ret)
if ret == 0:
    if pos.empty:
        print("empty")
    else:
        cols = [c for c in ["code","stock_name","qty","can_sell_qty","nominal_price","cost_price","pl_val","pl_ratio","market_val","currency"] if c in pos.columns]
        print(pos[cols].to_string(index=False) if cols else pos.to_string(index=False))
else:
    print(pos)

trd.close()
print("\nDONE probe")
