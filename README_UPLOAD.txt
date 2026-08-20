楽天Threads 感情先行型 v2 完成パッチ

リポジトリ直下へ中身をそのままアップロードしてください。
上書き:
- gemini.py
- generation_smoke_test.py
- history.py
- prompts/emotion_first_product.txt
- docs/emotion_first_product_rules.md

主な変更:
1. 共感3本を商品情報ゼロの独立APIリクエストで生成
2. 商品選定に hook_strength(7以上必須) / natural_reaction / why_shareable / hook_evidence を導入
3. 商品だけA/B/C生成・比較
4. E1/P1/E2/P2/E3をPython側で合流
5. 「嬉しい発見」「〜なら助かる」等のAI的な擬似感情表現を機械検品で拒否
6. 商品親投稿は感情フック、返信は理由・仕様という役割分担を維持

想定API回数:
通常4回（共感1 + 商品選定1 + 商品A/B/C生成1 + 比較1）。再生成時は増える場合あり。

アップロード後に Rakuten Generation Smoke Test を1回実行してください。
