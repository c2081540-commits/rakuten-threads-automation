import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HISTORY_PATH = Path(__file__).with_name("history.json")
JST = timezone(timedelta(hours=9))

def load_history():
    if not HISTORY_PATH.exists(): return {"product_history": [], "empathy_history": []}
    with HISTORY_PATH.open("r", encoding="utf-8") as f: data = json.load(f)
    data.setdefault("product_history", []); data.setdefault("empathy_history", []); return data

def save_history(data):
    tmp = HISTORY_PATH.with_suffix(".tmp"); tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8"); tmp.replace(HISTORY_PATH)

def record_success(post, thread_id, reply_id=None):
    data=load_history(); entry=dict(post); entry["posted_at"]=datetime.now(JST).isoformat(); entry["thread_id"]=thread_id
    if reply_id: entry["reply_id"]=reply_id
    data["product_history" if post.get("type")=="product" else "empathy_history"].append(entry); save_history(data)

def update_post_insights(thread_id, metrics, collected_at=None):
    data=load_history(); snapshot={"collected_at": collected_at or datetime.now(JST).isoformat(timespec="seconds"), "metrics":{str(k):int(v or 0) for k,v in (metrics or {}).items()}}; updated=False
    for key in ("product_history","empathy_history"):
        for entry in data[key]:
            if str(entry.get("thread_id",""))==str(thread_id): entry["insights"]=snapshot; updated=True; break
        if updated: break
    if updated: save_history(data)
    return updated

def _recent_product_entries(days=30):
    cutoff=datetime.now(JST)-timedelta(days=days); recent=[]
    for entry in load_history()["product_history"]:
        raw=entry.get("posted_at")
        if not raw: continue
        try:
            posted=datetime.fromisoformat(raw); posted=posted if posted.tzinfo else posted.replace(tzinfo=JST)
            if posted>=cutoff: recent.append(entry)
        except ValueError: continue
    return recent

def recent_product_codes(days=30):
    return {e.get("selected_item_code") or e.get("item_code") for e in _recent_product_entries(days) if e.get("selected_item_code") or e.get("item_code")}

def recent_product_axes(days=30):
    keys=("emotional_reaction","hook_type","purchase_trigger","problem_axis","benefit_axis","sales_structure")
    axes={k:set() for k in keys}
    for e in _recent_product_entries(days):
        for k in keys:
            v=str(e.get(k,"")).strip()
            if v: axes[k].add(v)
    return {k:sorted(v) for k,v in axes.items()}

def recent_product_strategy_entries(days=30, limit=30):
    rows=[]
    for e in _recent_product_entries(days)[-limit:]:
        ins=e.get("insights") or {}
        rows.append({"selected_item_code":e.get("selected_item_code") or e.get("item_code",""), "parent_text":e.get("parent_text",""), "emotional_reaction":e.get("emotional_reaction",""), "hook_type":e.get("hook_type",""), "purchase_trigger":e.get("purchase_trigger",""), "problem_axis":e.get("problem_axis",""), "benefit_axis":e.get("benefit_axis",""), "sales_structure":e.get("sales_structure",""), "scheduled_hour":e.get("scheduled_hour"), "posted_at":e.get("posted_at",""), "insights":ins.get("metrics",{})})
    return rows

def _engagement_score(metrics):
    return int(metrics.get("likes",0) or 0)+int(metrics.get("replies",0) or 0)*2+int(metrics.get("reposts",0) or 0)*3+int(metrics.get("quotes",0) or 0)*3+int(metrics.get("shares",0) or 0)*3

def product_performance_feedback(days=60, min_samples=3, limit=12):
    dimensions=("emotional_reaction","hook_type","purchase_trigger","problem_axis","benefit_axis","sales_structure","scheduled_hour")
    grouped={d:defaultdict(list) for d in dimensions}; eligible=0
    for e in _recent_product_entries(days):
        metrics=((e.get("insights") or {}).get("metrics") or {})
        if not metrics: continue
        eligible+=1; views=int(metrics.get("views",0) or 0); interactions=_engagement_score(metrics)
        for d in dimensions:
            v=e.get(d)
            if v not in (None,""): grouped[d][str(v)].append((views,interactions))
    tendencies=[]
    for d,values in grouped.items():
        for v,samples in values.items():
            if len(samples)<min_samples: continue
            tv=sum(x[0] for x in samples); ti=sum(x[1] for x in samples)
            tendencies.append({"dimension":d,"value":v,"samples":len(samples),"average_views":round(tv/len(samples),1),"average_interaction_score":round(ti/len(samples),2),"interaction_per_100_views":round(ti/tv*100,2) if tv else 0.0})
    tendencies.sort(key=lambda r:(r["samples"],r["interaction_per_100_views"],r["average_views"]), reverse=True)
    return {"days":days,"minimum_samples":min_samples,"eligible_posts_with_insights":eligible,"ready":bool(tendencies),"note":"サンプル不足の軸は評価に使わない。感情軸も十分な投稿数が貯まってから補助的に使う。","tendencies":tendencies[:limit]}

def recent_texts(kind, limit=5):
    key="product_history" if kind=="product" else "empathy_history"; return [e.get("parent_text","") for e in load_history()[key][-limit:] if e.get("parent_text")]

def recent_entries(limit=20):
    data=load_history(); combined=[]
    for kind,key in (("product","product_history"),("empathy","empathy_history")):
        for e in data[key]: row=dict(e); row["type"]=kind; combined.append(row)
    combined.sort(key=lambda x:x.get("posted_at","")); return combined[-limit:]
