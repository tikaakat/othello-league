#!/usr/bin/env bash
# データサイトの季のデータを消す（publish.yml から使う：公開の取り消し unpublish=true と、第1季からの始動 reset=true）。
# - サーバー上の取り込み用ファイル（matches・standings・rosters・season_state・新人リーグ）を消す
# - DBの本戦データを消す（reset_database.php）
# - 季に紐づく利用者のデータ（勝敗予想・応援・記事のいいね）を消す（reset_database.php は触れないため）
# 必要な環境変数：XSERVER_HOST XSERVER_USER XSERVER_PORT XSERVER_TARGET_DIR RESET_DB_URL、鍵は /tmp/deploy_key
set -uo pipefail
SITE_DIR="spia-net.com/public_html/lab.spia-net.com/othello-league/"
retry_cmd() { local n=1; until "$@"; do [ $n -ge 4 ] && return 1; echo "::warning::失敗（$n回目）。再試行: $*"; sleep $((n * 5)); n=$((n + 1)); done; }
SSH="ssh -i /tmp/deploy_key -p $XSERVER_PORT -o StrictHostKeyChecking=no -o ConnectTimeout=15 $XSERVER_USER@$XSERVER_HOST"

echo "=== サーバーの取り込み用ファイルを消す ==="
retry_cmd $SSH "rm -rf ${XSERVER_TARGET_DIR}matches ${XSERVER_TARGET_DIR}standings ${XSERVER_TARGET_DIR}rosters.json ${XSERVER_TARGET_DIR}season_state.json ${XSERVER_TARGET_DIR}newcomer_league" \
  || { echo "::error::サーバーのファイルを消せませんでした"; exit 1; }

echo "=== DBの本戦データを消す ==="
retry_cmd curl -sS -f --connect-timeout 8 --max-time 60 "$RESET_DB_URL" || { echo "::error::DBを消せませんでした"; exit 1; }

echo "=== 勝敗予想・応援・記事のいいねを消す ==="
# テーブルがまだ無い場合は飛ばす。SSH経由の既定のphpは古いことがあるので、新しい版から順に探す（deploy_site.ymlと同じ）
retry_cmd $SSH "PHP_BIN=\$(command -v php84 || command -v php83 || command -v php82 || command -v php81 || command -v php80 || command -v php); \"\$PHP_BIN\" -r '
  require \"${SITE_DIR}db_config.php\";
  \$pdo = league_db_connect();
  foreach ([\"predictions\", \"cheer_log\", \"article_likes\"] as \$t) {
    try { \$n = \$pdo->exec(\"DELETE FROM \$t\"); echo \"\$t: \$n 件を削除\" . PHP_EOL; }
    catch (\PDOException \$e) { echo \"\$t: 飛ばしました（\" . \$e->getMessage() . \"）\" . PHP_EOL; }
  }
'" || { echo "::error::利用者のデータを消せませんでした"; exit 1; }
