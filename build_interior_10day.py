import json, os, re
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
    chosen = []
    for cat, n in TARGET_PER_CATEGORY.items():
        rows = by[cat]
        if len(rows) < n:
            raise RuntimeError(f'{cat} candidate shortage: {len(rows)} < {n}')
        # Pick across the category list rather than only the first expensive/popular cluster.
        if n == 1:
            idxs = [0]
        else:
            idxs = [round(i * (len(rows)-1) / (n-1)) for i in range(n)]
        for i in idxs:
            chosen.append(rows[i])
    if len(chosen) != 30 or len({p['itemCode'] for p in chosen}) != 30:
        raise RuntimeError('30 unique products could not be selected')
    return chosen

def product_facts(p):
    return {
        'itemCode': p['itemCode'], 'itemName': p['itemName'],
        'itemCaption': p.get('itemCaption','')[:1400], 'price': p['itemPrice'],
        'rating': p['reviewAverage'], 'reviews': p['reviewCount'],
        'category': p['candidateCategory'], 'categoryLabel': p['candidateCategoryLabel'],
    }

def call_day(client, model, d, products, recent_texts):
    prompt = f'''あなたは家具・インテリア好きの20代後半〜30代女性としてThreads投稿を作る。
専門家・販売員ではない。楽天で見つけた家具を友人に共有する自然な距離感。
未所有商品を「買った」「届いた」「使った」「愛用」「うちでは」と書かない。
商品説明の要約文は禁止。見た目、部屋に置いた想像、価格への驚き、色・形、模様替えの気分を中心にする。
「便利そう」「良さそう」「ポイント」を定型的に使わない。

{d.isoformat()} の5投稿を作る。固定順は以下。
07:00 interior_engagement（A/B、どっち派、短い質問。家具・部屋づくり軸）
12:00 furniture_product（商品1）
15:00 room_idea（商品なし。模様替え・家具選び・色・配置等の短い話）
18:00 furniture_product（商品2）
21:00 furniture_product（商品3。照明・寝具等を夜に優先してよいが無理に固定しない）

商品3件は必ず指定コードのまま使う。親投稿は1〜3文、自然な発見・想像。返信は商品情報にある具体事実を1〜2点だけ補足。
非商品2件に商品名・URL・PRを入れない。
同日の3商品は同じ導入・締めを避ける。直近投稿とも言い回しを重ねない。

商品情報:{json.dumps([product_facts(p) for p in products], ensure_ascii=False)}
直近本文:{json.dumps(recent_texts[-15:], ensure_ascii=False)}

JSONのみ:
{{"posts":[
{{"slot":"07:00","type":"interior_engagement","parent_text":"...","hook_type":"comparison|question|preference"}},
{{"slot":"12:00","type":"product","selected_item_code":"指定コード","parent_text":"...","child_text_base":"...","hook_type":"discovery|room_imagination|price_hook|comparison|seasonal|wishlist"}},
{{"slot":"15:00","type":"room_idea","parent_text":"...","hook_type":"room_idea|layout|color|styling"}},
{{"slot":"18:00","type":"product","selected_item_code":"指定コード","parent_text":"...","child_text_base":"...","hook_type":"..."}},
{{"slot":"21:00","type":"product","selected_item_code":"指定コード","parent_text":"...","child_text_base":"...","hook_type":"..."}}
]}}'''
    for attempt in range(3):
        r = client.responses.create(model=model, input=prompt, max_output_tokens=4500,
            reasoning={'effort':'low'}, text={'format':{'type':'json_object'}})
        obj = json.loads(r.output_text)
        posts = obj.get('posts', [])
        try:
            if len(posts) != 5: raise ValueError('post count')
            expected_slots = ['07:00','12:00','15:00','18:00','21:00']
            if [x.get('slot') for x in posts] != expected_slots: raise ValueError('slot order')
            if [x.get('type') for x in posts] != ['interior_engagement','product','room_idea','product','product']: raise ValueError('type order')
            expected_codes = [p['itemCode'] for p in products]
            got_codes = [x.get('selected_item_code') for x in posts if x.get('type')=='product']
            if got_codes != expected_codes: raise ValueError(f'codes {got_codes} != {expected_codes}')
            banned = ['買った','届いた','愛用','使ってみた','うちでは','実際に使うと']
            for x in posts:
                text = str(x.get('parent_text','')).strip()
                if not text or any(b in text for b in banned): raise ValueError('bad parent')
                if x['type']=='product' and not str(x.get('child_text_base','')).strip(): raise ValueError('empty child')
            return posts
        except Exception as e:
            prompt += f'\n前回出力は検品不合格: {e}。全条件を守ってJSONを作り直す。'
    raise RuntimeError(f'generation failed for {d}')

def main():
    cand = load_json('data/candidates/latest.json')
    if cand.get('filters',{}).get('niche') != 'furniture_interiors':
        raise RuntimeError('latest candidates are not furniture_interiors')
    selected = select_products(cand['products'])
    client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=120.0)
    model = os.environ.get('OPENAI_MODEL','gpt-5-mini')
    queue = load_json('queue.json')
    cutoff = datetime(2026,8,24,0,0,tzinfo=JST)
    kept, removed = [], []
    for p in queue.get('posts',[]):
        try: dt = datetime.fromisoformat(p.get('scheduled_at','')).astimezone(JST)
        except Exception: kept.append(p); continue
        if dt >= cutoff:
            removed.append(p)
        else:
            kept.append(p)
    new_posts=[]; recent=[]
    # 3 products per day from the 30-product selection; order rotates categories naturally.
    for day_i in range(DAYS):
        d = START + timedelta(days=day_i)
        day_products = selected[day_i*3:(day_i+1)*3]
        generated = call_day(client, model, d, day_products, recent)
        by_code={p['itemCode']:p for p in day_products}
        for slot_i,(hour,g) in enumerate(zip(HOURS,generated),start=1):
            row={
                'post_id':f'INT-{d.strftime("%Y%m%d")}-{hour:02d}',
                'scheduled_at':datetime(d.year,d.month,d.day,hour,0,tzinfo=JST).isoformat(timespec='minutes'),
                'status':'scheduled',
                'type':'product' if g['type']=='product' else g['type'],
                'content_format':g['type'],
                'parent_text':g['parent_text'].strip(),
                'hook_type':str(g.get('hook_type','')).strip(),
            }
            if g['type']=='product':
                item=by_code[g['selected_item_code']]
                row.update({
                    'selected_item_code':item['itemCode'], 'item_code':item['itemCode'],
                    'item_name':item['itemName'], 'child_text_base':g['child_text_base'].strip(),
                    'image_url':item['imageUrls'][0], 'image_urls':item['imageUrls'][:3],
                    'affiliate_url':item['affiliateUrl'], 'price':item['itemPrice'],
                    'rating':item['reviewAverage'], 'review_count':item['reviewCount'],
                    'furniture_category':item['candidateCategory'],
                    'furniture_category_label':item['candidateCategoryLabel'],
                })
            new_posts.append(row); recent.append(row['parent_text'])
    # deterministic validation
    assert len(new_posts)==50
    assert sum(p['type']=='product' for p in new_posts)==30
    assert sum(p['type']=='interior_engagement' for p in new_posts)==10
    assert sum(p['type']=='room_idea' for p in new_posts)==10
    assert len({p['selected_item_code'] for p in new_posts if p['type']=='product'})==30
    for i in range(DAYS):
        rows=new_posts[i*5:(i+1)*5]
        assert [datetime.fromisoformat(p['scheduled_at']).hour for p in rows]==HOURS
    assert not any('affiliate_url' in p for p in new_posts if p['type']!='product')
    queue['posts']=sorted(kept+new_posts,key=lambda p:p.get('scheduled_at',''))
    Path(ROOT/'queue.json').write_text(json.dumps(queue,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    report={
        'generated_at':datetime.now(JST).isoformat(timespec='seconds'),'removed_old_reservations':len(removed),
        'new_reservations':50,'counts':{'furniture_product':30,'interior_engagement':10,'room_idea':10},
        'selected_products':[{'itemCode':p['itemCode'],'itemName':p['itemName'],'category':p['candidateCategoryLabel'],'price':p['itemPrice']} for p in selected],
    }
    (ROOT/'data/interior_10day_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__': main()
