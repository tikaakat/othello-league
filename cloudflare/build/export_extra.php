<?php
// ============================================================
// api.php を呼ぶだけでは静的ファイルにできないもの（利用者の入力で結果が変わるもの）を、
// Worker が組み立てられる形で書き出す。
//   _api/x/names.json              選手の名前の一覧（検索 action=search 用）
//   _api/x/player_matches/{id}.json その選手の直近50局（「最近の対局」の名前での絞り込み action=recent_matches&q= 用）
//   _api/x/games/season_{s}.json    季ごとの棋譜（action=game 用。1手を1つの数で表して小さくする）
//   _api/x/game_index.json          対局IDの範囲 → 季（どの季のファイルを読むか）
//   _api/x/title_holders.json       季ごと・タイトルごとの保持者（勝敗予想の採点用）
//
// 使い方：php export_extra.php <出力ディレクトリ>
// ============================================================

error_reporting(E_ALL);
require_once __DIR__ . '/db_config.php';
$out = rtrim($argv[1] ?? '', '/');
if ($out === '') { fwrite(STDERR, "使い方: php export_extra.php <出力ディレクトリ>\n"); exit(1); }
@mkdir("$out/_api/x/player_matches", 0777, true);
@mkdir("$out/_api/x/games", 0777, true);
$pdo = league_db_connect();
function put($path, $data) { file_put_contents($path, json_encode($data, JSON_UNESCAPED_UNICODE)); }

// api.php の MATCH_STAGE_ORDER_SQL と同じ（季の中での対局の進行順）
const STAGE_ORDER = "CASE m.league
    WHEN '青龍' THEN 1 WHEN '朱雀紅白決定戦' THEN 2 WHEN '朱雀紅組' THEN 3 WHEN '朱雀白組' THEN 3
    WHEN '朱雀紅組-playoff' THEN 4 WHEN '朱雀白組-playoff' THEN 4 WHEN '朱雀挑戦者決定戦' THEN 5 WHEN '朱雀' THEN 6
    WHEN '朱雀予選' THEN 7 WHEN '白虎予選' THEN 8 WHEN '白虎' THEN 9 WHEN '玄武予選' THEN 10 WHEN '玄武' THEN 11
    WHEN '麒麟予選' THEN 12 WHEN '麒麟' THEN 13 ELSE 0 END";

// ---- 名前の一覧（api.php の search と同じ：現役は retired=0、引退は retired=1・league=null） ----
$names = [];
foreach ($pdo->query("SELECT id, display_name, league FROM individuals WHERE retired = 0") as $r) $names[] = [$r['id'], $r['display_name'], $r['league'], 0];
foreach ($pdo->query("SELECT id, display_name FROM retired_archive") as $r) $names[] = [$r['id'], $r['display_name'], null, 1];
put("$out/_api/x/names.json", $names);

// ---- 選手ごとの直近50局（api.php の recent_matches と同じ列・同じ並び。_o は並べ替え用の進行順） ----
$base = "SELECT m.id, m.season, m.league, m.result, m.played_at,
            COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS individual_a_name,
            COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS individual_b_name,
            " . STAGE_ORDER . " AS _o
         FROM matches m
         LEFT JOIN individuals ia ON ia.id = m.individual_a_id
         LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
         LEFT JOIN individuals ib ON ib.id = m.individual_b_id
         LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
         WHERE m.individual_a_id = :a OR m.individual_b_id = :b
         ORDER BY m.season DESC, _o DESC, m.id DESC LIMIT 50";
$st = $pdo->prepare($base);
$nPlayers = 0;
foreach ($names as [$id]) {
    $st->execute(['a' => $id, 'b' => $id]);
    $rows = $st->fetchAll();
    foreach ($rows as &$r) { $r['id'] = (int)$r['id']; $r['season'] = (int)$r['season']; $r['_o'] = (int)$r['_o']; }
    unset($r);
    put("$out/_api/x/player_matches/" . rawurlencode($id) . ".json", $rows);
    $nPlayers++;
}

// ---- 季ごとの棋譜 ----
// api.php の game と同じ情報を持つ。1局の手順 moves は [{pos, color}, ...] を、pos + (color==2 ? 64 : 0) の数の列にする
$ranges = [];
$seasons = $pdo->query("SELECT DISTINCT season FROM matches ORDER BY season")->fetchAll(PDO::FETCH_COLUMN);
$st = $pdo->prepare(
    "SELECT m.id, m.season, m.league, m.result, m.games_json, m.played_at,
            COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS individual_a_name,
            COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS individual_b_name
     FROM matches m
     LEFT JOIN individuals ia ON ia.id = m.individual_a_id
     LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
     LEFT JOIN individuals ib ON ib.id = m.individual_b_id
     LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
     WHERE m.season = :s ORDER BY m.id"
);
foreach ($seasons as $s) {
    $st->execute(['s' => $s]);
    $byId = [];
    $min = null; $max = null;
    foreach ($st->fetchAll() as $r) {
        $id = (int)$r['id'];
        $min = $min === null ? $id : min($min, $id); $max = $max === null ? $id : max($max, $id);
        $games = json_decode($r['games_json'], true) ?: [];
        foreach ($games as &$g) {
            if (isset($g['moves']) && is_array($g['moves'])) {
                $g['moves'] = array_map(fn($mv) => (int)$mv['pos'] + (((int)$mv['color']) === 2 ? 64 : 0), $g['moves']);
                $g['_packed'] = 1;
            }
        }
        unset($g);
        $byId[$id] = [$r['league'], $r['result'], $r['played_at'], $r['individual_a_name'], $r['individual_b_name'], $games];
    }
    put("$out/_api/x/games/season_{$s}.json", ['season' => (int)$s, 'matches' => (object)$byId]);
    $ranges[] = [(int)$s, $min, $max];
}
put("$out/_api/x/game_index.json", $ranges);

// ---- 季ごと・タイトルごとの保持者（勝敗予想 predict.php の採点用。predict.php の holders_by_season と同じ） ----
$holders = [];
foreach ($pdo->query("SELECT season, title, holder_id, holder_name FROM title_history WHERE holder_id IS NOT NULL ORDER BY season") as $r) {
    $holders[(int)$r['season']][$r['title']] = ['id' => $r['holder_id'], 'name' => $r['holder_name']];
}
put("$out/_api/x/title_holders.json", (object)$holders);

echo "書き出し（Worker用）：選手 " . count($names) . "人　直近の対局 {$nPlayers}人分　棋譜 " . count($seasons) . "季分\n";
