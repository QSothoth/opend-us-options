from futu import *
from datetime import datetime, timezone, timedelta

q = OpenQuoteContext(host="127.0.0.1", port=11111)
trd = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host="127.0.0.1", port=11111, security_firm=SecurityFirm.FUTUSECURITIES)
ACC = 281756478582875671

print("now SH", datetime.now(timezone(timedelta(hours=8))).isoformat())
print("now ET", datetime.now(timezone(timedelta(hours=-4))).isoformat())

ret, st = q.get_global_state()
print("GLOBAL", {k: st.get(k) for k in ["market_us","trd_logined","qot_logined","timestamp"]})

codes = ["US.SKDD","US.SKHZ","US.SKHN","US.SKHY","US.SKHQ"]
ret, s = q.get_market_snapshot(codes)
print("\nSNAP", ret)
if ret==0:
    for _, r in s.iterrows():
        code=r["code"]
        bid=float(r.get("bid_price") or 0); ask=float(r.get("ask_price") or 0)
        last=float(r.get("last_price") or 0)
        pre=r.get("pre_price"); overnight=r.get("overnight_price"); after=r.get("after_price")
        pre_vol=r.get("pre_volume"); on_vol=r.get("overnight_volume"); after_vol=r.get("after_volume")
        spr = (ask-bid) if bid>0 and ask>0 else None
        print(f"\n{code} {r.get('name')}")
        print(f"  last={last} bid={bid} ask={ask} spr={spr}")
        print(f"  pre={pre} pre_vol={pre_vol} | after={after} after_vol={after_vol} | overnight={overnight} on_vol={on_vol}")
        print(f"  status={r.get('sec_status')} vol={r.get('volume')}")

        for sess_name in ["RTH","ETH","OVERNIGHT","ALL"]:
            sess=getattr(Session, sess_name)
            px = ask if ask>0 else (last if last>0 else 10)
            ret2, m = trd.acctradinginfo_query(order_type=OrderType.NORMAL, code=code, price=px, trd_env=TrdEnv.REAL, acc_id=ACC, session=sess)
            if ret2==0:
                row=m.iloc[0]
                print(f"  max_cash_buy[{sess_name}]={row.get('max_cash_buy')}")
            else:
                print(f"  max_cash_buy[{sess_name}]=FAIL {m}")

q.close(); trd.close()
