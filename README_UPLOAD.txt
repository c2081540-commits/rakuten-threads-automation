楽天Threads 感情先行型パッチ

リポジトリ直下へ、このZIP内の構成をそのまま上書き/追加してください。

置換:
- gemini.py
- history.py
- generation_smoke_test.py

追加:
- prompts/emotion_first_product.txt
- docs/emotion_first_product_rules.md

既存の poster_persona.md / prompts/product.txt は削除・置換しません。
新しい感情先行ルールを gemini.py が最優先で読み込みます。

アップロード後、Actions > Rakuten Generation Smoke Test を1回実行してください。
確認項目: 商品2本に emotional_reaction / hook_type / purchase_trigger が入ること、親投稿が説明文ではなく感情フックになっていること。
