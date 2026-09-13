from futu import *
import pandas as pd

ACC = 281756478582875671
trd = OpenSecTradeContext(
    filter_trdmarket=TrdMarket.NONE,
    host="127.0.0.1",
    port=11111,
    security_firm=SecurityFirm.FUTUSECURITIES,
)
ret, info = trd.accinfo_query(trd_env=TrdEnv.REAL, acc_id=ACC, currency=Currency.USD)
print("=== USD ACC ===", ret)
if ret == 0:
    row = info.iloc[0]
    for k in ["currency","cash","total_assets","market_val","avl_withdrawal_cash","max_withdrawal","power","us_cash","us_avl_withdrawal_cash","usd_net_cash_power","hk_cash","hk_avl_withdrawal_cash","usd_assets","hkd_assets"]:
        if k in row.index:
            print(f"{k}: {row[k]}")
ret, info_hkd = trd.accinfo_query(trd_env=TrdEnv.REAL, acc_id=ACC, currency=Currency.HKD)
print("\n=== HKD cash/power ===")
if ret == 0:
    row = info_hkd.iloc[0]
    for k in ["cash","power","avl_withdrawal_cash","hk_cash","us_cash","usd_net_cash_power","hkd_net_cash_power"]:
        if k in row.index:
            print(f"{k}: {row[k]}")

# can we trade US options?
print("\n=== ACC LIST AUTH ===")
ret, accs = trd.get_acc_list()
print(accs[accs["acc_id"]==ACC][["acc_id","trd_env","acc_type","trdmarket_auth","acc_status"]].to_string(index=False))
trd.close()

q = OpenQuoteContext(host="127.0.0.1", port=11111)
print("\n=== SPOT ===")
ret, s = q.get_market_snapshot(["US.SKHY","US.SNDK"])
print(s[["code","last_price","high_price","low_price","prev_close_price"]].to_string(index=False))

codes = [
    "US.SKHY261016P145000",
    "US.SKHY261016P150000",
    "US.SKHY261016P155000",
    "US.SKHY261120P145000",
    "US.SKHY261120P150000",
    "US.SKHY261120P155000",
    "US.SKHY260918P145000",
    "US.SKHY260918P150000",
    "US.SNDK260918P1300000",
    "US.SNDK261016P1300000",
    "US.SNDK261016P1400000",
    "US.SNDK261120P1300000",
]
ret, opt = q.get_market_snapshot(codes)
print("\n=== CANDIDATES ===", ret)
if ret == 0:
    cols = [c for c in [
        "code","name","last_price","bid_price","ask_price","bid_vol","ask_vol",
        "volume","option_open_interest","option_implied_volatility",
        "option_delta","option_theta","option_vega","option_gamma",
        "strike_price"
    ] if c in opt.columns]
    print(opt[cols].to_string(index=False))
    print("\n--- derived ---")
    for _, r in opt.iterrows():
        bid, ask = r.get("bid_price"), r.get("ask_price")
        mid = (bid+ask)/2 if pd.notna(bid) and pd.notna(ask) and bid>0 else r.get("last_price")
        theta = r.get("option_theta")
        delta = r.get("option_delta")
        iv = r.get("option_implied_volatility")
        debit = mid*100 if mid else None
        theta_day = theta*100 if pd.notna(theta) else None
        pct = abs(theta)/mid*100 if mid and theta and mid>0 else None
        print(f"{r['code']}: mid={mid:.3f} debit=${debit:.0f} delta={delta:.3f} theta/day=${theta_day:.1f} theta%={pct:.2f}%/d IV={iv:.1f} OI={r.get('option_open_interest')} spr={ask-bid:.2f}")
q.close()
