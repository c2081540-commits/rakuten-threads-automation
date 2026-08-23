import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
JST=timezone(timedelta(hours=9))
START='2026-08-24'; END='2026-09-02'; HOURS=[7,12,15,18,21]
q=json.loads((ROOT/'queue.json').read_text(encoding='utf-8'))
rows=[p for p in q.get('posts',[]) if START<=str(p.get('scheduled_at',''))[:10]<=END]
errors=[]; warnings=[]
if len(rows)!=50: errors.append(f'count={len(rows)}')
counts=Counter(p.get('type') for p in rows)
if counts.get('product')!=30: errors.append(f'product={counts.get("product")}')
if counts.get('interior_engagement')!=10: errors.append(f'engagement={counts.get("interior_engagement")}')
if counts.get('room_idea')!=10: errors.append(f'room_idea={counts.get("room_idea")}')
byday=defaultdict(list)
for p in rows: byday[p['scheduled_at'][:10]].append(p)
for d,day in sorted(byday.items()):
    day.sort(key=lambda p:p['scheduled_at'])
    hs=[datetime.fromisoformat(p['scheduled_at']).hour for p in day]
    if hs!=HOURS: errors.append(f'{d}:hours={hs}')
    cats=[p.get('furniture_category') for p in day if p.get('type')=='product']
    if len(cats)!=3 or len(set(cats))!=3: errors.append(f'{d}:categories={cats}')
products=[p for p in rows if p.get('type')=='product']
if len({p.get('selected_item_code') for p in products})!=30: errors.append('product duplication')
for p in products:
    for key in ('selected_item_code','parent_text','child_text_base','affiliate_url','image_urls','furniture_category'):
        if not p.get(key): errors.append(f'{p.get("post_id")}:missing {key}')
    if not str(p.get('affiliate_url','')).startswith('https://hb.afl.rakuten.co.jp/'): errors.append(f'{p.get("post_id")}:affiliate domain')
    if not all(str(u).startswith('https://thumbnail.image.rakuten.co.jp/') for u in p.get('image_urls',[])): errors.append(f'{p.get("post_id")}:image domain')
for p in rows:
    if p.get('type')!='product' and ('affiliate_url' in p or 'image_urls' in p or 'selected_item_code' in p): errors.append(f'{p.get("post_id")}:product data in nonproduct')
    text=str(p.get('parent_text',''))
    for phrase in ('買った','届いた','愛用','使ってみた','うちでは','実際に使うと','動画を見','動画で見'):
        if phrase in text: errors.append(f'{p.get("post_id")}:banned={phrase}')
for p in products:
    text=str(p.get('parent_text',''))
    for phrase in ('便利そう','良さそう','嬉しいポイント','おすすめ'):
        if phrase in text: warnings.append({'post_id':p['post_id'],'phrase':phrase,'text':text})
# exact parent duplicates
seen={}
for p in rows:
    t=''.join(str(p.get('parent_text','')).split())
    if t in seen: errors.append(f'exact duplicate:{seen[t]}={p.get("post_id")}')
    seen[t]=p.get('post_id')
# Due check without mutating queue: exact 8/24 07:00 exists and lies within normal 14 minute grace.
first=next((p for p in rows if p.get('scheduled_at')=='2026-08-24T07:00+09:00'),None)
if not first: errors.append('8/24 07 missing')
report={'validated_at':datetime.now(JST).isoformat(timespec='seconds'),'target_count':len(rows),'counts':dict(counts),'days':len(byday),'unique_products':len({p.get('selected_item_code') for p in products}),'errors':errors,'warnings':warnings,'first_post':first}
(ROOT/'data/interior_queue_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
if errors: raise SystemExit(1)
