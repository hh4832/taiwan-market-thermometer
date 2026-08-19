"use client";

import { useMemo, useState } from "react";

const breadthSeries = [39, 24, 59, 35, 23, 48, 86, 59, 59, 70, 81, 17];
const futuresSeries = [44, 58, 31, 67, 73, 46, 62, 29, 54, 77, 38, 64];

function Sparkline({ values, tone }: { values: number[]; tone: "cyan" | "amber" }) {
  const points = useMemo(() => values.map((value, index) => `${(index / (values.length - 1)) * 620},${150 - (value / 100) * 150}`).join(" "), [values]);
  return (
    <svg className="sparkline" viewBox="0 0 620 150" role="img" aria-label="最近交易日溫度走勢">
      <line x1="0" y1="30" x2="620" y2="30" className="guide guide-high" />
      <line x1="0" y1="120" x2="620" y2="120" className="guide" />
      <polyline points={points} className={`spark-path ${tone}`} />
      {values.map((value, index) => <circle key={`${value}-${index}`} cx={(index / (values.length - 1)) * 620} cy={150 - (value / 100) * 150} r="4" className={`spark-dot ${tone}`} />)}
    </svg>
  );
}

function Gauge({ score, tone, label }: { score: number | null; tone: "cyan" | "amber"; label: string }) {
  return (
    <div className={`gauge ${tone}`} style={{ "--score": `${(score ?? 0) * 3.6}deg` } as React.CSSProperties}>
      <div className="gauge-center"><span>{label}</span><strong>{score === null ? "—" : Math.round(score)}</strong><small>/ 100</small></div>
    </div>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>;
}

export default function Home() {
  const [refreshing, setRefreshing] = useState(false);
  const [showMethod, setShowMethod] = useState(false);
  const simulateRefresh = () => { setRefreshing(true); window.setTimeout(() => setRefreshing(false), 900); };

  return (
    <main>
      <header className="topbar">
        <div className="brand"><span className="brand-mark">TW</span><div><p>QUANT RESEARCH DESK</p><h1>臺股市場溫度計</h1></div></div>
        <div className="top-actions">
          <div className="data-date"><span className="status-dot" /><div><small>參考資料日期</small><strong>2026-07-15</strong></div></div>
          <button onClick={simulateRefresh} disabled={refreshing} className="refresh-button">{refreshing ? "正在檢查…" : "更新 FinLab 資料"}</button>
        </div>
      </header>

      <div className="workspace">
        <section className="notice"><span>LOCAL FIRST</span><p>目前呈現已上傳的研究快照。放入本機Python專案後，按更新即可改由FinLab取得最新資料。</p></section>

        <section className="hero-grid">
          <article className="verdict-card">
            <div className="section-kicker">COMBINED VIEW</div>
            <div className="verdict-head"><div><span className="eyebrow">綜合判讀</span><h2>資料日期尚未對齊</h2></div><span className="state-chip neutral">暫不判讀</span></div>
            <p className="verdict-copy">市場廣度已有研究快照，但外資期貨每日原始部位尚待本機FinLab更新。系統不會把不同日期或缺值訊號強行合併。</p>
            <div className="logic-row">
              <div><span>市場廣度</span><strong className="positive">廣度強勁</strong><small>反彈溫度僅 3</small></div><div className="logic-arrow">＋</div>
              <div><span>外資期貨</span><strong>等待資料</strong><small>需要同一交易日</small></div><div className="logic-arrow">＝</div>
              <div><span>參考狀態</span><strong>資料不足</strong><small>不產生方向建議</small></div>
            </div>
            <div className="warning-line">統計研究結果整理，不構成投資建議。</div>
          </article>
          <aside className="system-card">
            <div className="section-kicker">SYSTEM STATUS</div><h3>資料狀態</h3>
            <dl>
              <div><dt>市場廣度</dt><dd><span className="status-dot" />2026-07-15</dd></div>
              <div><dt>外資期貨</dt><dd className="muted"><span className="status-dot pending" />等待FinLab</dd></div>
              <div><dt>最後共同日</dt><dd>—</dd></div><div><dt>資料來源</dt><dd>研究快照</dd></div>
            </dl>
          </aside>
        </section>

        <section className="temperature-grid">
          <article className="temperature-card breadth">
            <div className="card-title-row"><div><span className="eyebrow">5–10日環境指標</span><h2>超跌反彈溫度</h2></div><span className="state-chip positive">市場廣度強勁</span></div>
            <div className="gauge-row">
              <Gauge score={3.13} tone="cyan" label="反彈溫度" />
              <div className="score-explain"><strong>目前沒有超跌反彈條件</strong><p>下跌家數只占有效漲跌股票的17.0%，位於歷史較低區間。</p>
                <div className="thresholds"><span>一般</span><span>最差20%</span><span>最差5%</span></div><div className="threshold-bar"><i style={{ width: "17%" }} /></div>
                <div className="threshold-values"><span>0%</span><span>68.55%</span><span>84.54%</span><span>100%</span></div>
              </div>
            </div>
            <div className="chart-title"><span>近期下跌比例百分位</span><small>最後12個可用交易日</small></div><Sparkline values={breadthSeries} tone="cyan" />
            <details><summary>查看市場廣度 Raw 資訊</summary><div className="metric-grid">
              <Metric label="上漲家數" value="1,527" /><Metric label="下跌家數" value="312" /><Metric label="平盤家數" value="103" />
              <Metric label="有效股票數" value="1,942" detail="股票池 1,974" /><Metric label="資料覆蓋率" value="98.38%" /><Metric label="下跌比例" value="16.97%" />
              <Metric label="上漲比例" value="83.03%" /><Metric label="Breadth Net Ratio" value="+0.6607" />
            </div></details>
          </article>

          <article className="temperature-card futures">
            <div className="card-title-row"><div><span className="eyebrow">隔日為主 · 極端空方延伸至20日</span><h2>外資期貨方向溫度</h2></div><span className="state-chip neutral">等待更新</span></div>
            <div className="gauge-row"><Gauge score={null} tone="amber" label="方向溫度" />
              <div className="score-explain empty-state"><strong>需要本機FinLab原始資料</strong><p>更新後會顯示OI Change Ratio的252日無前視百分位。一般分數主要判讀隔日；只有最低5%的極端往空方調整具有延伸至5～20日的負向統計證據。</p>
                <ul><li>0–20：明顯往空方移動</li><li>40–60：中性</li><li>80–100：明顯往多方移動</li></ul>
              </div>
            </div>
            <div className="chart-title"><span>介面示意</span><small>連線後顯示最近60日</small></div><Sparkline values={futuresSeries} tone="amber" />
            <details><summary>查看外資期貨 Raw 欄位</summary><div className="metric-grid placeholder-grid">
              {["今日多方OI", "今日空方OI", "今日淨OI", "淨部位日變化", "Long Change Ratio", "Short Change Ratio", "OI Ratio", "OI Change Ratio"].map((label) => <Metric key={label} label={label} value="—" detail="等待FinLab" />)}
            </div></details>
          </article>
        </section>

        <section className="research-strip">
          <div><span className="section-kicker">MARKET BREADTH RESEARCH ANCHOR</span><h2>極端普跌後的歷史反彈傾向</h2></div>
          <div className="research-numbers"><Metric label="極端5%樣本" value="189次" /><Metric label="隔日開盤→第5日" value="+0.57%" detail="勝率 56.1%" /><Metric label="隔日開盤→第10日" value="+0.88%" detail="勝率 63.1%" /><Metric label="證據定位" value="臨界" detail="非單獨買進訊號" /></div>
        </section>

        <section className="research-strip">
          <div><span className="section-kicker">FOREIGN FUTURES RESEARCH ANCHOR</span><h2>極端往空方調整後的負向差異</h2><p>固定條件：OI Change Ratio的252日無前視百分位最低5%。未觸發時，以下數字僅為研究背景。</p></div>
          <div><div className="research-numbers"><Metric label="極端空方樣本" value="239次" detail="PR 0～5" /><Metric label="未來1日" value="−0.33%" detail="相對其他日 −0.41%" /><Metric label="未來5日" value="−0.41%" detail="相對其他日 −0.77%" /><Metric label="未來10日" value="−0.66%" detail="相對其他日 −1.36%" /><Metric label="未來20日" value="−0.30%" detail="相對其他日 −1.67%" /></div><p>法人資料於d0盤後公布；上述為統計報酬，不等同可實現策略報酬。</p></div>
        </section>

        <section className="research-strip">
          <div><span className="section-kicker">LONG / SHORT CHANGE DECOMPOSITION</span><h2>外資多空部位變化拆解</h2><p>FDR校正後的結果作為主要判定；未通過者不視為已確認的預測訊號。</p></div>
          <div><div className="research-numbers">
            <Metric label="未來1日" value="多單增加偏正向" detail="+0.23%；相對 +0.19%；FDR .030" />
            <Metric label="未來5日" value="尚未確認" detail="多單、空單皆未通過FDR" />
            <Metric label="未來10日" value="多單減少後偏弱" detail="相對 −0.63%；FDR .005" />
            <Metric label="未來20日" value="尚未確認" detail="多單、空單皆未通過FDR" />
          </div><p>空單變化單獨看，在1、5、10、20日皆未通過FDR。整體OI Change Ratio的負向證據不等於「新增空單本身」；目前較穩健的拆解證據來自多單變化。</p></div>
        </section>

        <section className="method-section">
          <button className="method-toggle" onClick={() => setShowMethod(!showMethod)} aria-expanded={showMethod}><span>方法、公式與研究限制</span><strong>{showMethod ? "收合 −" : "展開 ＋"}</strong></button>
          {showMethod && <div className="method-grid">
            <article><h3>市場廣度</h3><code>(上漲家數 − 下跌家數) / (上漲家數 ＋ 下跌家數)</code><p>即時分數只和當日以前資料比較；完整樣本門檻僅作研究重現。</p></article>
            <article><h3>外資期貨</h3><code>ΔNet OI / (前日Long OI ＋ 前日Short OI)</code><p>一般分數主要作為隔日方向參考；只有最低5%的極端空方調整具有延伸至5～20日的負向統計證據。法人資料盤後公布，研究報酬不等同可交易報酬。</p></article>
            <article><h3>資料保護</h3><p>FinLab Token只存在本機。不同資料日期、樣本不足或缺值時，系統停止方向判讀。</p></article>
          </div>}
        </section>
      </div>
      <footer><span>TAIWAN MARKET THERMOMETER · v1.3.2</span><span>研究快照 · Standalone local dashboard</span></footer>
    </main>
  );
}
