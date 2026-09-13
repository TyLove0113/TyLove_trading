# ─────────────────────────────────────────
# dashboard.py — Web Dashboard
# 提供網頁介面睇評分同新聞
# ─────────────────────────────────────────

from flask import Flask, jsonify, render_template
from flask_cors import CORS
from data_fetcher import get_all_stocks
from scoring import score_all_stocks
import config

app = Flask(__name__)
CORS(app)

# ── HTML 模板 ─────────────────────────────
HTML = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>港股交易系統</title>
    ```<user-input>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'SF Mono', 'Fira Code', monospace;
            background: #0D0D0D;
            color: #F7F3EA;
            min-height: 100vh;
        }
        
        /* Header */
        .header {
            background: #111;
            padding: 12px 20px;
            border-bottom: 1px solid #333;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 {
            font-size: 14px;
            letter-spacing: 0.1em;
            color: #F7F3EA;
        }
        .header .time {
            font-size: 12px;
            color: #666;
        }
        .status-dot {
            width: 8px; height: 8px;
            border-radius: 50%;
            background: #1E8E5A;
            display: inline-block;
            margin-right: 8px;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }
        
        /* Main content */
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 16px;
        }
        
        /* Section titles */
        .section-title {
            font-size: 11px;
            letter-spacing: 0.1em;
            color: #666;
            text-transform: uppercase;
            margin: 20px 0 10px;
        }
        
        /* Heat Matrix */
        .heat-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
            gap: 4px;
            margin-bottom: 20px;
        }
        .heat-cell {
            padding: 10px 8px;
            border-radius: 4px;
            text-align: center;
            cursor: default;
            transition: transform 0.15s;
        }
        .heat-cell:hover { transform: scale(1.05); }
        .heat-cell .symbol {
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.05em;
        }
        .heat-cell .change {
            font-size: 13px;
            font-weight: 700;
            margin-top: 2px;
        }
        .heat-cell .price {
            font-size: 10px;
            opacity: 0.7;
            margin-top: 2px;
        }
        
        /* Score Table */
        .score-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }
        .score-table th {
            text-align: left;
            padding: 8px 12px;
            border-bottom: 1px solid #333;
            color: #666;
            font-size: 10px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .score-table td {
            padding: 10px 12px;
            border-bottom: 1px solid #1a1a1a;
        }
        .score-table tr:hover td { background: #151515; }
        
        /* Score bar */
        .score-bar-bg {
            background: #222;
            border-radius: 2px;
            height: 6px;
            width: 100px;
            display: inline-block;
            vertical-align: middle;
        }
        .score-bar-fill {
            height: 100%;
            border-radius: 2px;
            transition: width 0.5s;
        }
        
        /* Grade badges */
        .grade {
            font-size: 10px;
            padding: 2px 8px;
            border-radius: 3px;
            font-weight: 600;
            letter-spacing: 0.05em;
        }
        .grade-green { background: rgba(30,142,90,0.2); color: #1E8E5A; }
        .grade-yellow { background: rgba(232,163,61,0.2); color: #E8A33D; }
        .grade-orange { background: rgba(232,163,61,0.15); color: #E8A33D; }
        .grade-red { background: rgba(193,39,45,0.2); color: #C1272D; }
        
        /* News section */
        .news-card {
            background: #111;
            border: 1px solid #222;
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 8px;
        }
        .news-card .stock-label {
            font-size: 11px;
            font-weight: 700;
            color: #E8A33D;
            letter-spacing: 0.05em;
            margin-bottom: 8px;
        }
        .news-item {
            font-size: 12px;
            color: #aaa;
            padding: 4px 0;
            border-bottom: 1px solid #1a1a1a;
            line-height: 1.4;
        }
        .news-item:last-child { border-bottom: none; }
        .news-sentiment {
            display: inline-block;
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 2px;
            margin-right: 6px;
        }
        .sent-pos { background: rgba(30,142,90,0.2); color: #1E8E5A; }
        .sent-neg { background: rgba(193,39,45,0.2); color: #C1272D; }
        .sent-neu { background: rgba(255,255,255,0.08); color: #666; }
        
        /* AI Summary */
        .ai-summary {
            background: rgba(59,91,165,0.1);
            border: 1px solid rgba(59,91,165,0.3);
            border-radius: 4px;
            padding: 8px 12px;
            margin-top: 8px;
            font-size: 12px;
            color: #9ab;
            line-height: 1.5;
        }
        
        /* Loading */
        .loading {
            text-align: center;
            padding: 60px;
            color: #444;
            font-size: 13px;
        }
        
        /* Refresh button */
        .refresh-btn {
            background: #1a1a1a;
            border: 1px solid #333;
            color: #aaa;
            padding: 6px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 11px;
            letter-spacing: 0.05em;
        }
        .refresh-btn:hover { background: #222; color: #F7F3EA; }
        
        /* Responsive */
        @media (max-width: 600px) {
            .heat-grid { grid-template-columns: repeat(3, 1fr); }
            .score-table { font-size: 11px; }
            .score-bar-bg { width: 60px; }
        }
        
        /* Uptime notice */
        .notice {
            font-size: 11px;
            color: #444;
            text-align: center;
            padding: 8px;
            margin-top: 20px;
        }
    </user-input>
</head>
<body>
    <div class="header">
        <h1><span class="status-dot"></span>港股日內交易系統</h1>
        <div>
            <span class="time" id="clock"></span>
            <button class="refresh-btn" onclick="loadData()" style="margin-left:12px">
                ↻ 更新
            </button>
        </div>
    </div>
    
    <div class="container">
        <div id="content">
            <div class="loading">載入數據中...</div>
        </div>
    </div>
    
    ```<user-input>
        // 時鐘
        function updateClock() {
            const now = new Date();
            document.getElementById('clock').textContent = 
                now.toLocaleTimeString('zh-HK', {
                    timeZone: 'Asia/Hong_Kong',
                    hour12: false
                });
        }
        setInterval(updateClock, 1000);
        updateClock();
        
        // 熱力顏色
        function heatColor(pct) {
            const abs = Math.abs(pct);
            if (pct > 0) {
                if (abs < 0.5) return {bg:'rgba(30,142,90,0.12)', text:'#1E8E5A'};
                if (abs < 1.5) return {bg:'rgba(30,142,90,0.28)', text:'#1E8E5A'};
                if (abs < 3.0) return {bg:'rgba(30,142,90,0.55)', text:'#0F6B40'};
                return {bg:'rgba(30,142,90,0.85)', text:'#F7F3EA'};
            } else if (pct < 0) {
                if (abs < 0.5) return {bg:'rgba(193,39,45,0.12)', text:'#C1272D'};
                if (abs < 1.5) return {bg:'rgba(193,39,45,0.28)', text:'#C1272D'};
                if (abs < 3.0) return {bg:'rgba(193,39,45,0.55)', text:'#8B0E13'};
                return {bg:'rgba(193,39,45,0.85)', text:'#F7F3EA'};
            }
            return {bg:'rgba(107,107,107,0.15)', text:'#666'};
        }
        
        // 評級樣式
        function gradeClass(total) {
            if (total >= 80) return 'grade-green';
            if (total >= 70) return 'grade-yellow';
            if (total >= 60) return 'grade-orange';
            return 'grade-red';
        }
        
        // 評分條顏色
        function barColor(score, max) {
            const pct = score / max;
            if (pct >= 0.8) return '#1E8E5A';
            if (pct >= 0.6) return '#E8A33D';
            return '#C1272D';
        }
        
        // 新聞情緒樣式
        function sentClass(sent) {
            if (sent === '正面' || sent === '略正面') return 'sent-pos';
            if (sent === '負面' || sent === '略負面') return 'sent-neg';
            return 'sent-neu';
        }
        
        // 載入數據
        async function loadData() {
            try {
                const res = await fetch('/api/analysis');
                const data = await res.json();
                renderDashboard(data);
            } catch(e) {
                document.getElementById('content').innerHTML = 
                    '<div class="loading">載入失敗，請重試</div>';
            }
        }
        
        // 渲染 Dashboard
        function renderDashboard(data) {
            const results = data.results;
            let html = '';
            
            // ── 熱力矩陣 ──
            html += '<div class="section-title">Watchlist 熱力矩陣</div>';
            html += '<div class="heat-grid">';
            results.forEach(r => {
                const c = heatColor(r.change_pct);
                const sign = r.change_pct >= 0 ? '+' : '';
                html += `
                    <div class="heat-cell" style="background:${c.bg}">
                        <div class="symbol" style="color:${c.text}">${r.symbol.replace('.HK','')}</div>
                        <div class="change" style="color:${c.text}">${sign}${r.change_pct.toFixed(2)}%</div>
                        <div class="price" style="color:${c.text}">${r.price.toFixed(2)}</div>
                    </div>`;
            });
            html += '</div>';
            
            // ── 評分排行 ──
            html += '<div class="section-title">評分排行（由高到低）</div>';
            html += `
                <table class="score-table">
                    <thead>
                        <tr>
                            <th>股票</th>
                            <th>總分</th>
                            <th>評級</th>
                            <th>技術</th>
                            <th>風險</th>
                            <th>新聞</th>
                            <th>建議</th>
                        </tr>
                    </thead>
                    <tbody>`;
            
            results.forEach(r => {
                const techPct = (r.technical.score / 50 * 100).toFixed(0);
                const riskPct = (r.risk.score / 20 * 100).toFixed(0);
                const newsPct = ((r.news?.score || 15) / 30 * 100).toFixed(0);
                
                html += `
                    <tr>
                        <td><strong>${r.symbol}</strong><br>
                            <span style="color:#666;font-size:10px">${r.price.toFixed(3)}</span>
                        </td>
                        <td>
                            <strong style="font-size:16px">${r.total}</strong>
                            <span style="color:#444;font-size:10px">/100</span>
                        </td>
                        <td><span class="grade ${gradeClass(r.total)}">${r.grade}</span></td>
                        <td>
                            ${r.technical.score}/50<br>
                            <div class="score-bar-bg">
                                <div class="score-bar-fill" style="width:${techPct}%;background:${barColor(r.technical.score,50)}"></div>
                            </div>
                        </td>
                        <td>
                            ${r.risk.score}/20<br>
                            <div class="score-bar-bg">
                                <div class="score-bar-fill" style="width:${riskPct}%;background:${barColor(r.risk.score,20)}"></div>
                            </div>
                        </td>
                        <td>
                            ${r.news?.score || 15}/30<br>
                            <div class="score-bar-bg">
                                <div class="score-bar-fill" style="width:${newsPct}%;background:${barColor(r.news?.score||15,30)}"></div>
                            </div>
                        </td>
                        <td style="color:#888;font-size:11px">${r.action}</td>
                    </tr>`;
            });
            html += '</tbody></table>';
            
            // ── 新聞列表 ──
            const withNews = results.filter(r => r.news?.headlines?.length > 0);
            if (withNews.length > 0) {
                html += '<div class="section-title">最新新聞</div>';
                withNews.forEach(r => {
                    const newsLabel = r.news?.label || '中性';
                    html += `
                        <div class="news-card">
                            <div class="stock-label">
                                ${r.symbol} 
                                <span class="news-sentiment ${sentClass(newsLabel)}">${newsLabel}</span>
                                <span style="color:#444;font-size:10px">${r.news.score}/30分</span>
                            </div>`;
                    
                    r.news.headlines.forEach(h => {
                        html += `<div class="news-item">• ${h}</div>`;
                    });
                    
                    if (r.news.summary) {
                        html += `<div class="ai-summary">🤖 ${r.news.summary}</div>`;
                    }
                    html += '</div>';
                });
            }
            
            // ── 更新時間 ──
            html += `<div class="notice">
                數據更新：${data.updated_at} HKT ｜ 
                yfinance 15分鐘延遲 ｜ 
                僅供參考
            </div>`;
            
            document.getElementById('content').innerHTML = html;
        }
        
        // 啟動
        loadData();
        // 每 5 分鐘自動更新
        setInterval(loadData, 300000);
    </user-input>
</body>
</html>
"""


# ── API 路由 ──────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analysis")
def api_analysis():
    try:
        import pytz
        from datetime import datetime
        hk_tz = pytz.timezone("Asia/Hong_Kong")
        now   = datetime.now(hk_tz).strftime("%Y-%m-%d %H:%M:%S")

        stocks  = get_all_stocks()
        results = score_all_stocks(stocks)

        # 轉換成 JSON 格式
        output = []
        for r in results:
            output.append({
                "symbol":     r["symbol"],
                "price":      r["price"],
                "change_pct": r["change_pct"],
                "total":      r["total"],
                "grade":      r["grade"],
                "action":     r["action"],
                "technical":  r["technical"],
                "risk":       r["risk"],
                "news": {
                    "score":     r.get("news", {}).get("score", 15),
                    "label":     r.get("news", {}).get("label", "中性"),
                    "headlines": r.get("news", {}).get("headlines", []),
                    "summary":   r.get("news", {}).get("summary", ""),
                },
            })

        return jsonify({
            "results":    output,
            "updated_at": now,
            "count":      len(output),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 啟動 ──────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)