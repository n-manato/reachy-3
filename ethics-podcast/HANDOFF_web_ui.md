# 引き継ぎ: ブラウザUI(Web表示バックエンド)

最終更新: 2026-05-31 / 作業者: Claude Code → Codex へ引き継ぎ

## 0. ゴール(ユーザー要望)
現状 PySide6(Qtネイティブウィンドウ)で行っている「動画再生 + 3画面字幕UI(計4画面)」を
**ブラウザ表示に置き換え**、**LAN内のどのPCからもブラウザで各画面にアクセスできる**ようにする。

確定した設計判断(ユーザー合意済み):
- **音声はサーバ側のまま**(推奨)。各ロボットの発話wavは従来どおりオーケストレータPCの
  `sounddevice`/ロボットスピーカーで再生。ブラウザは映像・字幕・視線・音量バーの「見た目」だけ担当。
  → ロボットの首振り同期(`player.py` の speech sway / tool-call replay)は完全に現状維持。
- **画面は4台のPC/ディスプレイに分散**。`/top` `/student` `/professor` `/moderator` を各PCで全画面表示。
- 動画は `<video muted>` で**映像のみ**。音は従来経路(サーバ側)。

## 1. 実装したアーキテクチャ
既存の `DisplayManager`(Qt)は「再生ループ → 高レベルAPI → 画面」の綺麗な分離になっている。
そこで **同じAPIを持つ `WebDisplayManager`** を新規追加し、中身を「Qtウィジェット操作」→
「FastAPI/uvicornでHTMLを配信し、WebSocketで各ブラウザへ状態をpush」に置換した。
`player.py`・ロボット制御・音声まわりは**無変更**。

```
オーケストレータPC (司令塔 + Webサーバ + 音声出力)
  ├─ 既存の再生ループ player.py ── 発話wavを sounddevice/ロボットで再生【無変更】
  ├─ WebDisplayManager ── 状態を WebSocket で全ブラウザへ broadcast
  └─ FastAPI/uvicorn (0.0.0.0:8000、再生ループと同じasyncioループ上でタスク起動)
        ├─ GET /top /student /professor /moderator  … 各表示ページ(自己完結HTML)
        ├─ GET /all       … 4画面タイル(1台確認用、iframe)
        ├─ GET /video/current … 現在動画をRange対応で配信(FileResponse)
        └─ WS  /ws/{channel}   … 双方向。サーバ→状態push / ブラウザ→video_ended等
```

### メッセージプロトコル(WebSocket、JSON)
- サーバ→ブラウザ:
  - `{"type":"state", ...}` … 宣言的なフルビュー(ページはこれを見て描画)
    - top: `mode("video"|"topic"), videoSrc, videoEpoch, title, topic, current, next`
    - mini: `role, state("speaking"|"idle"|"gaze"), text, gazeDir(-1|0|1)`
  - `{"type":"level","value":0..1}` … mini の音量バー(サーバが実wavを読んで30fps配信)
- ブラウザ→サーバ:
  - `{"type":"video_ended"}` … top の `<video>` が再生終了 → `wait_until_near_end_and_pause()` が解除され次へ
- 新規接続時は、そのchannelの最新 `state` を再送(初期状態をシード済みなので空白にならない)

## 2. 作成/変更したファイル

### 新規
- `src/ethics_podcast/web_display/__init__.py` … `WebDisplayManager` を公開
- `src/ethics_podcast/web_display/manager.py` … 本体。FastAPIアプリ構築 + DisplayManager互換API +
  音量レベル配信 + uvicornを同一ループでタスク起動。主なメソッド:
  `load_video / play_video / wait_until_near_end_and_pause / show_topic / point_to /
   show_subtitle / finish_subtitle / pump_events_forever(no-op) / process_events(no-op) /
   close / wait_for_connection(channel,timeout)`
- `src/ethics_podcast/web_display/pages.py` … 自己完結HTML生成(白×青テーマ、視線アニメをCanvasに移植)。
  `page_top / page_mini(role) / page_all / page_index`
- `scripts/preview_web.py` … ロボット無し・1台で全フロー確認するプレビュー

### 変更
- `src/ethics_podcast/config.py` … `display_backend`("qt"|"web")、`display_web_host`、`display_web_port` を追加。
  env: `DISPLAY_BACKEND` / `DISPLAY_WEB_HOST` / `DISPLAY_WEB_PORT`
- `src/ethics_podcast/multi_video_discussion.py` … `_make_display()` を追加し `DISPLAY_BACKEND` で
  Qt版/Web版を切替(`DisplayManager` の直接importは関数内に移動)。Qt版は温存。
- `.env.example` … `DISPLAY_BACKEND` 等を記載
- `pyproject.toml` … `fastapi` / `uvicorn[standard]` / `websockets` を依存に追加(venvには既に導入済み)

## 3. 動作確認済み(サーバ側、自動テストで検証)
- HTTP `/ /top /student /professor /moderator /all` → 200
- WebSocket: 初期状態の再送 / `show_subtitle`→speaking / `finish_subtitle`→idle+gaze /
  `video_ended` の往復で `wait_until_near_end_and_pause()` が解除
- 音量レベル: 実wav(`output/<session>/turn_*.wav`)からサーバ側で算出して配信
- 動画配信 `/video/current`: **GET Range → 206 / Content-Type: video/mp4 / content-range 正常**(シーク可)
- 4接続を張った状態でも `close()` でトレースバック無しのクリーンなシャットダウン
  (`lifespan="off"` + WSハンドラで `CancelledError` を捕捉)

## 4. 未解決の問題 ★ここから続き★
**症状: ブラウザで動画が再生されない。** 字幕/視線フローは動く。

切り分け済み:
- サーバの `/video/current` は 206 + `video/mp4` で正常配信(コーデックも標準MP4 isom/H.264想定)
  → **サーバ側は問題なし**。原因はブラウザ側、ほぼ確実に**autoplay(自動再生)ブロック**。

入れた対策(`pages.py`):
- `<video muted autoplay playsinline>` に `autoplay` 追加
- 自動再生が弾かれたら「▶ クリックで動画を開始 / Click to start」オーバーレイ表示 →
  クリック or 画面クリックで `tryPlay()`
- `/all` の iframe に `allow="autoplay; fullscreen"` 付与
- コンソールにログ出力: `[top] state video src ... epoch ...` / `autoplay blocked ...` / `video error ...`

**Codexがまず確認すべきこと(ユーザーに依頼 or 自分で再現):**
ブラウザを更新して `/top` を直接開き、F12コンソールを確認:
1. `[top] state video src /video/current?token=N epoch N` が出るか
   - 出ない → WSが繋がっていない(IP違い/ファイアウォール TCP 8000)。字幕も出ないはず。
2. `autoplay blocked` / 「クリックで開始」表示 → autoplayブロック。クリックで再生されるか確認。
   - 無人運用は `chrome --kiosk --autoplay-policy=no-user-gesture-required http://<IP>:8000/top` で回避可。
3. `video error ...` → デコード/読込問題。`vid.error` の code を確認。
   - その場合の調査候補: コーデック(H.264/AAC か)、`ffprobe` で確認、`videos/*.mp4` の互換性。

もしautoplay対策後も映らない場合の追加調査ポイント:
- top ページ JS の `render()` 内 video分岐(`videoSrc`/`videoEpoch` の差分検知ロジック)
- `play_video()` のタイミング: プレビューでは `wait_for_connection("top")` で top接続を待ってから
  `play_video()` する。本番 `multi_video_discussion.py` では接続待ちをしていないので、表示PCの
  ブラウザ起動が遅いと最初の動画再生指示(epoch bump)を取りこぼす可能性 → 本番側でも
  `wait_for_connection` を入れるか、接続時に最新stateを再送している仕組みで足りるか要確認。
  (注: 接続時に最新stateは再送するが、`videoEpoch` は据え置きなので「接続前に play_video 済み」だと
   ブラウザ側 curEpoch=-1 → 差分ありで再生はされるはず。ここの挙動を実機で要検証。)

## 5. 実行方法

### 1台で確認(ロボット不要)
```powershell
.venv\Scripts\python.exe scripts\preview_web.py
# 起動直後に表示される URL を開く: http://<IP>:8000/top または /all
# 全クリップを観る: scripts\preview_web.py --full-video
# 接続待ち時間変更: scripts\preview_web.py --connect-wait 20
# 別セッション指定: scripts\preview_web.py video_military
```

### 本番(4台に分散)
1. `.env` に `DISPLAY_BACKEND=web`
2. `uv run ethics-podcast` → メニュー 2(Play) → 「Multi-Display Exhibit」
   (ロボット無しなら「Multi-Display Exhibit (Demo, no robots)」)
3. Windows Firewall で TCP 8000 許可:
   `New-NetFirewallRule -DisplayName "Ethics Podcast UI" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow`
4. 各表示PCで全画面: `start chrome --kiosk http://<サーバIP>:8000/top`(他 `/student` `/professor` `/moderator`)

## 6. テストの叩き方(参考: Codexが回帰確認に使える)
`WebDisplayManager` は asyncio ループ内で構築する必要がある(`asyncio.get_event_loop()` + `create_task`)。
`httpx`(導入済み)でHTTP、`websockets`(導入済み)でWSを叩ける。ポートは衝突回避で都度変える。
例は会話履歴の検証スクリプト参照(`/top` 取得、WS state往復、`/video/current` のRange確認 等)。

## 7. 注意・既知の制約
- `/top` を表示するPCのスピーカーから**動画音声**が出る(`<video muted>` なので現状は無音)。
  会場スピーカーがオーケストレータPC側なら、動画音声もサーバ側で鳴らす方式への切替が別途必要。
- `wait_until_near_end_and_pause()` は top の `video_ended` を待つ。top未接続時は
  `VIDEO_END_TIMEOUT`(1800s)でフォールバック。
- Qtバックエンド(`DISPLAY_BACKEND=qt`、デフォルト)は完全に温存。既存挙動に影響なし。
- gitブランチ: `main`。これらの変更は未コミット(ユーザー判断でコミット)。

## 9. main への統合メモ
- `src/ethics_podcast/main.py` の `MULTI_DISPLAY_LOOP` / `MULTI_DISPLAY_DEMO` から
  `run_multi_display_discussion_loop()` を呼ぶので、本番起動は preview と同じ共有ルートを通る。
- `.env` で `DISPLAY_BACKEND=web` を指定すると、`WebDisplayManager` が選ばれ、
  ブラウザ版の `top` / `student` / `professor` / `moderator` がそのまま robot フローに載る。
- `top` は動画を browser `<video>` で再生し、動画終了後に topic へ遷移する。
  `wait_for_connection("top")` で最初の再生を取りこぼしにくくしている。
- 本番で必要な確認項目:
  - `/top` を開いて動画音声が出ること
  - `MULTI_DISPLAY_LOOP` で video → topic → subtitle → gaze の流れが崩れないこと
  - Windows Firewall で TCP 8000 を許可していること
