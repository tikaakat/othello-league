#!/usr/bin/env bash
# ============================================================
# 公開する季の時点のデータ（data/ のスナップショット）から、Cloudflare に上げる dist/ を作る。
#
#   1. 一時的なMySQLに表を作り、data/ を取り込む（import.php）
#   2. いまの api.php をそのまま PHP の組み込みサーバーで動かし、全ページ分の結果をJSONに書き出す（export.py）
#      検索・名前での絞り込み・棋譜など、Worker が組み立てる分は export_extra.php で書き出す
#   3. データサイト（site/）・ファンサイト（news-site/）のページと、スナップショット時点の付随ファイルを並べる
#
# 使い方：bash cloudflare/build/build.sh <dataディレクトリ> <出力ディレクトリ（dist）>
# 前提：MySQL（MariaDB）が動いていて、環境変数 DB_HOST・DB_USER・DB_PASS で接続でき、DB_NAME を作り直してよいこと。
#       php（pdo_mysql）・python3 があること。リポジトリの一番上で実行する。
# ============================================================
set -euo pipefail

DATA="$(cd "$1" && pwd)"
OUT="$2"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
export DB_HOST="${DB_HOST:-127.0.0.1}" DB_NAME="${DB_NAME:-othello}" DB_USER="${DB_USER:-league}" DB_PASS="${DB_PASS:-league}"
MYSQL=(mysql -h "$DB_HOST" -u "$DB_USER" "-p$DB_PASS")

echo "=== 1. 一時的なMySQLに取り込む ==="
"${MYSQL[@]}" -e "DROP DATABASE IF EXISTS \`$DB_NAME\`; CREATE DATABASE \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"
"${MYSQL[@]}" "$DB_NAME" < "$HERE/schema.sql"
php "$HERE/import.php" "$DATA"

echo "=== 2. 結果をJSONに書き出す ==="
rm -rf "$OUT"
mkdir -p "$OUT"
WEB="$(mktemp -d)"
cp "$ROOT/site/api.php" "$HERE/db_config.php" "$WEB/"
mkdir -p "$WEB/league_export"
ln -s "$DATA/newcomer_league" "$WEB/league_export/newcomer_league"   # api.php の newcomer_league が直接読む
php "$HERE/export_extra.php" "$OUT"
python3 "$HERE/export.py" "$WEB" "$OUT"
rm -rf "$WEB"

echo "=== 3. ページと付随ファイルを並べる ==="
# データサイト（一番上）
cp "$ROOT/site/index.html" "$ROOT/site/ogp.png" "$OUT/"
# データサイトが読む、季の時点の付随ファイル（これまでは GitHub の main から直接読んでいた。未公開の季が見えないよう、スナップショットから置く）
mkdir -p "$OUT/data/news" "$OUT/data/bracket_tags"
[ -f "$DATA/upcoming_brackets.json" ] && cp "$DATA/upcoming_brackets.json" "$OUT/data/"
[ -f "$DATA/news/awards.json" ] && cp "$DATA/news/awards.json" "$OUT/data/news/"
cp "$DATA"/bracket_tags/*.json "$OUT/data/bracket_tags/" 2>/dev/null || true
# ファンサイト（/news/）
mkdir -p "$OUT/news"
cp "$ROOT/news-site/index.html" "$ROOT/news-site/robots.txt" "$ROOT/news-site/sitemap.xml" "$OUT/news/"
[ -d "$ROOT/news-site/assets" ] && cp -r "$ROOT/news-site/assets" "$OUT/news/"
if [ -f "$DATA/news/articles.json" ]; then cp "$DATA/news/articles.json" "$OUT/news/"; else echo '[]' > "$OUT/news/articles.json"; fi
if [ -f "$DATA/news/awards.json" ]; then cp "$DATA/news/awards.json" "$OUT/news/"; else echo '{}' > "$OUT/news/awards.json"; fi
python3 "$ROOT/fansite_state.py" "$DATA" "$OUT/news/season_state.json"
python3 "$ROOT/newcomer_careers.py" "$DATA" "$OUT/news/newcomer_careers.json" || echo "::warning::newcomer_careers.json を作れませんでした"
# 検索エンジン向け（サイト全体）
printf 'User-agent: *\nAllow: /\nDisallow: /_api/\nSitemap: https://othello.spia-net.com/news/sitemap.xml\n' > "$OUT/robots.txt"

echo "=== できあがり：$(find "$OUT" -type f | wc -l) ファイル・$(du -sh "$OUT" | cut -f1) ==="
