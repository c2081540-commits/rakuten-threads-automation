import json, os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))
START = date(2026, 8, 24)
DAYS = 10
HOURS = [7, 12, 15, 18, 21]
TARGET_PER_CATEGORY = {
    'sofa_chair': 4, 'table_desk': 4, 'lighting': 4, 'rug_curtain': 4,
    'bed_bedding': 4, 'storage_furniture': 4, 'mirror_dresser': 3, 'interior_decor': 3,
}

def load_json(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))

def select_products(products):
    by = defaultdict(list)
    for p in products:
        by[p['candidateCategory']].append(p)
    picked = {}
    for cat, n in TARGET_PER_CATEGORY.items():
        rows = by[cat]
        if len(rows) < n:
            raise RuntimeError(f'{cat} candidate shortage: {len(rows)} < {n}')
        idxs = [round(i * (len(rows)-1) / (n-1)) for i in range(n)] if n > 1 else [0]
        picked[cat] = [rows[i] for i in idxs]

    # 10日×3商品を、同日のカテゴリが重ならないように貪欲に配置する。
    remaining = {cat:list(rows) for cat, rows in picked.items()}
    ordered = []
    for _ in range(DAYS):
        day = []
        for _slot in range(3):
            candidates = [(len(rows), cat) for cat, rows in remaining.items() if rows and cat not in {p['candidateCategory'] for p in day}]
            if not candidates:
                raise RuntimeError('cannot create 3 distinct categories for a day')
            _, cat = max(candidates)
            day.append(remaining[cat].pop(0))
        ordered.extend(day)
    if len(ordered) != 30 or len({p['itemCode'] for p in ordered}) != 30:
        raise RuntimeError('30 unique products could not be selected')
    return ordered

def product_facts(p):
    return {
        'itemCode':p['itemCode'],'itemName':p['itemName'],'itemCaption':p.get('itemCaption','')[:1400],
        'price':p['itemPrice'],'rating':p['reviewAverage'],'reviews':p['reviewCount'],
        'category':p['candidateCategory'],'categoryLabel':p['candidateCategoryLabel']
    }

def call_day(client, model, d, products, recent_texts):
    prompt=f'''あなたは家具・インテリアを見るのが好きな20代後半〜30代女性としてThreads投稿を作る。専門家・販売員ではない。
楽天で見つけた家具を友人に「これ見て」と共有する自然な距離感。未所有商品を買った・届いた・使った・愛用・うちでは等と書かない。
商品説明の要約は禁止。見た目、部屋に置いた想像、価格への驚き、色・形、模様替えの気分を中心にする。
「便利そう」「良さそう」「ポイント」「おすすめ」を説明調の定型句として使わない。「フォトジェニック」「映える」等の広告っぽい語も必要以上に使わない。
実際には商品画像投稿なので「動画を見た」「動画で見た」と書かない。

{d.isoformat()} の5投稿を作る。
07:00 interior_engagement：A/B、どっち派、短い質問。家具・部屋づくり軸。
12:00 product：商品1。
15:00 room_idea：商品なし。模様替え・家具選び・色・配置の自然な気づき。専門家の断定や指導口調にしない。
18:00 product：商品2。
21:00 product：商品3。

指定3商品のカテゴリは別々。必ず指定コードのまま使用する。
親投稿は1〜3文。商品を見た人の自然な発見・想像。返信は商品情報にある具体事実を1〜2点だけ補足。
非商品2件に商品名・URL・PRを入れない。同日の3商品は導入・締め・切り口を変える。直近本文とも言い回しを重ねない。

商品情報:{json.dumps([product_facts(p) for p in products],ensure_ascii=False)}
直近本文:{json.dumps(recent_texts[-15:],ensure_ascii=False)}

JSONのみ:{{"posts":[
{{"slot":"07:00","type":"interior_engagement","parent_text":"...","hook_type":"comparison|question|preference"}},
{{"slot":"12:00","type":"product","selected_item_code":"指定コード","parent_text":"...","child_text_base":"...","hook_type":"discovery|room_imagination|price_hook|comparison|seasonal|wishlist"}},
{{"slot":"15:00","type":"room_idea","parent_text":"...","hook_type":"room_idea|layout|color|styling"}},
{{"slot":"18:00","type":"product","selected_item_code":"指定コード","parent_text":"...","child_text_base":"...","hook_type":"..."}},
{{"slot":"21:00","type":"product","selected_item_code":"指定コード","parent_text":"...","child_text_base":"...","hook_type":"..."}}]}}'''
    banned=['買った','届いた','愛用','使ってみた','うちでは','実際に使うと','動画を見','動画で見']
    for _ in range(3):
        r=client.responses.create(model=model,input=prompt,max_output_tokens=4500,reasoning={'effort':'low'},text={'format':{'type':'json_object'}})
        posts=json.loads(r.output_text).get('posts',[])
        try:
            if len(posts)!=5:raise ValueError('post count')
            if [x.get('slot') for x in posts]!=['07:00','12:00','15:00','18:00','21:00']:raise ValueError('slot order')
            if [x.get('type') for x in posts]!=['interior_engagement','product','room_idea','product','product']:raise ValueError('type order')
            if [x.get('selected_item_code') for x in posts if x.get('type')=='product']!=[p['itemCode'] for p in products]:raise ValueError('product codes')
            for x in posts:
                text=str(x.get('parent_text','')).strip()
                if not text or any(b in text for b in banned):raise ValueError('bad parent')
                if x['type']=='product' and not str(x.get('child_text_base','')).strip():raise ValueError('empty child')
            return posts
        except Exception as e:
            prompt+=f'\n前回は検品不合格:{e}。条件を守って全5件を作り直す。'
    raise RuntimeError(f'generation failed for {d}')

def main():
    cand=load_json('data/candidates/latest.json')
    if cand.get('filters',{}).get('niche')!='furniture_interiors':raise RuntimeError('latest candidates are not furniture_interiors')
    selected=select_products(cand['products'])
    client=OpenAI(api_key=os.environ['OPENAI_API_KEY'],timeout=120.0)
    model=os.environ.get('OPENAI_MODEL','gpt-5-mini')
    queue=load_json('queue.json')
    old_report={}
    try:old_report=load_json('data/interior_10day_report.json')
    except Exception:pass
    cutoff=datetime(2026,8,24,0,0,tzinfo=JST)
    kept=[];removed=[]
    for p in queue.get('posts',[]):
        try:dt=datetime.fromisoformat(p.get('scheduled_at','')).astimezone(JST)
        except Exception:kept.append(p);continue
        (removed if dt>=cutoff else kept).append(p)
    new_posts=[];recent=[]
    for day_i in range(DAYS):
        d=START+timedelta(days=day_i);day_products=selected[day_i*3:(day_i+1)*3]
        if len({p['candidateCategory'] for p in day_products})!=3:raise RuntimeError(f'category duplication on {d}')
        generated=call_day(client,model,d,day_products,recent);by_code={p['itemCode']:p for p in day_products}
        for hour,g in zip(HOURS,generated):
            row={'post_id':f'INT-{d.strftime("%Y%m%d")}-{hour:02d}','scheduled_at':datetime(d.year,d.month,d.day,hour,0,tzinfo=JST).isoformat(timespec='minutes'),'status':'scheduled','type':'product' if g['type']=='product' else g['type'],'content_format':g['type'],'parent_text':g['parent_text'].strip(),'hook_type':str(g.get('hook_type','')).strip()}
            if g['type']=='product':
                item=by_code[g['selected_item_code']]
                row.update({'selected_item_code':item['itemCode'],'item_code':item['itemCode'],'item_name':item['itemName'],'child_text_base':g['child_text_base'].strip(),'image_url':item['imageUrls'][0],'image_urls':item['imageUrls'][:3],'affiliate_url':item['affiliateUrl'],'price':item['itemPrice'],'rating':item['reviewAverage'],'review_count':item['reviewCount'],'furniture_category':item['candidateCategory'],'furniture_category_label':item['candidateCategoryLabel']})
            new_posts.append(row);recent.append(row['parent_text'])
    assert len(new_posts)==50
    assert sum(p['type']=='product' for p in new_posts)==30
    assert sum(p['type']=='interior_engagement' for p in new_posts)==10
    assert sum(p['type']=='room_idea' for p in new_posts)==10
    assert len({p['selected_item_code'] for p in new_posts if p['type']=='product'})==30
    for i in range(DAYS):
        rows=new_posts[i*5:(i+1)*5]
        assert [datetime.fromisoformat(p['scheduled_at']).hour for p in rows]==HOURS
        assert len({p.get('furniture_category') for p in rows if p['type']=='product'})==3
    assert not any('affiliate_url' in p for p in new_posts if p['type']!='product')
    queue['posts']=sorted(kept+new_posts,key=lambda p:p.get('scheduled_at',''))
    (ROOT/'queue.json').write_text(json.dumps(queue,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    original_removed=int(old_report.get('removed_old_reservations',0) or 0)
    report={'generated_at':datetime.now(JST).isoformat(timespec='seconds'),'removed_old_reservations':max(original_removed,len(removed)),'new_reservations':50,'counts':{'furniture_product':30,'interior_engagement':10,'room_idea':10},'selected_products':[{'itemCode':p['itemCode'],'itemName':p['itemName'],'category':p['candidateCategoryLabel'],'price':p['itemPrice']} for p in selected]}
    (ROOT/'data/interior_10day_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
