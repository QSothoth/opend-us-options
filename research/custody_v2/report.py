"""Build a standalone Chinese audit report from the frozen evidence."""
import argparse
import gzip
import html
import json
from pathlib import Path

ROOT = Path(__file__).parent

def read(name):
    p = ROOT / 'results' / name
    return json.loads(p.read_text() if p.exists() else gzip.decompress(p.with_suffix(p.suffix + '.gz').read_bytes()))

def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join('<th>'+html.escape(str(x))+'</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in r)+'</tr>' for r in rows) + '</tbody></table></div>'

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', required=True); args = ap.parse_args()
    d = read('DIAGNOSTICS.json'); robust = read('FINAL_SELECTION_BEFORE_CONFIRMATION.json'); aggressive = read('NOMINAL_PRESSURE.json')
    reports = {'v1 基线': read('v1_service_report.json'), 'v2 进取型': read('aggressive_service_reports.json')['1'], 'v2 稳健型': read('v2_service_report.json')}
    metrics = [d['factorial']['v1_entry_v1_exit']['net'], d['aggressive']['net'], d['robust']['net']]
    rows = []
    for (label, report), m in zip(reports.items(), metrics):
        rows.append([label, '124/124', f"{m['payoff_dollar']:.4f}", f"{m['payoff_return']:.4f}", f"{m['wins']/124:.2%}", f"${m['pnl']:,.2f}", f"{m['mean']:.2%}"])
    summary = table(['方案','完成','美元盈亏比','收益率盈亏比','胜率','净期权盈亏','等权平均收益率'], rows)
    pressure = table(['延迟 / 单边滑点','进取型净盈亏','美元盈亏比','稳健型净盈亏','美元盈亏比'], [[f"{a['delay']} 分钟 / {a['bps']} bps", f"${a['pnl']:,.2f}", f"{a['payoff_dollar']:.4f}", f"${b['pnl']:,.2f}", f"{b['payoff_dollar']:.4f}"] for a,b in zip(aggressive['scenarios'],robust['scenarios'])])
    ablations = table(['稳健型组件消融','改变 case','净盈亏','美元盈亏比','收益率盈亏比','正收益时间块'], [[a['name'],a['changed_cases'],f"${a['pnl']:,.2f}",f"{a['payoff_dollar']:.4f}",f"{a['payoff_return']:.4f}",f"{a['block_positive']}/3"] for a in d['ablations']])
    data = {}
    for label, report in reports.items():
        data[label] = []
        for row in report['cases']:
            c=row['case']; ep=row['entry']['price']; xp=row['exit']['price']; net=(xp*.9975-ep*1.0025)*100-1.3
            data[label].append([c['trade_date'],c['symbol'],c['contract'],c['direction'],row['entry']['at'],row['exit']['at'],ep,xp,(xp-ep)*100,net,net/(ep*1.0025*100)])
    branch='https://github.com/QSothoth/opend-us-options/tree/agent/custody-payoff-search-v2'
    body = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>托管择时 v2 · 31,295 组合</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f2f5f8;color:#172a3a;font:16px/1.7 system-ui,-apple-system,sans-serif}main{max-width:1160px;margin:auto;padding:32px 22px 70px}h1{font-size:32px;line-height:1.35;margin:10px 0}h2{font-size:22px;margin-top:0}h3{font-size:18px}section{background:white;border:1px solid #dbe3eb;border-radius:12px;padding:25px;margin:20px 0}.tag{color:#35647e;font-weight:700}.note{padding:14px 18px;border-left:4px solid #b88028;background:#fff5df}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px;white-space:nowrap}th,td{padding:10px 13px;border-bottom:1px solid #e1e7ed;text-align:right}th:first-child,td:first-child{text-align:left}th{background:#edf4f8}a{color:#126398}select,input{font:inherit;padding:8px;border:1px solid #a8b9c5;border-radius:5px;margin:5px 12px 12px 0}code{font-size:13px;overflow-wrap:anywhere}details{margin:18px 0}summary{cursor:pointer;font-weight:700}.grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}.muted{color:#576b7b}@media(max-width:720px){main{padding:18px 12px}.grid{grid-template-columns:1fr}section{padding:17px}h1{font-size:26px}}@media print{body{background:white}section{break-inside:avoid}.scroll{overflow:visible}input,select{display:none}}</style><main>
    <p class="tag">2026-09-15 · 指定期权 · 每 case 一次往返 · 仅 dryrun</p><h1>31,295 组搜索后，保留两种盈亏比取向</h1><p>进取型优先平均盈利 / 平均亏损；稳健型优先延迟成交与更高成本下的最差表现。最终两个候选都完成 124/124 笔交易，没有靠空仓或删除标的提高成绩。</p>
    <p class="note"><b>这是训练内结果。</b>仅使用 custody-train-dte4 的 124 个 case、19 个日期。3-case 验证集在旧 v1 阶段已报告，本轮完全排除；成本压力、时间分块与参数邻居也不能替代新的盲测。</p>
    <section><h2>同口径比较</h2><p>每个 case 1 张指定期权，单边手续费 $0.65 + 单边滑点 25 bps；取入场/退出意图后至少一分钟的首根正成交量期权 K 线收盘价。所有收益都来自那张期权。</p>''' + summary + '''<p class="muted">平均收益率是逐 case 权利金收益率等权均值，并非账户收益率。美元盈亏比与收益率盈亏比同时衡量，避免单纯依赖高价期权；没有人为设置固定止盈目标。</p><p>毛口径美元盈亏比：v1 <b>3.98</b> → 进取型 <b>6.83</b> / 稳健型 <b>5.08</b>。盈利/亏损均值分别为 $390.36/$97.96、$401.18/$58.77、$299.07/$58.87。稳健型主要压低亏损，平均盈利并未放大。费用会改变小额盈亏的分类，不能把净比率上升解释为费用有益。</p></section>
    <section><h2>两个候选实际做什么</h2><div class="grid"><div><h3>进取型 · 7.22 净美元盈亏比</h3><p>09:50 ET 固定入场，不使用入场指标门槛。EMA 5/21、当天 VWAP 判断失败；持仓 5 分钟后逆向 0.75 个入场 ATR 时软止损，若此前已向有利方向走出 0.5 ATR 则免除此项。6 ATR 跟踪盈利，15:45 发出退出决定。</p><p>胜率 16.13%，更依赖少量大盈利；不承诺其低胜率形态能长期延续。</p></div><div><h3>稳健型 · 5.13 净美元盈亏比</h3><p>09:40 后寻找前 5 分钟区间突破，EMA 5/21、RSI、3 分钟动量和同日量能确认。10:15 兜底，41 笔使用兜底。5 分钟后 0.5 ATR 软止损；有利移动 6 ATR 后按当前 ATR 跟踪，止损线只向有利方向移动；15:30 发出退出决定。</p><p>所有有效择时输入均为当天正股已完成 1 分钟 K 线；期权价格只用于执行与盈亏。</p></div></div><p class="muted">完整失败观察期、DTE 0 时钟、豁免和安全线参数见代码报告。ATR 是正股尺度，不代表可保证的期权最大亏损。两者均无固定止盈上限。</p></section>
    <section><h2>成交延迟与成本压力</h2>''' + pressure + '''<p>稳健型五种场景的三个时间块平均收益率均为正，最差 dual payoff 为 4.57；进取型最差为 3.31，个别压力情景的部分时间块为负。延迟偶尔改善训练结果，不表示应该主动延迟。</p><p class="muted">OHLCV 不含历史 bid/ask、队列与实际限价成交证据。滑点是情景假设，不能据此证明真实点差很小或必然成交。</p></section>
    <section><h2>探索规模与证据</h2><p>11 个入场族 × 6 套指标周期；开盘突破、回踩、VWAP 收复、Donchian、压缩扩张、量价脉冲、趋势回踩、自适应效率和固定时刻等。退出同时探索软止损、失败确认、浮盈豁免、DTE 时钟、跟踪宽度、结构和停滞退出。</p><p>13,145 个初始组合 + 1,276 个邻居 + 16,874 个第二阶段组合；31,295 个不同配置形成 23,918 种时间/退出原因路径。最终选择检查覆盖全部搜索集合：11,316 个主情景与分块合格配置中，2,949 个逐一压力回放，8,367 个由严格上界排除。</p><p>73 项测试通过。快速引擎和真实托管服务共 868 次重复 case 回放逐项对齐；两个冷启动结果一致，最终 CLI 的 124 笔再次完全一致。没有下真单。</p>
    <details><summary>13 项消融：哪些判断有帮助，哪些没有一致改善</summary>''' + ablations + '''</details><p>取消软止损可将稳健型净利润提高到 $3,916.76，但美元盈亏比降至 3.59。选择盈亏比优先是实际取舍，而非所有指标都同步改善。24 个有效参数约 ±20% 邻居均完成全部交易且主情景平均收益率/美元利润为正，dual payoff 3.89～5.19。</p></section>
    <section><h2>124 个 case：逐笔核对</h2><label>方案 <select id="variant"><option>v2 进取型</option><option>v2 稳健型</option><option>v1 基线</option></select></label><label>筛选 <input id="filter" placeholder="标的 / 日期 / 合约"></label><span id="count"></span><div class="scroll"><table><thead><tr><th>日期</th><th>标的</th><th>合约</th><th>方向</th><th>入场 ET</th><th>退出 ET</th><th>买价</th><th>卖价</th><th>毛盈亏 $</th><th>净盈亏 $</th><th>净收益率</th></tr></thead><tbody id="cases"></tbody></table></div><p class="muted">买卖价为加滑点前期权 OHLCV 模拟成交价。筛选只用于查看，不改变全量评价。</p></section>
    <section><h2>长期稳定性仍未证实</h2><p>两者剔除最佳单笔后仍盈利；剔除最佳三笔后进取型约亏 $1,450、稳健型约亏 $219。两者的按日期重采样训练内平均收益率 95% 区间都跨零，并且已经受参数选择影响。19 个日期上做三万余次搜索，选择偏差必须认真对待。</p><p>本轮结论：已有两种可复现的训练候选，值得在新数据上比较；不能宣称已找到长期稳定盈利方案。</p><p><a href="''' + branch + '''/research/custody_v2/README.md">完整规则、统计口径和复现命令</a> · <a href="''' + branch + '''">代码与全部搜索证据</a></p><p class="muted">训练 Release ZIP SHA256：<code>d72e190e6467f780782f809302a51f9dc8d6f72a00885c2dfb1aa96bf65c51f0</code></p></section></main>
    <script type="application/json" id="data">''' + json.dumps(data, ensure_ascii=False).replace('<','\\u003c') + '''</script><script>
    const data=JSON.parse(document.getElementById('data').textContent);
    function render(){const label=document.getElementById('variant').value,q=document.getElementById('filter').value.toLowerCase();const rows=data[label].filter(r=>r.slice(0,4).join(' ').toLowerCase().includes(q));const target=document.getElementById('cases');target.replaceChildren();for(const r of rows){const tr=document.createElement('tr');r.forEach((v,i)=>{const td=document.createElement('td');td.textContent=i===4||i===5?v.slice(11,16):i===10?(v*100).toFixed(2)+'%':i>=6?v.toFixed(2):v;tr.appendChild(td)});target.appendChild(tr)}document.getElementById('count').textContent=rows.length+' / 124 cases';}
    document.getElementById('variant').addEventListener('change',render);document.getElementById('filter').addEventListener('input',render);render();</script></html>'''
    target=Path(args.out);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(body)
    print(target)

if __name__ == '__main__':main()
