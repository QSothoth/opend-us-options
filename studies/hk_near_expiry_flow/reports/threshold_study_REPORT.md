# 港股末日 / 近月期权异动门槛研究（中文）

**范围：** 港股上市正股期权 OpenD 异动，不是涡轮、不是美股。  
**目标：** 在「多报一点」和「别把次日胜率打烂」之间找可执行门槛。  
**不是交易建议。** 不下单。

数据截止 2026-09-18。

## 结论

1. 近月样本极薄：约一年 CALL BUY 异动 1706 笔，到期 ≤5 日只有约 29 笔 / 13 个标的日，几乎全在 9/14–18。  
2. 现行默认（单笔 20 万或扎堆）约 9 个标的日、次日胜率约 44%；其中 6 条是腾讯/阿里等单笔噪声。  
3. **提高信噪比的关键是关掉单笔、只报扎堆。**  
4. 两套预设：  
   - **conservative**：只扎堆，到期 ≤7 日，30 分钟 3 笔或 150 万，正股已涨 >8% 不报  
   - **aggressive**：只扎堆，到期 ≤14 日，30 分钟 2 笔或 100 万  
5. 不要：单笔 20 万当主信号；一边放宽到期一边继续「单笔或扎堆」；到期推到 21 日；拿 n=3 的高胜率当可外推。

## 怎么跑

```bash
/home/box/futu/venv/bin/python3 /home/box/futu/option_flow/scan_hk_option_flow.py --backtest --preset conservative
/home/box/futu/venv/bin/python3 /home/box/futu/option_flow/scan_hk_option_flow.py --backtest --preset aggressive
```

完整网格见同目录 `grid_results.csv`（约 2 万组）。  
后续更优的因果版见 `/workspace/hk-option-flow/pi-improve/REPORT.md`（`--preset pi`）。
