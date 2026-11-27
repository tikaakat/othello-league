<?php
// ============================================================
// data/（ある季の時点のスナップショット）を、一時的なMySQLにまとめて取り込む。
// Xserverで使っていた import_from_colab.php と同じ対応づけで表を作る（api.php をそのまま動かすため）。
//
// 本番では季ごとに少しずつ取り込んでいたため、次の値は「取り込んだ時刻・その時点の季」で決まっていた。
// まとめて取り込むと全部同じになってしまうので、季から決まる値に置き換える。
//   - retired_archive.retired_season：その選手が順位表に最後に出た季（＝引退した季）
//   - 各表の時刻（played_at・achieved_at・retired_at・imported_at・updated_at）：季から作った架空の時刻
//     （並び順にだけ使われ、画面には出ない。毎回同じ値になるので、書き出すJSONも毎回同じになる）
// 季のファイルは番号順に取り込む（matches.id が季の順に振られ、本番と同じ並びになる）。
//
// 使い方：php import.php <dataディレクトリ>   （接続先は環境変数 DB_HOST・DB_NAME・DB_USER・DB_PASS）
// ============================================================

error_reporting(E_ALL);
require_once __DIR__ . '/db_config.php';

$dir = rtrim($argv[1] ?? '', '/');
if ($dir === '' || !is_dir($dir)) { fwrite(STDERR, "使い方: php import.php <dataディレクトリ>\n"); exit(1); }

function load_json($path) {
    if (!is_file($path)) return null;
    $d = json_decode(file_get_contents($path), true);
    if ($d === null) { fwrite(STDERR, "JSONを読めません: $path\n"); exit(1); }
    return $d;
}
// 季（または新人リーグの対象季）から作る架空の時刻。季の順に並ぶ
function season_time($season, $offsetSec = 0) {
    return date('Y-m-d H:i:s', strtotime('2020-01-01 00:00:00 UTC') + (int)$season * 86400 + $offsetSec);
}
// season_12.json のような名前のファイルを、番号順に返す
function numbered_files($pattern, $re) {
    $files = [];
    foreach (glob($pattern) as $f) if (preg_match($re, basename($f), $m)) $files[(int)$m[1]] = $f;
    ksort($files);
    return $files;
}

$rosters = load_json("$dir/rosters.json") ?? [];
$state = load_json("$dir/season_state.json") ?? [];
$current = (int)($state['current_season'] ?? 0);

$pdo = league_db_connect();
$pdo->beginTransaction();

// ---- 現役の選手 ----
$st = $pdo->prepare(
    "INSERT INTO individuals
       (id, display_name, league, clan_root_id, awakened_param, params_json, elo_rating, peak_elo,
        generation, parent_a_id, parent_b_id, seasons_in_league, total_seasons, initial_age, demotion_points, gender, retired,
        created_at, updated_at)
     VALUES (:id, :display_name, :league, :clan_root_id, :awakened, :params_json, :elo, :peak_elo,
             :generation, :parent_a, :parent_b, :seasons_in_league, :total_seasons, :initial_age, :demotion_points, :gender, 0,
             :t, :t2)"
);
$nInd = 0;
foreach (['A', 'B', 'C', 'D', 'F'] as $league) {
    foreach ($rosters[$league] ?? [] as $ind) {
        $st->execute([
            'id' => $ind['id'],
            'display_name' => $ind['display_name'] ?? null,
            'league' => $ind['league'],
            'clan_root_id' => $ind['clan_root_id'] ?? $ind['id'],
            'awakened' => $ind['awakened_param'] ?? null,
            'params_json' => json_encode($ind['params'], JSON_UNESCAPED_UNICODE),
            'elo' => (float)($ind['elo'] ?? 1500.0),
            'peak_elo' => (float)($ind['peak_elo'] ?? $ind['elo'] ?? 1500.0),
            'generation' => (int)($ind['generation'] ?? 0),
            'parent_a' => $ind['parent_a_id'] ?? null,
            'parent_b' => $ind['parent_b_id'] ?? null,
            'seasons_in_league' => (int)($ind['seasons_in_league'] ?? 0),
            'total_seasons' => (int)($ind['total_seasons'] ?? 0),
            'initial_age' => isset($ind['initial_age']) ? (int)$ind['initial_age'] : null,
            'demotion_points' => (int)($ind['demotion_points'] ?? 0),
            'gender' => $ind['gender'] ?? null,
            't' => season_time($current), 't2' => season_time($current),
        ]);
        $nInd++;
    }
}

// ---- 対局（棋譜つき） ----
$st = $pdo->prepare(
    "INSERT IGNORE INTO matches (match_uid, season, league, stage, block_no, individual_a_id, individual_b_id, result, games_json, played_at)
     VALUES (:match_uid, :season, :league, :stage, :block_no, :ind_a, :ind_b, :result, :games_json, :t)"
);
$nMatch = 0;
foreach (numbered_files("$dir/matches/season_*.json", '/^season_(\d+)\.json$/') as $season => $file) {
    foreach (load_json($file) ?? [] as $i => $e) {
        $games = json_encode($e['games'] ?? [], JSON_UNESCAPED_UNICODE);
        $st->execute([
            'match_uid' => md5($season . '|' . $e['league'] . '|' . $e['individual_a_id'] . '|' . ($e['individual_b_id'] ?? '') . '|' . $e['result'] . '|' . $games),
            'season' => $season,
            'league' => $e['league'],
            'stage' => $e['stage'] ?? null,
            'block_no' => $e['block'] ?? null,
            'ind_a' => $e['individual_a_id'],
            'ind_b' => $e['individual_b_id'] ?? null,
            'result' => $e['result'],
            'games_json' => $games,
            't' => season_time($season, min($i, 80000)),
        ]);
        $nMatch += $st->rowCount();
    }
}

// ---- 順位表（朱雀の紅白組は別の表） ----
$stS = $pdo->prepare(
    "INSERT INTO standings (season, league, `rank`, individual_id, display_name, elo, win, loss, draw, no_roundrobin, movement, demotion_point_event, demotion_points)
     VALUES (:season, :league, :rank, :individual_id, :display_name, :elo, :win, :loss, :draw, :no_roundrobin, :movement, :demotion_point_event, :demotion_points)"
);
$stZ = $pdo->prepare(
    "INSERT INTO suzaku_group_standings (season, league, `rank`, individual_id, display_name, elo, win, loss, draw, no_roundrobin, movement)
     VALUES (:season, :league, :rank, :individual_id, :display_name, :elo, :win, :loss, :draw, :no_roundrobin, :movement)"
);
$lastSeen = [];  // 選手ID => 順位表に最後に出た季（引退した季を求めるのに使う）
$nStand = 0;
foreach (numbered_files("$dir/standings/season_*.json", '/^season_(\d+)\.json$/') as $season => $file) {
    foreach (load_json($file) ?? [] as $r) {
        $suzaku = in_array($r['league'], ['朱雀紅組', '朱雀白組'], true);
        $p = [
            'season' => $r['season'], 'league' => $r['league'], 'rank' => $r['rank'] ?? null,
            'individual_id' => $r['individual_id'], 'display_name' => $r['display_name'] ?? null,
            'elo' => (float)($r['elo'] ?? 1500.0), 'win' => (int)($r['win'] ?? 0), 'loss' => (int)($r['loss'] ?? 0),
            'draw' => (int)($r['draw'] ?? 0), 'no_roundrobin' => !empty($r['no_roundrobin']) ? 1 : 0,
            'movement' => $r['movement'] ?? 'stay',
        ];
        if ($suzaku) { $stZ->execute($p); }
        else {
            $p['demotion_point_event'] = $r['demotion_point_event'] ?? null;
            $p['demotion_points'] = (int)($r['demotion_points'] ?? 0);
            $stS->execute($p);
        }
        $lastSeen[$r['individual_id']] = max($lastSeen[$r['individual_id']] ?? 0, (int)$r['season']);
        $nStand++;
    }
}

// ---- 新人リーグ（三段リーグ） ----
$stM = $pdo->prepare("INSERT INTO newcomer_league_meta (for_season, slots_needed, submission_count, imported_at) VALUES (:s, :slots, :subs, :t)");
$stN = $pdo->prepare(
    "INSERT INTO newcomer_league_standings (for_season, `rank`, individual_id, display_name, win, loss, draw, auto_generated, promoted, master_id, master_name)
     VALUES (:s, :rank, :individual_id, :display_name, :win, :loss, :draw, :auto_generated, :promoted, :master_id, :master_name)"
);
$nNew = 0;
foreach (numbered_files("$dir/newcomer_league/for_season_*.json", '/^for_season_(\d+)\.json$/') as $fs => $file) {
    $nl = load_json($file) ?? [];
    $stM->execute(['s' => $fs, 'slots' => (int)($nl['slots_needed'] ?? 0), 'subs' => (int)($nl['submission_count'] ?? 0), 't' => season_time($fs)]);
    foreach ($nl['standings'] ?? [] as $r) {
        $stN->execute([
            's' => $fs, 'rank' => $r['rank'] ?? null, 'individual_id' => $r['individual_id'],
            'display_name' => $r['display_name'] ?? null, 'win' => (int)($r['win'] ?? 0), 'loss' => (int)($r['loss'] ?? 0),
            'draw' => (int)($r['draw'] ?? 0), 'auto_generated' => !empty($r['auto_generated']) ? 1 : 0,
            'promoted' => !empty($r['promoted']) ? 1 : 0, 'master_id' => $r['master_id'] ?? null, 'master_name' => $r['master_name'] ?? null,
        ]);
        $nNew++;
    }
}

// ---- 引退した選手 ----
$st = $pdo->prepare(
    "INSERT IGNORE INTO retired_archive
       (id, display_name, league, clan_root_id, parent_a_id, params_json, elo_rating, peak_elo, total_seasons, initial_age, demotion_points, gender, retirement_reason, retired_season, retired_at)
     VALUES (:id, :display_name, :league, :clan_root_id, :parent_a_id, :params_json, :elo, :peak_elo, :total_seasons, :initial_age, :demotion_points, :gender, :retirement_reason, :retired_season, :t)"
);
$del = $pdo->prepare("DELETE FROM individuals WHERE id = :id");
$nRet = 0;
foreach ($state['retired_archive'] ?? [] as $i => $ind) {
    $rs = $lastSeen[$ind['id']] ?? $current;
    $st->execute([
        'id' => $ind['id'], 'display_name' => $ind['display_name'] ?? null, 'league' => $ind['league'],
        'clan_root_id' => $ind['clan_root_id'] ?? $ind['id'], 'parent_a_id' => $ind['parent_a_id'] ?? null,
        'params_json' => json_encode($ind['params'], JSON_UNESCAPED_UNICODE),
        'elo' => (float)($ind['elo'] ?? 1500.0), 'peak_elo' => (float)($ind['peak_elo'] ?? $ind['elo'] ?? 1500.0),
        'total_seasons' => (int)($ind['total_seasons'] ?? 0),
        'initial_age' => isset($ind['initial_age']) ? (int)$ind['initial_age'] : null,
        'demotion_points' => (int)($ind['demotion_points'] ?? 0), 'gender' => $ind['gender'] ?? null,
        'retirement_reason' => $ind['retirement_reason'] ?? null, 'retired_season' => $rs,
        // 同じ季に引退した選手どうしは、引退の記録順（season_state.jsonの並び）に並ぶようにずらす
        't' => season_time($rs, $i),
    ]);
    if ($st->rowCount() > 0) { $nRet++; $del->execute(['id' => $ind['id']]); }
}

// ---- タイトルの履歴 ----
$st = $pdo->prepare(
    "INSERT IGNORE INTO title_history (season, title, event_type, holder_name, holder_id, challenger_wins, titleholder_wins, achieved_at)
     VALUES (:season, :title, :event_type, :holder_name, :holder_id, :c_wins, :t_wins, :t)"
);
$nTitle = 0;
foreach ($state['title_history'] ?? [] as $t) {
    $season = $t['season'] ?? $current;
    $st->execute([
        'season' => $season, 'title' => $t['title'],
        'event_type' => $t['event'] ?? (($t['won'] ?? false) ? '奪取' : '防衛'),
        'holder_name' => $t['holder_name'] ?? $t['new_holder'] ?? $t['challenger_name'] ?? '不明',
        'holder_id' => $t['holder_id'] ?? $t['challenger_id'] ?? null,
        'c_wins' => $t['challenger_wins'] ?? null, 't_wins' => $t['titleholder_wins'] ?? null,
        't' => season_time($season, 86000),
    ]);
    $nTitle += $st->rowCount();
}

// ---- 今の季 ----
$pdo->prepare("INSERT INTO season_state (id, current_season, suzaku_league_json, updated_at) VALUES (1, :s, :j, :t)")
    ->execute(['s' => $current, 'j' => json_encode($state['suzaku_league'] ?? null, JSON_UNESCAPED_UNICODE), 't' => season_time($current)]);

$pdo->commit();
echo "取り込み完了：第{$current}季時点　選手 {$nInd}　対局 {$nMatch}　順位 {$nStand}　新人リーグ {$nNew}　引退 {$nRet}　タイトル {$nTitle}\n";
