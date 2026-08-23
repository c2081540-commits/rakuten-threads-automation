import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
q=ROOT/'queue.json'
data=json.loads(q.read_text(encoding='utf-8'))
replacements={
'INT-20260824-18':'壁にちょこんと天然木の棚があるだけで、写真立てや小さな植物の居場所ができる感じが好き。奥行きが浅いから、壁面に少し飾りたい時の候補に入れたい。',
'INT-20260830-18':'透明ガラスのペンダント、灯りが入った時の表情まできれいそう。ダイニングにひとつ下げるだけで、家具を増やさず雰囲気を変えられそうなのが気になる。',
'INT-20260902-18':'木目調の掛け時計って、白い壁にひとつあるだけで少し温かく見える。デスク横やリビングの余白に置きたいサイズ感で気になった。',
}
changed=0
for p in data['posts']:
    if p.get('post_id') in replacements:
        p['parent_text']=replacements[p['post_id']];changed+=1
if changed!=3: raise RuntimeError(f'expected 3 changes, got {changed}')
q.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('patched',changed)
