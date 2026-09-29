from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
import numpy as np
import pandas as pd
from .scoring import safe_divide

TURNOVER_TABLE="market_transaction_info:成交金額"; BUY_TABLE="institutional_investors_trading_all_market_summary:買進金額"
SELL_TABLE="institutional_investors_trading_all_market_summary:賣出金額"; NET_TABLE="institutional_investors_trading_all_market_summary:買賣超"
LISTED_FOREIGN="上市外資及陸資(不含外資自營商)"; LISTED_FOREIGN_FULLWIDTH="上市外資及陸資（不含外資自營商）"
LISTED_DEALER_SELF="上市自營商(自行買賣)"; LISTED_DEALER_HEDGE="上市自營商(避險)"
OTC_FOREIGN="上櫃外資及陸資(不含自營商)"; OTC_FOREIGN_FULLWIDTH="上櫃外資及陸資（不含自營商）"
OTC_DEALER_SELF="上櫃自營商(自行買賣)"; OTC_DEALER_HEDGE="上櫃自營商(避險)"; OTC_TOTAL="上櫃三大法人合計*"

@dataclass(frozen=True)
class SpotEvidence:
    family:str; economic_signal_id:str; trigger_id:str; label:str; direction:str; horizon:str; evidence_grade:str
    a_grade_status:str; current_value:float|None; normalized_value:float|None; normalization:str; reference_window:int
    accumulation_days:int; threshold_label:str; raw_buy_amount:float|None; raw_sell_amount:float|None
    market_turnover:float|None; historical_result:str; plain_explanation:str; evidence_scope:str="absolute"
    data_quality:str="pass"; quality_flags:tuple[str,...]=(); research_only:bool=True
    @property
    def percentile(self): return self.normalized_value if self.normalization=="pr" else None
    @property
    def percentile_lower(self): return float("nan")
    @property
    def percentile_upper(self): return float("nan")
    @property
    def evidence_statement(self): return self.historical_result
    def as_record(self,data_date:str,recorded_at_taipei:str="",version:str="",git_commit:str="")->dict[str,Any]:
        market="otc" if self.family.startswith("otc_") else ("combined" if self.family.startswith("combined_") else "listed")
        institution="foreign" if "foreign" in self.family else ("dealer" if "dealer" in self.family else "total_institutional")
        metric="buy" if "buy" in self.trigger_id else ("sell" if "sell" in self.trigger_id else "net")
        r=asdict(self); r.update(data_date=data_date,recorded_at_taipei=recorded_at_taipei,market=market,
          institution=institution,metric=metric,percentile=self.percentile,evidence_statement=self.historical_result,
          quality_flags=";".join(self.quality_flags),version=version,git_commit=git_commit); return r

@dataclass(frozen=True)
class SpotFlowReport:
    data_date:str; evidence:tuple[SpotEvidence,...]; family_state:str; bullish_family_count:int; bearish_family_count:int
    mixed_family_count:int; data_quality:str; research_only:bool=True
    canonical_metrics:dict[str,pd.Series]|None=None

def _frame(x,name):
    f=pd.DataFrame(x).copy(); d=pd.to_datetime(f.index,errors="coerce"); f=f.loc[~pd.isna(d)]; f.index=d[~pd.isna(d)]
    if f.empty: raise RuntimeError(f"FinLab資料表為空或沒有有效日期：{name}")
    return f.loc[~f.index.duplicated(keep="last")].sort_index()
def _col(f,names,table):
    for n in names:
        if n in f.columns:return pd.to_numeric(f[n],errors="coerce")
    raise RuntimeError(f"{table}缺少精確欄位：{' 或 '.join(names)}")
def _optional_col(f,names):
    for n in names:
        if n in f.columns:return pd.to_numeric(f[n],errors="coerce")
    return pd.Series(np.nan,index=f.index,dtype=float)
def _ratio(x,t,d): return safe_divide(x.rolling(d,min_periods=d).sum(),t.rolling(d,min_periods=d).sum())
def nonoverlapping_flow_change(level,days): return pd.to_numeric(level,errors="coerce")-pd.to_numeric(level,errors="coerce").shift(days)
def _pr(s,w):
    def rank(a):
        v=a[np.isfinite(a)]; return float(np.mean(v<=a[-1])*100) if np.isfinite(a[-1]) and len(v)==w else np.nan
    return pd.to_numeric(s,errors="coerce").rolling(w,min_periods=w).apply(rank,raw=True)
def _z(s,w):
    s=pd.to_numeric(s,errors="coerce"); return safe_divide(s-s.rolling(w,min_periods=w).mean(),s.rolling(w,min_periods=w).std(ddof=0))
def _finite(v):
    try:n=float(v)
    except (TypeError,ValueError):return None
    return n if np.isfinite(n) else None

def build_spot_flow_report(inst_buy,inst_sell,inst_net,market_amount):
    buy,sell,net,turn=(_frame(inst_buy,BUY_TABLE),_frame(inst_sell,SELL_TABLE),_frame(inst_net,NET_TABLE),_frame(market_amount,TURNOVER_TABLE))
    idx=buy.index.intersection(sell.index).intersection(net.index).intersection(turn.index)
    if idx.empty: raise RuntimeError("法人現貨與市場成交金額沒有共同交易日")
    buy,sell,net,turn=buy.loc[idx],sell.loc[idx],net.loc[idx],turn.loc[idx]
    lt,ot=_col(turn,("TAIEX",),TURNOVER_TABLE),_col(turn,("OTC",),TURNOVER_TABLE)
    lf=(_col(buy,(LISTED_FOREIGN,LISTED_FOREIGN_FULLWIDTH),BUY_TABLE),_col(sell,(LISTED_FOREIGN,LISTED_FOREIGN_FULLWIDTH),SELL_TABLE),lt)
    ld=(_col(buy,(LISTED_DEALER_SELF,),BUY_TABLE)+_col(buy,(LISTED_DEALER_HEDGE,),BUY_TABLE),_col(sell,(LISTED_DEALER_SELF,),SELL_TABLE)+_col(sell,(LISTED_DEALER_HEDGE,),SELL_TABLE),lt)
    of=(_optional_col(buy,(OTC_FOREIGN,OTC_FOREIGN_FULLWIDTH)),_optional_col(sell,(OTC_FOREIGN,OTC_FOREIGN_FULLWIDTH)),ot)
    od=(_optional_col(buy,(OTC_DEALER_SELF,))+_optional_col(buy,(OTC_DEALER_HEDGE,)),_optional_col(sell,(OTC_DEALER_SELF,))+_optional_col(sell,(OTC_DEALER_HEDGE,)),ot)
    otc=(_col(buy,(OTC_TOTAL,),BUY_TABLE),_col(sell,(OTC_TOTAL,),SELL_TABLE),ot)
    groups={"listed_dealer":ld,"listed_foreign":lf,"otc_foreign":of,"otc_dealer":od,"otc_total":otc,"combined_foreign":(lf[0]+of[0],lf[1]+of[1],lt+ot)}
    # economic id, trigger, label, direction, horizon, group, metric, days, norm, window, lower, upper, scope, history, plain
    specs=[
    ("otc_total_sell_low","otc_sell5_low","上櫃三大法人5日賣壓偏低","bullish","O1→C5","otc_total","sell",5,"pr",504,5,20,"absolute","平均+2.77%，中位數+2.53%，勝率81.3%；A級。","上櫃法人賣得明顯較少，歷史上之後5日偏強。"),
    ("otc_dealer_sell_low","otc_dealer_sell10_low","上櫃自營商10日賣壓極低","bullish","O1→C10","otc_dealer","sell",10,"z",756,-2.5,-1.5,"absolute","平均+3.58%，中位數+3.96%，勝率87.5%，樣本72；A級。","上櫃自營商近10日賣壓極低，歷史上後續10日偏強，但樣本不大。"),
    ("listed_dealer_net10_high","listed_dealer_net_10d_extreme_pr756","上市自營商10日淨買超極高","bullish","O1→C10","listed_dealer","net",10,"pr",756,95,100,"absolute","平均+3.08%，中位數+2.78%，勝率83.0%；A級。","上市自營商近10日淨買超在長期極高區，歷史上後續10日偏強。"),
    ("listed_dealer_net5_high","listed_dealer_net_5d_high_pr504","上市自營商5日淨買超偏高","bullish","O1→C10","listed_dealer","net",5,"z",504,1.5,2.5,"absolute","控制後相對報酬約+1.94%；A級。","上市自營商近5日淨買超明顯偏高，歷史上後續10日偏強。"),
    ("otc_total_sell_low","otc_sell5_low_10","上櫃三大法人5日賣壓偏低","bullish","O1→C10","otc_total","sell",5,"pr",504,5,20,"absolute","平均+2.77%，中位數+2.53%，勝率81.3%；A級。","上櫃法人近5日賣壓偏低，歷史上後續10日較強。"),
    ("otc_total_sell1_low","otc_sell1_low","上櫃三大法人單日賣壓偏低","bullish","O1→C10","otc_total","sell",1,"pr",504,5,20,"absolute","控制後相對報酬約+1.75%；A級。","今天上櫃法人賣壓明顯偏低，歷史上後續10日較強。"),
    ("listed_foreign_net5_high","listed_foreign_net5_high","上市外資5日淨買超極高","bullish","O1→C10","listed_foreign","net",5,"pr",504,95,100,"absolute","504／756日版本通過A級；空頭樣本較少。","上市外資近5日淨買超在長期極端高檔，歷史上後續10日偏強。"),
    ("combined_foreign_sell_high","combined_foreign_sell10_high","整體外資10日賣出活動極高","bullish","O1→C10","combined_foreign","sell",10,"z",756,2.5,float("inf"),"absolute","迴歸結果通過A級；研究表未提供分組樣本與勝率。","賣出活動極端高時統計上偏向反彈；缺少完整分組統計，應保守解讀。"),
    ("otc_foreign_buy10_high","otc_foreign_buy10_mid","上櫃外資10日買進偏高","bearish","O1→C10","otc_foreign","buy",10,"z",756,.5,1.5,"relative","中位數+0.064%、勝率50.92%，但相對0050較弱；A級。","這不是一定下跌，只表示0050歷史表現相對較弱。"),
    ("otc_foreign_buy5_high","otc_foreign_buy5_mid","上櫃外資5日買進偏高","bearish","O1→C10","otc_foreign","buy",5,"z",756,.5,1.5,"relative","方向受市場狀態影響；A級相對偏空證據。","可能是輪動或逆勢承接；只代表相對偏弱，不等於絕對下跌。"),
    ("otc_total_sell5_mid","otc_sell5_mid","上櫃三大法人5日賣壓中度偏高","bearish","O1→C10","otc_total","sell",5,"pr",756,60,80,"absolute","控制後相對報酬約−1.96%；A級。","上櫃法人近5日賣壓偏高，歷史上0050後續10日偏弱。"),
    ("otc_total_sell10_mid","otc_sell10_mid","上櫃三大法人10日賣壓中度偏高","bearish","O1→C10","otc_total","sell",10,"pr",756,60,80,"absolute","平均−1.21%，中位數−1.01%，勝率41.4%；A級。","上櫃法人近10日賣壓持續偏高，歷史上0050後續10日偏弱。")]
    out=[]
    for econ,trig,label,direction,horizon,g,metric,days,norm,w,lo,hi,scope,hist,plain in specs:
        b,s,t=groups[g]; amount={"buy":b,"sell":s,"net":b-s}[metric]; series=_ratio(amount,t,days)
        nv=_finite((_pr(series,w) if norm=="pr" else _z(series,w)).iloc[-1])
        matched=nv is not None and ((lo<nv<=hi) if norm=="pr" else (lo<=nv<hi))
        status="insufficient_data" if nv is None else ("matched" if matched else "not_matched")
        display_family={"listed_dealer":"listed_dealer_net","listed_foreign":"listed_foreign_net","otc_total":"otc_institutional_sell"}.get(g,g)
        out.append(SpotEvidence(display_family,econ,trig,label,direction,horizon,"A",status,_finite(series.iloc[-1]),nv,norm,w,days,
          (f"PR ({lo:g}, {hi:g}]" if norm=="pr" else f"Z [{lo:g}, {hi:g})"),_finite(b.rolling(days,min_periods=days).sum().iloc[-1]),
          _finite(s.rolling(days,min_periods=days).sum().iloc[-1]),_finite(t.rolling(days,min_periods=days).sum().iloc[-1]),hist,"白話說："+plain,scope))
    matched=[x for x in out if x.a_grade_status=="matched"]; d={}
    for x in matched:d.setdefault(x.economic_signal_id,set()).add(x.direction)
    bull=sum(v=={"bullish"} for v in d.values()); bear=sum(v=={"bearish"} for v in d.values()); mixed=sum(len(v)>1 for v in d.values())
    state="mixed" if mixed or (bull and bear) else ("bullish_evidence" if bull else ("bearish_evidence" if bear else "no_a_grade_match"))
    canonical_metrics:dict[str,pd.Series]={}
    for group_name,(group_buy,group_sell,group_turnover) in groups.items():
        subject={"otc_total":"otc_total_institutional"}.get(group_name,group_name)
        for metric_name,amount in (("buy",group_buy),("sell",group_sell),("net",group_buy-group_sell)):
            for days in (1,5,10):
                canonical_metrics[f"{subject}_{metric_name}_{days}d"]=_ratio(amount,group_turnover,days)
    return SpotFlowReport(idx[-1].date().isoformat(),tuple(out),state,bull,bear,mixed,"pass",True,canonical_metrics)

def load_live_spot_flow():
    from finlab import data
    return build_spot_flow_report(data.get(BUY_TABLE),data.get(SELL_TABLE),data.get(NET_TABLE),data.get(TURNOVER_TABLE))
