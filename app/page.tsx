"use client";

import { useEffect, useMemo, useState } from "react";

type Page = "overview" | "forecast" | "history" | "research" | "system";
type Scalar = string | number | boolean | null;
type Row = Record<string, Scalar>;
type Group = { signal_date?: string; economic_signal_id?: string; signal_identity?: string; source?: string; research_status?: string; production_eligible?: boolean; horizons?: Row[]; constituent_variants?: Row[] };
type TargetView = {
  target_date?: Scalar;
  production_bullish_count?: Scalar;
  production_bearish_count?: Scalar;
  research_bullish_count?: Scalar;
  research_bearish_count?: Scalar;
  research_relative_stronger_count?: Scalar;
  research_relative_weaker_count?: Scalar;
  research_absolute_bullish_count?: Scalar;
  research_absolute_bearish_count?: Scalar;
  signals?: Row[];
  signal_groups?: Group[];
};
type DateView = {
  status?: Scalar;
  target_date?: Scalar;
  selected_trading_date?: Scalar;
  signal_date?: Scalar;
  production_bullish_count?: Scalar;
  production_bearish_count?: Scalar;
  research_bullish_count?: Scalar;
  research_bearish_count?: Scalar;
  unconfirmed_direction_count?: Scalar;
  signals?: Row[];
  signal_groups?: Group[];
  targets?: TargetView[];
  ledger_completeness?: Row;
  maturity_counts?: Row;
};
type Payload = {
  schema_version: string;
  provenance: Row;
  forecast_calendar: Row[];
  target_date_calendar: Row[];
  latest_signals: Row[];
  visible_signals?: Row[];
  historical_validation: Row[];
  research_evidence: Row[];
  system_health: Row;
  next_trading_day_forecast?: DateView;
  current_day_validation?: DateView;
  post_close_forward_signals?: DateView;
  signal_identity?: Row[];
};

const NAV: { id: Page; label: string; short: string }[] = [
  { id: "overview", label: "Overview", short: "Overview" },
  { id: "forecast", label: "Forecast", short: "Forecast" },
  { id: "history", label: "Validation", short: "History" },
  { id: "research", label: "Research Evidence", short: "Research" },
  { id: "system", label: "System Health", short: "System" },
];

const value = (item: Scalar | undefined) => item === null || item === undefined || item === "" ? "N/A" : String(item);
const pct = (item: Scalar | undefined, digits = 2) => typeof item === "number" ? `${item >= 0 ? "+" : ""}${(item * 100).toFixed(digits)}%` : "N/A";
const rate = (item: Scalar | undefined) => typeof item === "number" ? `${(item * 100).toFixed(1)}%` : "N/A";
const fdr = (item: Scalar | undefined) => typeof item === "number" ? (item < .001 ? item.toExponential(2) : item.toFixed(4)) : "N/A";
const sourceName = (item: Scalar | undefined) => value(item).replaceAll("_", " ").toUpperCase();
const num = (item: Scalar | undefined) => typeof item === "number" ? item : Number(item ?? 0) || 0;

function Chip({ children, tone = "neutral" }: { children: React.ReactNode; tone?: string }) { return <span className={`chip ${tone}`}>{children}</span>; }
function Meta({ label, value: shown }: { label: string; value: string }) { return <div className="meta"><span>{label}</span><strong>{shown}</strong></div>; }
function EmptyState({ title, children, code = "DATA_UNAVAILABLE" }: { title: string; children: React.ReactNode; code?: string }) { return <div className="empty"><div className="empty-code">{code}</div><h3>{title}</h3><p>{children}</p></div>; }
function Direction({ row }: { row: Row }) {
  const direction = row.evidence_scope === "relative"
    ? row.canonical_direction === "bullish" ? "相對偏強" : row.canonical_direction === "bearish" ? "相對偏弱" : "相對方向未確認"
    : value(row.direction_label ?? row.direction);
  const tone = direction.includes("多") ? "production" : direction.includes("空") ? "bearish" : "neutral";
  return <Chip tone={tone}>{direction}</Chip>;
}

function Header({ page, setPage, payload }: { page: Page; setPage: (page: Page) => void; payload: Payload | null }) {
  const state = payload ? value(payload.provenance.evaluation_result) : "DATA_UNAVAILABLE";
  return <><header className="topbar"><button className="brand" onClick={() => setPage("overview")}><span className="brand-mark">TW</span><span><small>QUANT RESEARCH DESK</small><strong>臺股市場溫度計</strong></span></button><div className="header-state"><span className={`status-orb ${payload ? "ok" : ""}`} /><span><small>CANONICAL ARTIFACT</small><strong>{state}</strong></span></div></header><aside className="side-nav"><div className="nav-label">FORECAST CONSOLE</div>{NAV.map((item, index) => <button key={item.id} onClick={() => setPage(item.id)} className={page === item.id ? "active" : ""}><span>{String(index + 1).padStart(2, "0")}</span>{item.label}</button>)}<div className="nav-foot">SSOT<br /><b>GitHub production payload</b><br />{payload ? value(payload.provenance.git_commit).slice(0, 8) : "unavailable"}</div></aside></>;
}

function Provenance({ payload }: { payload: Payload }) {
  const p = payload.provenance;
  return <section className="provenance"><Meta label="DATA DATE" value={value(p.data_date)} /><Meta label="STATUS" value={value(p.evaluation_result)} /><Meta label="FRESHNESS" value={value(p.freshness)} /><Meta label="RUN ID" value={value(p.run_id)} /><Meta label="GIT COMMIT" value={value(p.git_commit).slice(0, 12)} /></section>;
}

function SignalCard({ row }: { row: Row }) {
  const production = row.formal_vote_eligible === true;
  if (row.source === "options" && row.subject === "options_volume") row = { ...row, signal_identity: "選擇權成交量 PCR" };
  if (row.source === "options" && row.subject === "options_open_interest") row = { ...row, signal_identity: "選擇權未平倉量 PCR" };
  return <details className="signal-card"><summary><div><span className="kicker">{sourceName(row.source)} · {value(row.horizon)}</span><h3>{value(row.signal_identity ?? row.plain_definition)}</h3><small>{value(row.signal_date)} → {value(row.target_date)} · {value(row.research_status)}</small></div><div className="card-status"><Direction row={row} /><Chip tone={production ? "production" : "backfill"}>{production ? "PRODUCTION" : "RESEARCH"}</Chip></div></summary><div className="evidence-body">{row.research_flag && <div className="scope-note"><b>Parameter island</b><br />{value(row.research_warning)}</div>}<p className="plain">{value(row.metric_definition ?? row.plain_definition)}</p><div className="evidence-grid"><Meta label="SIGNAL DATE" value={value(row.signal_date)} /><Meta label="ENTRY DATE" value={value(row.entry_date)} /><Meta label="TARGET DATE" value={value(row.target_date)} /><Meta label="HORIZON" value={value(row.horizon)} /><Meta label="RAW VALUE" value={value(row.raw_value_display ?? row["指標原始值"])} /><Meta label="RAW UNIT" value={value(row.raw_unit)} /><Meta label="NORMALIZED" value={value(row.normalized_value)} /><Meta label="THRESHOLD" value={value(row.threshold)} /><Meta label="LOOKBACK" value={row.lookback_days === null ? "N/A" : `${value(row.lookback_days)} sessions`} /><Meta label="ROLLING WINDOW" value={row.rolling_window === null ? "N/A" : `${value(row.rolling_window)} sessions`} /><Meta label="BOUNDARY" value={value(row.boundary_inclusivity)} /><Meta label="MATCHED" value={value(row.matched)} /><Meta label="EVIDENCE SCOPE" value={value(row.evidence_scope)} /><Meta label="MEAN RETURN" value={value(row.historical_mean_return)} /><Meta label="MEDIAN RETURN" value={value(row.historical_median_return)} /><Meta label="RELATIVE MEAN" value={value(row.relative_mean_return)} /><Meta label="WIN RATE" value={value(row.historical_win_rate)} /><Meta label="SAMPLE N" value={value(row.sample_size)} /><Meta label="FAMILY FDR" value={value(row.family_fdr)} /><Meta label="GLOBAL FDR" value={value(row.global_fdr)} /><Meta label="EVIDENCE" value={`Level ${value(row.evidence_grade)}`} /><Meta label="RESEARCH FLAG" value={value(row.research_flag)} /></div><div className="mechanism"><p><span>FORMULA</span>{value(row.raw_metric_formula)}</p><p><span>MARKET MECHANISM</span>{value(row.market_mechanism)}</p><p><span>RISKS</span>{value(row.risks)}</p></div></div></details>;
}

function CountStrip({ view }: { view: DateView }) {
  const research = (view.signals ?? []).filter(row => row.formal_vote_eligible !== true);
  const relativeStrong = view.research_relative_stronger_count === undefined ? research.filter(row => row.evidence_scope === "relative" && row.canonical_direction === "bullish").length : num(view.research_relative_stronger_count);
  const relativeWeak = view.research_relative_weaker_count === undefined ? research.filter(row => row.evidence_scope === "relative" && row.canonical_direction === "bearish").length : num(view.research_relative_weaker_count);
  const otherResearch = view.research_absolute_bullish_count === undefined ? research.filter(row => row.evidence_scope !== "relative").length : num(view.research_absolute_bullish_count) + num(view.research_absolute_bearish_count) + num(view.unconfirmed_direction_count);
  return <div className="count-strip"><div><span>正式偏多</span><strong className="bull">{num(view.production_bullish_count)}</strong></div><div><span>正式偏空</span><strong className="bear">{num(view.production_bearish_count)}</strong></div><div><span>研究相對偏強</span><strong>{relativeStrong}</strong></div><div><span>研究相對偏弱</span><strong>{relativeWeak}</strong></div><div><span>其他研究證據</span><strong>{otherResearch}</strong></div></div>;
}

function ForecastSection({ payload, compact = false }: { payload: Payload; compact?: boolean }) {
  const view = payload.next_trading_day_forecast;
  if (!view) return <EmptyState title="等待 schema 1.1 production payload">目前 GitHub payload 尚未包含 next_trading_day_forecast；不以舊資料推算。</EmptyState>;
  const signals = view.signals ?? [], groups = view.signal_groups ?? [];
  return <section className="date-block"><div className="block-head"><div><span className="kicker">01 / NEXT TRADING DAY FORECAST</span><h2>下一交易日預測</h2><p>Target Date {value(view.target_date)} · headline 依 economic signal 去重；展開保留所有同時命中的 robustness variants。</p></div><Chip tone={view.status === "SIGNALS_PRESENT" ? "production" : view.status === "VALID_NO_SIGNAL" ? "active" : "unavailable"}>{value(view.status)}</Chip></div><CountStrip view={view} />{value(view.ledger_completeness?.status) !== "COMPLETE" && <div className="integrity-warning">{value(view.ledger_completeness?.warning)}</div>}{signals.length === 0 ? <EmptyState title="此目標日沒有命中訊號" code={value(view.status)}>沒有命中不等於市場中性；若 ledger 不完整，頁面會另外警示。</EmptyState> : <div className="target-stack">{groups.slice(0, compact ? 3 : groups.length).map((group, index) => <article className="target-panel" key={`${value(group.signal_date)}-${value(group.economic_signal_id)}-${index}`}><div className="target-title"><div><small>ECONOMIC SIGNAL</small><strong>{value(group.signal_identity)}</strong></div><span>{value(group.research_status)} · {group.production_eligible ? "PRODUCTION" : "RESEARCH"}</span></div><div className="signal-list">{(group.constituent_variants ?? group.horizons ?? []).map((row, rowIndex) => <SignalCard row={row} key={`${value(row.signal_id)}-${value(row.target_date)}-${rowIndex}`} />)}</div></article>)}</div>}</section>;
}

function ValidationSection({ payload, compact = false }: { payload: Payload; compact?: boolean }) {
  const view = payload.current_day_validation;
  if (!view) return <EmptyState title="等待 schema 1.1 validation view">目前 payload 尚未包含 current_day_validation；不以當日漲跌代替 O1→Cn。</EmptyState>;
  const rows = view.signals ?? [];
  return <section className="date-block"><div className="block-head"><div><span className="kicker">02 / CURRENT TRADING DAY VALIDATION</span><h2>當個交易日驗證</h2><p>Target Date {value(view.selected_trading_date)} · Actual Return 永遠是 0050 adjusted O1→Cn。</p></div><Chip tone={view.status === "AVAILABLE" ? "production" : view.status === "VALID_NO_SIGNAL" ? "active" : "unavailable"}>{value(view.status)}</Chip></div>{rows.length === 0 ? <EmptyState title="此交易日沒有可顯示的正式驗證" code={value(view.status)}>沒有事件與 validation 不完整是不同狀態。</EmptyState> : <div className="history-list">{rows.slice(0, compact ? 3 : rows.length).map((row, index) => <details className="history-card" key={`${value(row.signal_date)}-${value(row.signal_id)}-${index}`}><summary><div><span className="kicker">{sourceName(row.source)} · {value(row.signal_date)}</span><h3>{value(row.signal_identity ?? row.signal_id)}</h3><small>{value(row.event_origin)} · {value(row.maturity)} · O1→C{value(row.horizon)}</small></div><Chip tone={row.outcome === "HIT" ? "production" : row.outcome === "MISS" ? "bearish" : "neutral"}>{value(row.outcome)}</Chip></summary><div className="history-body"><Meta label="DIRECTION" value={value(row.direction_label)} /><Meta label="ENTRY" value={value(row.entry_date)} /><Meta label="TARGET" value={value(row.target_date)} /><Meta label="ACTUAL RETURN" value={pct(row.actual_return)} /><Meta label="DIRECTIONAL RETURN" value={pct(row.directional_return)} /><Meta label="RESEARCH STATUS" value={value(row.research_status)} /></div></details>)}</div>}</section>;
}

function PostCloseSection({ payload, compact = false }: { payload: Payload; compact?: boolean }) {
  const view = payload.post_close_forward_signals;
  if (!view) return <EmptyState title="等待 schema 1.1 post-close view">目前 payload 尚未包含 post_close_forward_signals；不從 latest snapshot 自行推算。</EmptyState>;
  const groups = view.signal_groups ?? [];
  return <section className="date-block"><div className="block-head"><div><span className="kicker">03 / POST-CLOSE FORWARD SIGNALS</span><h2>當天收盤訊號指向未來</h2><p>Signal Date {value(view.signal_date)} · 同一 economic signal 只計一份 headline 證據；展開可追溯所有同時命中的 window / horizon variants。</p></div><Chip tone={view.status === "SIGNALS_PRESENT" ? "production" : "active"}>{value(view.status)}</Chip></div><CountStrip view={view} />{groups.length === 0 ? <EmptyState title="本次沒有命中的 forward signals" code="VALID_NO_SIGNAL">系統不建立不存在的 horizon event。</EmptyState> : <div className="target-stack">{groups.slice(0, compact ? 2 : groups.length).map((group, index) => <article className="target-panel" key={`${value(group.signal_date)}-${value(group.economic_signal_id)}-${index}`}><div className="target-title"><div><small>ECONOMIC SIGNAL</small><strong>{value(group.signal_identity)}</strong></div><span>{value(group.research_status)} · {group.production_eligible ? "PRODUCTION" : "RESEARCH"}</span></div><div className="signal-list">{(group.constituent_variants ?? group.horizons ?? []).map((row, rowIndex) => <SignalCard row={row} key={`${value(row.signal_id)}-${value(row.target_date)}-${rowIndex}`} />)}</div></article>)}</div>}</section>;
}

function Overview({ payload, setPage }: { payload: Payload; setPage: (page: Page) => void }) {
  return <div className="view"><div className="title-row"><div><span className="kicker">OVERVIEW / FORECAST-CENTRIC MONITOR</span><h1>最新研究狀態</h1><p>GitHub 定義研究與計算；Work 只呈現 production publication。</p></div><Chip tone="production">{value(payload.provenance.evaluation_result)}</Chip></div><Provenance payload={payload} /><ForecastSection payload={payload} compact /><ValidationSection payload={payload} compact /><PostCloseSection payload={payload} compact /><div className="two-col"><button className="portal-card" onClick={() => setPage("forecast")}><span className="kicker">FULL FORECAST VIEW</span><h3>展開全部訊號</h3><p>依 target date、economic signal 與 horizon 查看。</p><b>開啟 Forecast →</b></button><button className="portal-card" onClick={() => setPage("research")}><span className="kicker">CANONICAL REGISTRY</span><h3>Research Evidence</h3><p>{payload.research_evidence.length} 個研究定義；Evidence A 不等於正式啟用。</p><b>檢視證據 →</b></button></div><section className="contract"><span className="kicker">READING CONTRACT</span><div><p><b>Matched ≠ Production</b><br />是否命中、能否正式計票、事後 Outcome 是三個不同狀態。</p><p><b>VALID_NO_SIGNAL ≠ DATA_UNAVAILABLE</b><br />前者資料有效但未命中；後者不能判讀。</p><p><b>票數不是機率</b><br />FDR 與歷史勝率都不是本次命中機率。</p></div></section></div>;
}

function Forecast({ payload }: { payload: Payload }) { return <div className="view"><div className="title-row"><div><span className="kicker">FORECAST / SIGNAL LEDGER</span><h1>預測日期視角</h1><p>同一 economic signal 的多個 horizon 以群組呈現，但每筆 event 仍各自保留。</p></div><Chip tone="active">SCHEMA {payload.schema_version}</Chip></div><ForecastSection payload={payload} /><PostCloseSection payload={payload} /></div>; }

function History({ payload }: { payload: Payload }) {
  const counts = (origin: string) => payload.historical_validation.filter(row => row.event_origin === origin && row.maturity === "MATURED").length;
  return <div className="view"><div className="title-row"><div><span className="kicker">HISTORICAL VALIDATION</span><h1>逐筆預測驗證</h1><p>BACKFILL 與 PRODUCTION 分開；不顯示未定義的總勝率。</p></div><Chip tone="production">CANONICAL</Chip></div><div className="formula-grid"><div><span>ACTUAL RETURN</span><strong>0050 adjusted O1 → Cn</strong><p>市場本身的實現報酬。</p></div><div><span>DIRECTIONAL RETURN</span><strong>Bullish = Actual<br />Bearish = −Actual</strong><p>方向調整不改寫 Actual Return。</p></div><div><span>OUTCOME</span><strong>&gt; 0 HIT · &lt; 0 MISS · = 0 FLAT</strong><p>PENDING / DATA_UNAVAILABLE 維持 N/A。</p></div></div><section className="origin-split"><div><Chip tone="backfill">BACKFILL</Chip><h3>Matured {counts("BACKFILL")}</h3><p>歷史 as-of reconstruction，不是 prospective track record。</p></div><div><Chip tone="production">PRODUCTION</Chip><h3>Matured {counts("PRODUCTION")}</h3><p>實際 production system 當時產生的 forecast。</p></div></section><ValidationSection payload={payload} /><div className="history-list all-history">{[...payload.historical_validation].reverse().map((row, index) => <details className="history-card" key={`${value(row.signal_date)}-${value(row.signal_id)}-${index}`}><summary><div><span className="kicker">{sourceName(row.source)} · {value(row.signal_date)}</span><h3>{value(row.signal_id)}</h3><small>{value(row.event_origin)} · {value(row.maturity)}</small></div><Chip tone={row.outcome === "HIT" ? "production" : row.outcome === "MISS" ? "bearish" : "neutral"}>{value(row.outcome)}</Chip></summary><div className="history-body"><Meta label="DIRECTION" value={row.direction === "bullish" ? "偏多" : row.direction === "bearish" ? "偏空" : value(row.direction)} /><Meta label="HORIZON" value={`C${value(row.horizon)}`} /><Meta label="ENTRY" value={value(row.entry_date)} /><Meta label="TARGET" value={value(row.target_date)} /><Meta label="ACTUAL RETURN" value={pct(row.actual_return)} /><Meta label="DIRECTIONAL RETURN" value={pct(row.directional_return)} /></div></details>)}</div></div>;
}

function EvidenceCard({ item }: { item: Row }) {
  const status = value(item.research_status), eligible = status === "RETAINED" && item.activation_ready === true && item.evidence_scope !== "relative", conditional = item.direction_mode === "conditional";
  const direction = conditional ? "條件式" : item.evidence_scope === "relative" ? (item.direction === "bullish" ? "相對偏強" : "相對偏弱") : item.direction === "bullish" ? "偏多" : "偏空";
  return <details className="evidence-card"><summary><div className="evidence-id"><span>{sourceName(item.source)} · C{value(item.horizon)}</span><h3>{value(item.plain_definition)}</h3><small>{value(item.signal_id)}</small></div><div className="traffic"><span className={`traffic-dot ${status.toLowerCase()}`} /><b>{status}</b>{!eligible && <em>NOT PRODUCTION-ELIGIBLE</em>}</div></summary><div className="evidence-body">{conditional && <div className="scope-note">Margin / Short 的報酬與勝率是 PR95 flow-group 描述統計；FDR / Evidence Level 是 FlowHigh × continuous Prior5D interaction 檢定。</div>}{item.research_flag && <div className="scope-note"><b>Parameter island</b><br />此結果在鄰近參數未呈現一致支持，穩健性較弱。</div>}<div className="evidence-grid"><Meta label="DIRECTION MODE" value={value(item.direction_mode)} /><Meta label="CANONICAL DIRECTION" value={direction} /><Meta label="INTERACTION SIGN" value={value(item.interaction_sign)} /><Meta label="EVIDENCE SCOPE" value={value(item.evidence_scope)} /><Meta label="THRESHOLD LOWER" value={value(item.threshold_lower)} /><Meta label="THRESHOLD UPPER" value={value(item.threshold_upper)} /><Meta label="NORMALIZATION" value={value(item.normalization)} /><Meta label="ACTIVATION READY" value={value(item.activation_ready)} /><Meta label="MEAN RETURN" value={pct(item.mean_return)} /><Meta label="MEDIAN RETURN" value={pct(item.median_return)} /><Meta label="WIN RATE" value={rate(item.win_rate)} /><Meta label="SAMPLE N" value={value(item.sample_size)} /><Meta label="RELATIVE MEAN" value={pct(item.relative_mean_return)} /><Meta label="FAMILY FDR" value={fdr(item.family_fdr)} /><Meta label="GLOBAL FDR" value={fdr(item.global_fdr)} /><Meta label="EVIDENCE" value={`Level ${value(item.evidence_grade)}`} /><Meta label="RESEARCH FLAG" value={value(item.research_flag)} /></div><div className="mechanism"><p><span>FORMULA</span>{value(item.formula)}</p><p><span>MARKET MECHANISM</span>{value(item.market_mechanism)}</p><p><span>RISKS</span>{value(item.risks)}</p></div></div></details>;
}

function Research({ payload }: { payload: Payload }) {
  const [filter, setFilter] = useState("ALL");
  const shown = useMemo(() => payload.research_evidence.filter(item => filter === "ALL" || item.research_status === filter), [payload, filter]);
  const count = (status: string) => payload.research_evidence.filter(item => item.research_status === status).length;
  return <div className="view"><div className="title-row"><div><span className="kicker">RESEARCH EVIDENCE</span><h1>Canonical signal registry</h1><p>只呈現 registry 已發布的 evidence；不因當日 HIT / MISS 改定義。</p></div><div className="commit-tag">RUN COMMIT<br /><b>{value(payload.provenance.git_commit).slice(0, 12)}</b></div></div><section className="traffic-summary">{["RETAINED", "RETEST", "REJECTED"].map(status => <button key={status} onClick={() => setFilter(status)} className={filter === status ? "selected" : ""}><span className={`traffic-dot ${status.toLowerCase()}`} /><b>{count(status)}</b><small>{status}</small></button>)}</section><div className="filter-row"><button onClick={() => setFilter("ALL")} className={filter === "ALL" ? "active" : ""}>ALL {payload.research_evidence.length}</button><span>publication payload · schema {payload.schema_version}</span></div><div className="evidence-list">{shown.map((item, index) => <EvidenceCard key={`${value(item.signal_id)}-${index}`} item={item} />)}</div></div>;
}

function System({ payload }: { payload: Payload }) {
  const p = payload.provenance, h = payload.system_health;
  return <div className="view"><div className="title-row"><div><span className="kicker">SYSTEM / RUN HEALTH</span><h1>資料與執行健康度</h1><p>Calculation 與 Publication 分開顯示。</p></div><Chip tone="production">{value(h.publication_status)}</Chip></div><section className="health-grid"><Meta label="DATA DATE" value={value(p.data_date)} /><Meta label="CALCULATION TIME" value={value(p.calculation_timestamp)} /><Meta label="RUN ID" value={value(p.run_id)} /><Meta label="RUN GIT COMMIT" value={value(p.git_commit)} /><Meta label="CALCULATION" value={value(h.calculation_status)} /><Meta label="PUBLICATION" value={value(h.publication_status)} /><Meta label="EVALUATION" value={value(p.evaluation_result)} /><Meta label="FRESHNESS" value={value(p.freshness)} /><Meta label="ARTIFACT" value={value(h.artifact_availability)} /><Meta label="CHECKSUMS" value={h.artifact_checksums_valid === true ? "VALID" : "UNKNOWN"} /><Meta label="SOURCE" value={value(h.source_type)} /><Meta label="GENERATED AT" value={value(p.generated_at)} /></section><section className="health-contract"><h3>狀態字典</h3>{[["SUCCESS", "必要 stages 完成且 manifest 驗證通過。"], ["VALID_NO_SIGNAL", "資料有效但沒有命中，不是中性訊號。"], ["DATA_UNAVAILABLE", "必要資料缺失；不能顯示成 0 多 / 0 空。"], ["VALIDATION_INCOMPLETE", "應有 forecast 未完整出現在 validation artifact。"], ["STALE", "資料日期落後預期交易日。"]].map(([a, b]) => <div key={a}><Chip tone={a === "SUCCESS" ? "production" : a === "VALID_NO_SIGNAL" ? "active" : "neutral"}>{a}</Chip><p>{b}</p></div>)}</section></div>;
}

function Unavailable({ message }: { message: string }) { return <div className="view"><div className="title-row"><div><span className="kicker">PUBLICATION HANDOFF</span><h1>Production data unavailable</h1><p>Work Site 不會用舊 snapshot 或 0 值取代缺少的 artifact。</p></div><Chip tone="unavailable">DATA_UNAVAILABLE</Chip></div><EmptyState title="無法讀取已驗證的 publication payload">{message}</EmptyState></div>; }

export default function Home() {
  const [page, setPage] = useState<Page>("overview"), [payload, setPayload] = useState<Payload | null>(null), [error, setError] = useState("正在讀取 GitHub production publication payload…");
  useEffect(() => { fetch("/api/market-data", { cache: "no-store" }).then(async response => { if (!response.ok) throw new Error(`publication endpoint ${response.status}`); return response.json(); }).then(data => { setPayload(data); setError(""); }).catch(reason => setError(reason instanceof Error ? reason.message : "publication payload unavailable")); }, []);
  return <main><Header page={page} setPage={setPage} payload={payload} /><div className="content">{!payload ? <Unavailable message={error} /> : <>{page === "overview" && <Overview payload={payload} setPage={setPage} />}{page === "forecast" && <Forecast payload={payload} />}{page === "history" && <History payload={payload} />}{page === "research" && <Research payload={payload} />}{page === "system" && <System payload={payload} />}</>}</div><nav className="bottom-nav">{NAV.map(item => <button key={item.id} onClick={() => setPage(item.id)} className={page === item.id ? "active" : ""}><span>{item.id === "overview" ? "⌂" : item.id === "forecast" ? "□" : item.id === "history" ? "✓" : item.id === "research" ? "A" : ">_"}</span>{item.short}</button>)}</nav><footer><span>TAIWAN MARKET THERMOMETER · PRESENTATION LAYER</span><span>Research monitoring only · not investment advice</span></footer></main>;
}
