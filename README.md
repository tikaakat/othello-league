# サーキットオセロリーグ

AIの選手たちが毎日戦う、架空のオセロ・プロリーグです。選手は生まれ、育ち、昇級し、タイトルを争い、衰えて引退します。その歴史を、毎日 7:30・21:00（日本時間）に1季ずつ公開しています。

- **データサイト**（公式記録）：https://lab.spia-net.com/othello-league/
  リーグ戦・5大タイトル戦の結果、選手ごとの通算成績と系譜、棋譜の再生、新人の歩み。**選手と対局**もできます。
- **オセロ界タイムズ**（ファンメディア）：https://lab.spia-net.com/othello-news/
  季ごとのニュース記事（Claudeが執筆）、選手名鑑、掲示板。

## 仕組み

```mermaid
flowchart LR
  S[scheduler.yml<br/>毎時起動・朝夜の回を判定] --> E[evolve.yml<br/>本戦を計算]
  E -->|公開時刻まで待つ| X[(Xserver<br/>PHP + MySQL)]
  E --> N[newcomer_league.yml<br/>奨励会・三段リーグ]
  N --> X
  E --> G[generate_news.yml<br/>Claudeが記事を執筆]
  N --> G
  G --> F[ファンサイト]
  X --> D[データサイト<br/>api.php]
  E -.結果をコミット.-> R[(GitHub<br/>data/)]
```

| 層 | 技術 | 主なファイル |
|---|---|---|
| シミュレーション | Python 3.11（標準ライブラリのみ） | `run_season.py`・`run_newcomer_league.py`・`othello_league/` |
| 自動運用 | GitHub Actions（定時実行・連続実行・自動復旧） | `.github/workflows/` |
| データサイト | 1枚のHTML（素のJS）＋ PHP API ＋ MySQL | `site/index.html`・`site/api.php` |
| ファンサイト | 1枚のHTML（素のJS）＋ 静的JSON | `news-site/index.html`・`fansite_state.py`・`newcomer_careers.py` |
| 記事生成 | Claude Code CLI（ヘッドレス実行） | `generate_news.py`・`news_topics.py` |

### 選手の強さ

選手は「8つの能力の比率」（角・危険マス回避・機動力・辺の安定・開放度・石数・パリティ・中央支配）で棋風が決まり、それを重みにした評価関数とアルファベータ探索で手を選びます（`othello_league/engine.py`）。比率だけでは強さに差がつきにくいため、次の実力の軸を重ねています（`othello_league/skills.py`）。

- **読みの深さ**：中盤以降に何手深く読むか（才能で上限が決まり、45歳から衰える）
- **終盤力**：空きマスがいくつ以下から深く読む・読み切るか
- **定跡**：序盤の何手目までを定跡で指すか
- **ポカ率**：最善手以外を指してしまう確率
- **天賦・大天才**：ごくまれに生まれる才能（約50季に1人）

弟子は師匠の棋風を受け継いで生まれ、奨励会 → 三段リーグ（上位3名がプロ入り）→ D・C・B・Aリーグと上がっていきます。タイトルは青龍・麒麟・朱雀・白虎・玄武の5つです。

### 対局モード

データサイトの「あそぶ」では、選手と実際に対局できます。Pythonの思考エンジンをJavaScriptに移植し（合法手・評価関数ともPython版と一致することを確認済み）、Web Workerで動かしているので、選手の棋風・読みの深さ・終盤力・ポカの癖まで、リーグ戦と同じ強さで再現されます。

## 運用

- **定時公開**：`scheduler.yml` が毎時起動し、朝の回（2:30〜15:59）・夜の回（16:00〜翌2:29）に本戦を1回だけ起動します。計算が済んでも、サイトへの反映はその回の公開時刻（7:30・21:00）まで待ちます。
- **自動復旧**：本戦が失敗した回は次の起動でやり直し、新人リーグが失敗していればそれを先にやり直します（同じ季を二重に実行しない作り）。
- **連続実行**：`evolve.yml` を `chain_remaining=N` で起動すると、本戦→新人リーグ→本戦…をN季分続けます（検証用）。

## ローカルで動かす

```bash
python3 run_season.py --data-dir data --seasons 1     # 1季進める（data/ が更新される）
python3 run_newcomer_league.py --data-dir data        # 三段リーグ
python3 newcomer_careers.py data /tmp/careers.json    # 新人の通算成績一覧を作る
```
