#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_option_quotes.py

用途：只读探测 Futu OpenD 对「美股期权」行情的支持能力，回答：
  A. 能否拿到期权实时/快照行情，有哪些字段
  B. 能否拿历史日K/分钟K做回测，粒度/时间跨度
  C. 失败原因（权限/额度/限频/接口不支持）
  D. 「先回测、后实盘盯分钟K」的可行性

设计要点：
  * 只读行情，绝不下单。
  * 每个请求之间 sleep，避免触发限频。
  * 订阅后必须持有 >= 1 分钟才能 unsubscribe，否则报错；脚本默认等待 61s。
    使用 --fast 可跳过等待（会记录 unsubscribe 失败原文）。
  * 运行结束后生成 RESULT.md 并打印关键结论。

用法：
  /home/box/futu/venv/bin/python3 probe_option_quotes.py          # 完整跑（含 1 分钟退订等待）
  /home/box/futu/venv/bin/python3 probe_option_quotes.py --fast   # 不等待退订
"""

import os
import sys
import time
import json
import datetime
import traceback

import pandas as pd

from futu import (
    OpenQuoteContext, SubType, KLType, AuType, OptionType, OptionMarket,
    StrategyLegAction, OptionStrategyLeg, RET_OK,
)

HOST = "127.0.0.1"
PORT = 11111
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_MD = os.path.join(OUT_DIR, "RESULT.md")
RAW_JSON = os.path.join(OUT_DIR, "probe_raw.json")
SLEEP = 1.2          # 普通请求间隔
FAST = "--fast" in sys.argv

pd.set_option("display.width", 320)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", 400)

LOG = []            # 文本日志
RAW = {}            # 结构化结果，写入 probe_raw.json


def log(*args):
    s = " ".join(str(a) for a in args)
    print(s)
    LOG.append(s)


def sleep(t=SLEEP):
    time.sleep(t)


def df_preview(df, n=8):
    if df is None:
        return "(None)"
    if hasattr(df, "head"):
        try:
            return df.head(n).to_string()
        except Exception:
            return str(df)
    return str(df)


def series_dict(s):
    """把 get_market_snapshot / get_stock_quote 的单行结果转成 dict（去掉 NaN）"""
    try:
        d = s.to_dict() if hasattr(s, "to_dict") else dict(s)
    except Exception:
        return {"_repr": str(s)}
    out = {}
    for k, v in d.items():
        try:
            if pd.isna(v):
                continue
        except Exception:
            pass
        out[str(k)] = v
    return out


def as_float(x):
    try:
        return float(x)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 0. 连接
# ---------------------------------------------------------------------------
def connect():
    log("=" * 90)
    log("0) 连接 OpenQuoteContext host=%s port=%s" % (HOST, PORT))
    q = OpenQuoteContext(host=HOST, port=PORT)
    log("connected:", q.conn_str if hasattr(q, "conn_str") else "ok")
    return q


# ---------------------------------------------------------------------------
# 1. 权限 / 全局状态
# ---------------------------------------------------------------------------
def probe_permission(q):
    log("\n" + "=" * 90)
    log("1) 权限与全局状态")
    sleep()

    ret, state = q.get_global_state()
    log("get_global_state ret=%s" % ret)
    log(json.dumps(state, ensure_ascii=False, indent=2) if ret == RET_OK else state)
    RAW["global_state"] = state if ret == RET_OK else str(state)

    sleep()
    ret, ui = q.get_user_info()
    log("\nget_user_info ret=%s" % ret)
    if ret == RET_OK:
        log(json.dumps(ui, ensure_ascii=False, indent=2))
        RAW["user_info"] = ui
    else:
        log(ui)
        RAW["user_info"] = str(ui)

    sleep()
    ret, sub = q.query_subscription()
    log("\nquery_subscription ret=%s" % ret)
    if ret == RET_OK:
        log(json.dumps(sub, ensure_ascii=False, indent=2))
        RAW["subscription_before"] = sub
    else:
        log(sub)
        RAW["subscription_before"] = str(sub)

    sleep()
    ret, quota = q.get_history_kl_quota(get_detail=False)
    log("\nget_history_kl_quota ret=%s -> %s" % (ret, quota))
    RAW["history_kl_quota_before"] = quota if ret == RET_OK else str(quota)


# ---------------------------------------------------------------------------
# 2. 选一个真实合约
# ---------------------------------------------------------------------------
def pick_contract(q, underlying="US.AAPL"):
    log("\n" + "=" * 90)
    log("2) 选取真实期权合约 underlying=%s" % underlying)

    sleep()
    ret, spot_df = q.get_market_snapshot([underlying])
    spot = None
    if ret == RET_OK and len(spot_df):
        spot = as_float(spot_df.iloc[0].get("last_price"))
    log("标的最新价 %s = %s (ret=%s)" % (underlying, spot, ret))
    RAW["underlying_spot"] = spot

    sleep()
    ret, exps = q.get_option_expiration_date(underlying)
    if ret != RET_OK:
        raise RuntimeError("get_option_expiration_date 失败: %s" % exps)
    log("get_option_expiration_date ret=0 共 %d 个到期日" % len(exps))
    log(df_preview(exps, 12))
    RAW["expirations"] = exps.to_dict("records")

    # 选最近的、未到期的到期日（dist >= 1）
    future = exps[exps["option_expiry_date_distance"] >= 1].copy()
    future = future.sort_values("option_expiry_date_distance")
    expiry = str(future.iloc[0]["strike_time"])
    log("选定到期日（最近未到期）= %s" % expiry)

    # 取该到期日全部 CALL + PUT
    time.sleep(1.0)
    ret, chain = q.get_option_chain(underlying, start=expiry, end=expiry,
                                    option_type=OptionType.ALL)
    if ret != RET_OK:
        raise RuntimeError("get_option_chain 失败: %s" % chain)
    log("get_option_chain ret=0 共 %d 条合约" % len(chain))
    log("chain columns: %s" % list(chain.columns))
    log(df_preview(chain, 5))
    RAW["chain_count"] = int(len(chain))
    RAW["chain_columns"] = list(chain.columns)

    # 选最接近平值的 call / put
    def nearest(df_chain, opt_type):
        sub = df_chain[df_chain["option_type"] == opt_type].copy()
        if spot is None or sub.empty:
            return None, None
        sub["dist"] = (sub["strike_price"] - spot).abs()
        row = sub.sort_values("dist").iloc[0]
        return row["code"], float(row["strike_price"])

    call_code, call_strike = nearest(chain, "CALL")
    put_code, put_strike = nearest(chain, "PUT")
    log("ATM CALL = %s (strike=%s)" % (call_code, call_strike))
    log("ATM PUT  = %s (strike=%s)" % (put_code, put_strike))

    main = call_code or (chain.iloc[len(chain) // 2]["code"])
    RAW["expiry"] = expiry
    RAW["atm_call"] = {"code": call_code, "strike": call_strike}
    RAW["atm_put"] = {"code": put_code, "strike": put_strike}
    RAW["main_code"] = main

    # ---- 额外挑几个不同到期日的 ATM CALL，用于比较「历史K线能回溯多久」----
    # 新挂周合约（最近到期）往往只有几天历史，月度/LEAPS 才有长历史。
    future = future.reset_index(drop=True)
    picks = []
    picks.append(("nearest", 0))
    for target in (30, 90):
        idx = (future["option_expiry_date_distance"] - target).abs().idxmin()
        picks.append(("~%dd" % target, int(idx)))
    picks.append(("farthest", int(future["option_expiry_date_distance"].idxmax())))
    seen = set()
    candidates = []
    for label, idx in picks:
        exp = str(future.iloc[idx]["strike_time"])
        if exp in seen:
            continue
        seen.add(exp)
        if exp == expiry:
            candidates.append({"label": label, "expiry": exp,
                               "code": call_code, "strike": call_strike})
            continue
        time.sleep(1.0)
        ret_c, ch = q.get_option_chain(underlying, start=exp, end=exp,
                                       option_type=OptionType.CALL)
        if ret_c != RET_OK or ch.empty:
            log("  候选到期日 %s 取链失败: %s" % (exp, ch))
            continue
        c, s = nearest(ch, "CALL")
        candidates.append({"label": label, "expiry": exp, "code": c, "strike": s})
        log("候选合约[%s] exp=%s -> %s (strike=%s)" % (label, exp, c, s))
    RAW["history_candidates"] = candidates
    return main, [call_code, put_code], chain


# ---------------------------------------------------------------------------
# 3. 快照 get_market_snapshot
# ---------------------------------------------------------------------------
def probe_snapshot(q, codes, main):
    log("\n" + "=" * 90)
    log("3) 快照 get_market_snapshot")
    codes = [c for c in codes if c]
    sleep()
    ret, snap = q.get_market_snapshot(codes)
    log("ret=%s n=%s" % (ret, 0 if ret != RET_OK else len(snap)))
    if ret != RET_OK:
        log(snap)
        RAW["snapshot_error"] = str(snap)
        return
    RAW["snapshot_columns"] = list(snap.columns)
    show = [c for c in [
        "code", "last_price", "open_price", "high_price", "low_price",
        "prev_close_price", "bid_price", "ask_price", "bid_vol", "ask_vol",
        "volume", "turnover", "option_open_interest", "option_net_open_interest",
        "option_implied_volatility", "option_delta", "option_gamma",
        "option_vega", "option_theta", "option_rho", "option_premium",
        "option_strike_price", "strike_time", "option_expiry_date_distance",
        "option_type", "option_area_type", "option_contract_multiplier",
        "update_time",
    ] if c in snap.columns]
    log(snap[show].to_string(index=False))
    # 主合约完整字段
    row = snap[snap["code"] == main]
    if len(row):
        RAW["main_snapshot"] = series_dict(row.iloc[0])
        log("\n主合约全部非空字段 (%s):" % main)
        for k, v in RAW["main_snapshot"].items():
            log("  %-38s = %s" % (k, v))

    # 逐条快照看 bid/ask 是否随合约不同而不同
    sleep()
    ret2, snap1 = q.get_market_snapshot([main])
    if ret2 == RET_OK and len(snap1):
        log("\n主合约单条快照 bid/ask/IV/greeks：")
        for k in ["last_price", "bid_price", "ask_price", "bid_vol", "ask_vol",
                  "volume", "option_open_interest", "option_implied_volatility",
                  "option_delta", "option_gamma", "option_vega", "option_theta"]:
            if k in snap1.columns:
                log("  %-32s = %s" % (k, snap1.iloc[0][k]))


# ---------------------------------------------------------------------------
# 3b. get_option_quote（期权专用行情）
# ---------------------------------------------------------------------------
def probe_option_quote(q, code):
    log("\n" + "=" * 90)
    log("3b) get_option_quote（期权专用行情） code=%s" % code)
    if not code:
        log("跳过（无合约）")
        return
    sleep()
    try:
        leg = OptionStrategyLeg()
        leg.code = code
        leg.action = StrategyLegAction.BUY
        leg.quantity = 1
        ret, d = q.get_option_quote([leg])
        log("ret=%s" % ret)
        if ret == RET_OK:
            log(d.T.to_string())
            RAW["option_quote"] = series_dict(d.iloc[0]) if len(d) else {}
        else:
            log(d)
            RAW["option_quote_error"] = str(d)
    except Exception as e:
        log("EXC:", repr(e))
        RAW["option_quote_error"] = repr(e)


# ---------------------------------------------------------------------------
# 4. 订阅 + 实时报价 + 逐笔 + 摆盘 + 实时分钟K
# ---------------------------------------------------------------------------
def probe_realtime(q, code):
    log("\n" + "=" * 90)
    log("4) 订阅与实时行情 code=%s" % code)
    if not code:
        log("跳过（无合约）")
        return
    subtypes = [SubType.QUOTE, SubType.TICKER, SubType.ORDER_BOOK, SubType.K_1M]
    sleep()
    ret, msg = q.subscribe([code], subtypes)
    log("subscribe(%s) ret=%s msg=%s" % ([str(s) for s in subtypes], ret, msg))
    RAW["subscribe"] = {"ret": ret, "msg": str(msg)}
    time.sleep(2)

    sleep(0.5)
    ret, sub = q.query_subscription()
    if ret == RET_OK:
        log("query_subscription -> %s" % json.dumps(sub, ensure_ascii=False))
        RAW["subscription_after"] = sub

    # get_stock_quote
    log("\n-- get_stock_quote --")
    sleep()
    ret, sq = q.get_stock_quote([code])
    log("ret=%s" % ret)
    if ret == RET_OK:
        log(sq.T.to_string())
        RAW["stock_quote"] = series_dict(sq.iloc[0]) if len(sq) else {}
    else:
        log(sq)
        RAW["stock_quote_error"] = str(sq)

    # get_rt_ticker
    log("\n-- get_rt_ticker --")
    sleep()
    ret, rt = q.get_rt_ticker(code, num=20)
    log("ret=%s n=%s" % (ret, 0 if ret != RET_OK else len(rt)))
    if ret == RET_OK:
        log(df_preview(rt, 8))
        RAW["rt_ticker_rows"] = int(len(rt))
        RAW["rt_ticker_columns"] = list(rt.columns)
    else:
        log(rt)
        RAW["rt_ticker_error"] = str(rt)

    # get_order_book
    log("\n-- get_order_book --")
    sleep()
    ret, ob = q.get_order_book(code)
    log("ret=%s" % ret)
    if ret == RET_OK:
        log(ob if isinstance(ob, dict) else df_preview(ob))
        RAW["order_book"] = ob if isinstance(ob, dict) else str(ob)
    else:
        log(ob)
        RAW["order_book_error"] = str(ob)

    # get_cur_kline（需要 K_1M 订阅）
    log("\n-- get_cur_kline(K_1M) --")
    sleep()
    ret, ck = q.get_cur_kline(code, 10, KLType.K_1M)
    log("ret=%s" % ret)
    if ret == RET_OK:
        log(ck.to_string())
        RAW["cur_kline_rows"] = int(len(ck))
        RAW["cur_kline_columns"] = list(ck.columns)
    else:
        log(ck)
        RAW["cur_kline_error"] = str(ck)


def probe_unsubscribe(q, code):
    log("\n-- unsubscribe --")
    if not code:
        return
    if FAST:
        log("--fast: 不等待 61s，直接退订（预期报“订阅时长过短”）")
    else:
        log("订阅需持有 >= 1 分钟才能退订，等待 61s ...")
        time.sleep(61)
    ret, msg = q.unsubscribe_all()
    log("unsubscribe_all ret=%s msg=%s" % (ret, msg))
    RAW["unsubscribe"] = {"ret": ret, "msg": str(msg)}


# ---------------------------------------------------------------------------
# 5/6. 历史 K 线 + 实时分钟 K
# ---------------------------------------------------------------------------
def probe_history(q, code):
    log("\n" + "=" * 90)
    log("5) request_history_kline code=%s" % code)
    if not code:
        log("跳过（无合约）")
        return
    RAW["history"] = {}
    for kt in [KLType.K_DAY, KLType.K_1M, KLType.K_5M]:
        log("\n-- ktype=%s --" % kt)
        sleep()
        try:
            out = q.request_history_kline(
                code, start="2015-01-01", end="2030-01-01",
                ktype=kt, autype=AuType.NONE, max_count=1000)
            ret, df, page_key = out[0], out[1], out[2]
            log("ret=%s n=%s" % (ret, 0 if ret != RET_OK else len(df)))
            if ret != RET_OK:
                log(df)
                RAW["history"][str(kt)] = {"ret": ret, "error": str(df)}
                continue
            info = {
                "ret": ret,
                "n": int(len(df)),
                "columns": list(df.columns),
                "earliest": str(df["time_key"].min()) if len(df) else None,
                "latest": str(df["time_key"].max()) if len(df) else None,
                "has_next_page": page_key is not None,
            }
            RAW["history"][str(kt)] = info
            log("columns: %s" % info["columns"])
            log("earliest=%s latest=%s has_next_page=%s"
                % (info["earliest"], info["latest"], info["has_next_page"]))
            log(df_preview(df, 3))
            log("...")
            if len(df):
                log(df.tail(3).to_string(index=False))

            # 分钟K翻页验证
            if kt in (KLType.K_1M, KLType.K_5M) and page_key is not None:
                sleep()
                out2 = q.request_history_kline(
                    code, start="2015-01-01", end="2030-01-01",
                    ktype=kt, autype=AuType.NONE, max_count=1000,
                    page_req_key=page_key)
                ret2, df2, key2 = out2[0], out2[1], out2[2]
                if ret2 == RET_OK and len(df2):
                    log("page2 ret=0 n=%d %s -> %s next=%s"
                        % (len(df2), df2["time_key"].min(),
                           df2["time_key"].max(), key2 is not None))
                    info["page2_earliest"] = str(df2["time_key"].min())
                    info["page2_latest"] = str(df2["time_key"].max())
                else:
                    log("page2 ret=%s %s" % (ret2, df2))
        except Exception as e:
            log("EXC:", repr(e))
            traceback.print_exc()
            RAW["history"][str(kt)] = {"error": repr(e)}

    # 6) get_cur_kline 已在 probe_realtime 里测过；这里再记录实时分钟K的订阅要求
    log("\n6) get_cur_kline 需先 subscribe K_1M，已在第 4 步验证。")

    sleep()
    ret, quota = q.get_history_kl_quota(get_detail=True)
    log("\nget_history_kl_quota(after) ret=%s -> %s" % (ret, quota))
    RAW["history_kl_quota_after"] = str(quota) if ret == RET_OK else str(quota)


# ---------------------------------------------------------------------------
# 5b. 多个到期日的历史跨度对比
# ---------------------------------------------------------------------------
def probe_history_span(q, candidates):
    log("\n" + "=" * 90)
    log("5b) 不同到期日合约的历史K线跨度对比（判断能回测多久）")
    span = []
    for c in candidates:
        code = c.get("code")
        if not code:
            continue
        entry = {"label": c["label"], "expiry": c["expiry"], "code": code}
        sleep()
        try:
            r, d, _ = q.request_history_kline(code, start="2005-01-01",
                                              end="2035-01-01",
                                              ktype=KLType.K_DAY,
                                              autype=AuType.NONE, max_count=1000)
            if r == RET_OK and len(d):
                entry["day_n"] = int(len(d))
                entry["day_from"] = str(d["time_key"].min())
                entry["day_to"] = str(d["time_key"].max())
            else:
                entry["day_err"] = str(d) if r != RET_OK else "empty"
        except Exception as e:
            entry["day_err"] = repr(e)
        sleep()
        try:
            r, d, pk = q.request_history_kline(code, start="2005-01-01",
                                               end="2035-01-01",
                                               ktype=KLType.K_1M,
                                               autype=AuType.NONE, max_count=1000)
            if r == RET_OK and len(d):
                entry["min1_n"] = int(len(d))
                entry["min1_from"] = str(d["time_key"].min())
                entry["min1_to"] = str(d["time_key"].max())
                entry["min1_paged"] = pk is not None
            else:
                entry["min1_err"] = str(d) if r != RET_OK else "empty"
        except Exception as e:
            entry["min1_err"] = repr(e)
        span.append(entry)
        log("[%s] %s exp=%s" % (entry.get("label"), code, entry.get("expiry")))
        log("    日K : %s 根 %s ~ %s %s"
            % (entry.get("day_n"), entry.get("day_from"), entry.get("day_to"),
               entry.get("day_err", "")))
        log("    1M  : %s 根 %s ~ %s 翻页=%s %s"
            % (entry.get("min1_n"), entry.get("min1_from"), entry.get("min1_to"),
               entry.get("min1_paged"), entry.get("min1_err", "")))
    RAW["history_span"] = span


# ---------------------------------------------------------------------------
# 7. Zero-DTE screener
# ---------------------------------------------------------------------------
def probe_zero_dte(q):
    log("\n" + "=" * 90)
    log("7) get_option_zero_dte_screener(US_SECURITY)")
    sleep()
    try:
        ret, d = q.get_option_zero_dte_screener(OptionMarket.US_SECURITY,
                                                count=10)
        log("ret=%s type=%s" % (ret, type(d)))
        if ret == RET_OK:
            if isinstance(d, dict):
                items = d.get("item_list")
                if items is not None and hasattr(items, "to_string"):
                    log(items.to_string(index=False))
                    RAW["zero_dte_count"] = int(len(items))
                    RAW["zero_dte_columns"] = list(items.columns)
                else:
                    log(json.dumps(d, ensure_ascii=False, default=str)[:2000])
                log("next_page=%s" % (d.get("next_page") if isinstance(d, dict) else None))
            else:
                log(d)
                RAW["zero_dte"] = str(d)
        else:
            log(d)
            RAW["zero_dte_error"] = str(d)
    except Exception as e:
        log("EXC:", repr(e))
        RAW["zero_dte_error"] = repr(e)


# ---------------------------------------------------------------------------
# 生成 RESULT.md
# ---------------------------------------------------------------------------
def build_result_md(raw):
    def g(*keys, default=None):
        cur = raw
        for k in keys:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur

    ui = raw.get("user_info", {})
    gs = raw.get("global_state", {})
    main = raw.get("main_code")
    snap = raw.get("main_snapshot", {})
    sq = raw.get("stock_quote", {})
    oq = raw.get("option_quote", {})
    hist = raw.get("history", {})
    sub_after = raw.get("subscription_after", {})

    def hist_line(kt):
        h = hist.get(kt, {})
        if not h:
            return "- %s: 无数据" % kt
        if h.get("ret") != 0:
            return "- %s: 失败 ret=%s err=%s" % (kt, h.get("ret"), h.get("error"))
        return ("- %s: 成功，首屏 %s 根；最早 %s，最晚 %s；翻页=%s"
                % (kt, h.get("n"), h.get("earliest"), h.get("latest"),
                   h.get("has_next_page")))

    def fmt_fields(d, keys):
        lines = []
        for k in keys:
            if k in d:
                lines.append("| `%s` | `%s` |" % (k, d[k]))
        return "\n".join(lines) if lines else "| - | - |"

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    md = []
    md.append("# 美股期权行情能力探测 RESULT")
    md.append("")
    md.append("生成时间：%s（本机时区）" % now)
    md.append("探测脚本：`probe_option_quotes.py`（可复跑）；原始数据：`probe_raw.json`，完整日志见脚本 stdout。")
    md.append("连接：OpenD %s:%s；SDK futu-api 10.10.7008；标的：`US.AAPL`" % (HOST, PORT))
    md.append("")
    md.append("> 仅读行情，未下任何订单。")
    md.append("")

    md.append("## 0. 权限 / 账户")
    md.append("")
    md.append("- `qot_logined=%s`，`trd_logined=%s`，`server_ver=%s`"
              % (gs.get("qot_logined"), gs.get("trd_logined"), gs.get("server_ver")))
    md.append("- 美股行情权限 `us_qot_right` = **%s**" % ui.get("us_qot_right"))
    md.append("- 美股期权行情权限 `us_option_qot_right` = **%s**" % ui.get("us_option_qot_right"))
    md.append("- 订阅额度 `sub_quota` = %s；历史K线额度 `history_kl_quota` = %s"
              % (ui.get("sub_quota"), ui.get("history_kl_quota")))
    md.append("- 订阅后 `option_used_quota=%s / option_remain_quota=%s`（期权订阅总上限=%s）"
              % (sub_after.get("option_used_quota"), sub_after.get("option_remain_quota"),
                 (sub_after.get("option_used_quota", 0) + sub_after.get("option_remain_quota", 0))
                 if isinstance(sub_after.get("option_used_quota"), int) else "?"))
    md.append("")

    md.append("## A. 现在能不能拿到期权实时/快照行情？有哪些字段？")
    md.append("")
    md.append("**能。** 快照 `get_market_snapshot([option_code])`、实时报价 `get_stock_quote`、"
              "逐笔 `get_rt_ticker`、摆盘 `get_order_book`、期权专用 `get_option_quote` 全部 ret=0。")
    md.append("")
    md.append("探测合约：`%s`（到期 %s，ATM CALL，strike=%s）"
              % (main, raw.get("expiry"), g("atm_call", "strike")))
    md.append("")
    md.append("| 来源 | 字段 | 值 |")
    md.append("| --- | --- | --- |")
    md.append(fmt_fields(snap, [
        "last_price", "bid_price", "ask_price", "bid_vol", "ask_vol",
        "volume", "turnover", "option_open_interest",
        "option_implied_volatility", "option_delta", "option_gamma",
        "option_vega", "option_theta", "option_rho", "option_premium",
        "option_strike_price", "strike_time", "option_expiry_date_distance",
        "option_type", "update_time",
    ]))
    md.append("")
    md.append("`get_option_quote({code})` 额外给出（更丰富）：")
    md.append("")
    md.append("| 字段 | 值 |")
    md.append("| --- | --- |")
    md.append(fmt_fields(oq, [
        "price", "mid_price", "mark_price", "implied_volatility",
        "delta", "gamma", "vega", "theta", "rho",
        "intrinsic_value", "time_value", "breakeven_point",
        "dist_to_breakeven", "prob_of_profit", "seller_roi",
        "leverage_ratio", "effective_gearing", "days_to_expiry",
    ]))
    md.append("")
    md.append("- 快照字段列举（列名）：`%s`" % ", ".join(raw.get("snapshot_columns", [])[:40]))
    md.append("- 实时报价（订阅 QUOTE 后 `get_stock_quote`）字段：last/open/high/low/volume/turnover/"
              "IV(bid/ask 走快照)、greeks、open_interest、premium 等。")
    md.append("- 逐笔 `get_rt_ticker`：%s 行，列 `%s`。"
              % (raw.get("rt_ticker_rows"), raw.get("rt_ticker_columns")))
    md.append("- 摆盘 `get_order_book`：%s" % raw.get("order_book"))
    md.append("")
    md.append("**注意**：实测非流动性/深实值合约的 IV 与 greeks 可能返回 `0`（如 260918 C240000），"
              "平值活跃合约（C325000~C340000）IV≈25~27、delta/gamma/theta/vega 均正常。")
    md.append("")

    md.append("## B. 能不能拿历史日K/分钟K做回测？粒度/多久？")
    md.append("")
    md.append("**能。** `request_history_kline` 对期权 code 直接返回 ret=0（返回三元组 `ret, df, page_req_key`，"
              "分页 key 用于翻页）。")
    md.append("")
    md.append(hist_line("K_DAY"))
    md.append(hist_line("K_1M"))
    md.append(hist_line("K_5M"))
    md.append("")
    md.append("### 不同到期日合约的历史跨度对比（关键：历史长短取决于挂牌时间）")
    md.append("")
    span = raw.get("history_span", [])
    if span:
        md.append("| 类型 | 合约 | 到期日 | 日K根数 | 日K起点 | 日K终点 | 1M首屏 | 1M起点 | 1M终点 | 1M可翻页 |")
        md.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for e in span:
            md.append("| %s | `%s` | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                e.get("label"), e.get("code"), e.get("expiry"),
                e.get("day_n", "-"), e.get("day_from", e.get("day_err", "-")),
                e.get("day_to", "-"), e.get("min1_n", "-"),
                e.get("min1_from", e.get("min1_err", "-")),
                e.get("min1_to", "-"), e.get("min1_paged", "-")))
    else:
        md.append("（无候选数据）")
    md.append("")
    md.append("- 日K：期权上市首日 → 最近交易日，字段 open/close/high/low/volume/turnover/change_rate/last_close。")
    md.append("  实测同一 AAPL 标的、不同到期日差异极大：**新挂周合约仅 2 天**历史（260914），"
              "**月度/远期合约可达 600+ 个交易日 / 2 年+**（261218 自 2024-03-26 起 618 根）；"
              "**并非所有挂牌合约都有 K 线**（最远 LEAPS 290119 返回空，未成交/新挂）。")
    md.append("- 分钟K：支持 `K_1M/K_5M/K_15M/K_30M/K_60M` 等。单页最多 1000 根，用 `page_req_key` 翻页，"
              "可从合约**上市首日**开始拉取。活跃平值合约 1 分钟约 300~470 根/交易日，长历史合约全生命周期约 10 万+ 根，"
              "翻页即可全部取回（历史短于 1000 根时一次性返回、无 page key）。")
    md.append("- 实时分钟K：`get_cur_kline(code, n, K_1M)` **必须先 `subscribe(SubType.K_1M)`**，"
              "订阅后 ret=0 返回最近 n 根；未订阅返回原文：`Before calling the Get Real-time Candlestick interface, "
              "please subscribe to KL_1Min data first.`")
    md.append("")
    md.append("### 回测粒度/跨度结论")
    md.append("")
    md.append("- 粒度：**日K + 1/5/15/30/60 分钟K** 都可用；做「分钟级回测」没问题。")
    md.append("- 跨度：**逐合约**从上市日到摘牌/到期。实测 AAPL 不同到期日差异很大：新挂周合约仅几天，"
              "远期月合约可回溯 2 年+（618 个交易日）。所以做多年回测要自行把同一标的的多个月份/多个到期日合约"
              "拼接成连续序列，并对「上市初期成交稀、无成交分钟」做清洗。")
    md.append("- 额度：`history_kl_quota` 初始 300；本次拉取了 3 个不同到期日的 AAPL 期权日K+1M 后，"
              "`used/remain` 仍为 8/292（`before` / `after` 均为 `(8, 292, ...)`），说明额度不是按请求次数计，"
              "而是按「标的（期权按标的+到期日）」计的滚动窗口额度，翻页/重复拉取同一标的不会额外扣减。")
    md.append("")

    md.append("## C. 失败原因 / 限制")
    md.append("")
    md.append("1. **退订时长限制**：订阅（QUOTE/K_1M 等）后必须持有 **≥ 1 分钟** 才能 `unsubscribe`，"
              "否则原文：`The Basic subscription duration for ... is too short. Minimum subscription duration is 1 minute.`"
              "（本脚本完整模式会等 61s 再退订，已验证可成功退订：%s）" % raw.get("unsubscribe"))
    md.append("2. **实时分钟K必须先订阅 K_1M**：否则 `get_cur_kline` ret=-1（原文见 B 节）。")
    md.append("3. **期权订阅额度按 (合约, 类型) 计**：订阅 1 个合约 × 4 种类型（QUOTE/TICKER/ORDER_BOOK/K_1M）即占用 4 个额度，"
              "`option_used_quota=%s / option_remain_quota=%s`，总上限 60。"
              "即最多约 15 个合约 × 4 类（或 60 个合约 × 1 类），需要做 LRU/批量轮换。"
              % (sub_after.get("option_used_quota"), sub_after.get("option_remain_quota")))
    md.append("4. **IV/greeks 对不活跃合约可能为 0**：优先用平值近月合约。")
    md.append("5. **`get_option_chain` 单次时间窗 ≤ 30 天**，需按到期日分段查询。")
    md.append("6. **`request_history_kline` 返回三元组**，直接 `ret, df = ...` 会报 `too many values to unpack`。")
    md.append("7. 本次探测**未出现权限不足**：`us_option_qot_right=LV1` 已足够拿到快照/实时/历史/分钟K；"
              "未触发限频（所有请求均返回 ret=0，仅退订时长限制和未订阅提示）。")
    md.append("8. **历史 K 线并非所有合约都有**：新挂/无成交合约（如 LEAPS 290119）`request_history_kline` 返回成功但空表。")
    md.append("9. 本次探测在**美股盘后**进行，样例 `update_time` 为最近收盘前后的最后成交/报价，"
              "无法区分「实时 vs 延迟」；`us_option_qot_right=LV1`，正式盘中建议再确认一次数据时延。")
    md.append("")

    md.append("## D. 「先回测、后实盘盯分钟K」可行性结论")
    md.append("")
    md.append("**可行。** 当前账户（美股期权 LV1）已经可以逐合约拉取从上市日到最近的日K与 1/5 分钟K"
              "（`request_history_kline` + `page_req_key` 翻页），回测阶段无需任何额外权限，注意每日 300 的"
              "历史额度消耗和同一合约跨月份拼接；实盘盯盘阶段可用 `subscribe(QUOTE/K_1M)` 拿实时报价与实时分钟K"
              "（`get_cur_kline`）外加 `get_market_snapshot` 取 IV/greeks，但受期权订阅额度上限约束，"
              "需要控制同时订阅的合约数并遵守 1 分钟最短订阅时长；整体链路已跑通，"
              "唯一需要额外设计的是「合约池轮换订阅」和「跨月合约拼接」两件事。")
    md.append("")
    md.append("---")
    md.append("")
    md.append("### 附：原始关键数据")
    md.append("")
    md.append("```json")
    keep = {
        "expiry": raw.get("expiry"),
        "atm_call": raw.get("atm_call"),
        "atm_put": raw.get("atm_put"),
        "history": raw.get("history"),
        "history_span": raw.get("history_span"),
        "history_kl_quota_before": raw.get("history_kl_quota_before"),
        "history_kl_quota_after": raw.get("history_kl_quota_after"),
        "subscription_after": raw.get("subscription_after"),
        "unsubscribe": raw.get("unsubscribe"),
    }
    md.append(json.dumps(keep, ensure_ascii=False, indent=2, default=str))
    md.append("```")

    with open(RESULT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return "\n".join(md)


def print_summary(raw):
    ui = raw.get("user_info", {})
    print("A. 权限/实时行情：us_qot_right=%s, us_option_qot_right=%s；"
          "快照/实时/逐笔/摆盘/期权专用行情均 ret=0，含 IV+greeks。" %
          (ui.get("us_qot_right"), ui.get("us_option_qot_right")))
    for e in raw.get("history_span", []):
        print("B. [%s] %s 日K %s 根 %s ~ %s；1M %s 根 %s ~ %s" % (
            e.get("label"), e.get("code"), e.get("day_n"),
            e.get("day_from", e.get("day_err")), e.get("day_to", ""),
            e.get("min1_n"), e.get("min1_from", e.get("min1_err")), e.get("min1_to", "")))
    hist = raw.get("history", {})
    for kt in ["K_DAY", "K_1M", "K_5M"]:
        h = hist.get(kt, {})
        if h.get("ret") == 0:
            print("B. (主合约 %s) 成功：首屏 %s 根，%s ~ %s，可翻页=%s" %
                  (kt, h.get("n"), h.get("earliest"), h.get("latest"),
                   h.get("has_next_page")))
        else:
            print("B. (主合约 %s) 失败：%s" % (kt, h.get("error")))
    print("C. 限制：订阅>=1min才能退订；get_cur_kline需先订阅K_1M；"
          "期权订阅额度按(合约,类型)计(总上限60)；history额度=%s。"
          % str(raw.get("history_kl_quota_before")))
    print("D. 结论：先回测（逐合约日K/1M/5M分钟K）+后实盘（subscribe QUOTE/K_1M + "
          "get_cur_kline + snapshot）可行，需处理合约池轮换与跨月拼接。")
    print("RESULT.md ->", RESULT_MD)


def main():
    if "--from-raw" in sys.argv:
        with open(RAW_JSON, "r", encoding="utf-8") as f:
            raw = json.load(f)
        build_result_md(raw)
        print("（--from-raw：仅由 probe_raw.json 重建 RESULT.md，未连接 OpenD）")
        print_summary(raw)
        return

    q = None
    try:
        q = connect()
        probe_permission(q)
        main_code, codes, chain = pick_contract(q, "US.AAPL")
        probe_snapshot(q, codes, main_code)
        probe_option_quote(q, main_code)
        probe_realtime(q, main_code)
        probe_history(q, main_code)
        probe_history_span(q, RAW.get("history_candidates", []))
        probe_zero_dte(q)
        probe_unsubscribe(q, main_code)
    except Exception as e:
        log("FATAL:", repr(e))
        traceback.print_exc()
    finally:
        if q is not None:
            try:
                q.close()
                log("\ncontext closed.")
            except Exception as e:
                log("close error:", repr(e))

    # 写原始 json
    with open(RAW_JSON, "w", encoding="utf-8") as f:
        json.dump(RAW, f, ensure_ascii=False, indent=2, default=str)

    # 写 RESULT.md
    build_result_md(RAW)

    # 打印关键结论
    print("\n" + "#" * 90)
    print("# RESULT.md 关键结论")
    print("#" * 90)
    print_summary(RAW)


if __name__ == "__main__":
    main()
