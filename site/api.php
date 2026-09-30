<?php
// ============================================================
// CIRCUIT OTHELLO LEAGUE 表示用API
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
// キャラクリエイト送信直後など、ブラウザ（特にモバイル）が古いレスポンスを
// キャッシュして「反映が遅い」ように見えるのを防ぐため、常に最新を取得させる
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

function json_out($data) {
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
    exit;
}
function json_error($message, $status = 400) {
    http_response_code($status);
    json_out(['error' => $message]);
}

function format_season_ranges($seasons, $latest_season, $show_open_ended = true) {
    // 昇順のシーズン番号配列から、連続区間をまとめた文字列を作る（例：「1-3シーズン、5シーズン〜」）
    // 現在も継続中（オープンエンド）の区間は、まだ終わっていないので終了シーズンを書かず「開始シーズン〜」とだけ示す
    sort($seasons);
    $ranges = [];
    $start = $seasons[0];
    $prev = $seasons[0];
    for ($i = 1; $i <= count($seasons); $i++) {
        $current = $seasons[$i] ?? null;
        if ($current !== $prev + 1) {
            $isOpen = $show_open_ended && $prev === $latest_season;
            if ($isOpen) {
                $ranges[] = "{$start}シーズン〜";
            } elseif ($start === $prev) {
                $ranges[] = "{$start}シーズン";
            } else {
                $ranges[] = "{$start}-{$prev}シーズン";
            }
            $start = $current;
        }
        $prev = $current;
    }
    return implode('、', $ranges);
}

function get_current_titleholders($pdo) {
    // id => [title1, title2, ...]（現在保持している全タイトル）
    $stmt = $pdo->query(
        "SELECT title, holder_id, holder_name
         FROM title_history t1
         WHERE season = (SELECT MAX(season) FROM title_history t2 WHERE t2.title = t1.title)
         GROUP BY title"
    );
    $byId = [];
    $byName = [];
    foreach ($stmt->fetchAll() as $row) {
        if ($row['holder_id']) {
            $byId[$row['holder_id']][] = $row['title'];
        }
        if ($row['holder_name']) {
            $byName[$row['holder_name']][] = $row['title'];
        }
    }
    return ['by_id' => $byId, 'by_name' => $byName];
}

// 新人リーグ在籍中の個体は仮ID（例：CCNL-015）で管理されており、実際にDリーグへ昇格する際は
// Python側（othello_league/league.py の _build_character_creation_individual）が
// 別の恒久ID「CC{昇格したシーズン番号}-{昇格順:03d}」を新たに発行する
// （newcomer_league_standings.individual_idは仮IDのまま更新されないため、そのまま個体詳細への
// リンクに使うと昇格後も「見つかりません」になる）。
// 昇格順は新人リーグでの順位（rank）と一致するため、promoted=1の行についてはこの命名規則から
// 実IDを逆算できる。ただし命名規則はあくまで実装上の慣習でしかないため、逆算したIDと同じ
// 表示名の個体が実在する場合のみ real_individual_id として付与する（実在確認に失敗した場合は
// 単にリンクしないだけで、誤った個体へ誘導することはない）
function resolve_newcomer_real_ids($pdo, $rows) {
    $candidateIdByIndex = [];
    foreach ($rows as $i => $r) {
        if (empty($r['promoted'])) continue;
        $candidateIdByIndex[$i] = sprintf('CC%d-%03d', (int)$r['for_season'], (int)$r['rank'] - 1);
    }
    if (!$candidateIdByIndex) return $rows;

    $ids = array_values(array_unique($candidateIdByIndex));
    $ph = implode(',', array_fill(0, count($ids), '?'));
    $stmt = $pdo->prepare(
        "SELECT id, display_name FROM individuals WHERE id IN ($ph)
         UNION ALL
         SELECT id, display_name FROM retired_archive WHERE id IN ($ph)"
    );
    $stmt->execute(array_merge($ids, $ids));
    $nameById = [];
    foreach ($stmt->fetchAll() as $row) { $nameById[$row['id']] = $row['display_name']; }

    foreach ($candidateIdByIndex as $i => $candidateId) {
        if (isset($nameById[$candidateId]) && $nameById[$candidateId] === $rows[$i]['display_name']) {
            $rows[$i]['real_individual_id'] = $candidateId;
        }
    }
    return $rows;
}

// 4大タイトル（青龍・朱雀・白虎・玄武）。旧タイトル名（陸王・海王・空王）はシミュレーション側で
// 移行済みのため、サイト側はこの新名称のみを扱う
const TITLE_NAMES_LIST = ['青龍', '朱雀', '白虎', '玄武'];

// 永世称号の判定基準（タイトルごとに異なる）
//   青龍＝通算5期（連続でなくてよい）
//   朱雀＝連続5期 または 通算10期
//   白虎＝連続5期
//   玄武＝通算10期（連続でなくてよい）
function is_eternal_title($title, $total, $consec) {
    if ($title === '青龍') return $total >= 5;
    if ($title === '朱雀') return $consec >= 5 || $total >= 10;
    if ($title === '玄武') return $total >= 10;
    return $consec >= 5; // 白虎
}

// 段位（将棋の段位制度を模した表示専用のランキング指標）。新規のテーブル・状態は持たず、
// 既存の対局結果（matches）・リーグ経験（standings）・タイトル実績（title_history）から
// 都度算出する。基準（キリのいい数字での暫定運用）：
//   勝数：新人は4段スタート。通算勝数（タイトル戦を含む全対局）に応じて9段まで昇段
//     4→5段:30勝  5→6段:+40(累計70)  6→7段:+50(累計120)  7→8段:+70(累計190)  8→9段:+100(累計290)
//   リーグ経験：Cリーグ経験で5段、Bリーグ経験で6段、Aリーグ経験で7段（勝数による段位の方が
//     高ければそちらを優先。あくまで下限＝フロアとして働く）
//   タイトル：青龍通算1期以上、または他タイトル（朱雀・白虎・玄武）通算2期以上で8段。
//     いずれかのタイトルで永世称号（is_eternal_title）を得ていれば9段
//   これらのうち最も高いものを最終的な段位とする（各基準は下限＝一度上がれば下がらない）
const DAN_WIN_THRESHOLDS = [9 => 290, 8 => 190, 7 => 120, 6 => 70, 5 => 30];
const DAN_LEAGUE_FLOOR = ['A' => 7, 'B' => 6, 'C' => 5];

function calc_dan($totalWins, array $leaguesReached, array $titleTotalsByTitle, $hasEternalTitle) {
    $dan = 4;
    foreach (DAN_WIN_THRESHOLDS as $d => $needWins) {
        if ($totalWins >= $needWins) { $dan = max($dan, $d); break; }
    }
    foreach (DAN_LEAGUE_FLOOR as $league => $floor) {
        if (in_array($league, $leaguesReached, true)) { $dan = max($dan, $floor); break; }
    }
    $seiryu = $titleTotalsByTitle['青龍'] ?? 0;
    $others = ($titleTotalsByTitle['朱雀'] ?? 0) + ($titleTotalsByTitle['白虎'] ?? 0) + ($titleTotalsByTitle['玄武'] ?? 0);
    if ($seiryu >= 1 || $others >= 2) $dan = max($dan, 8);
    if ($hasEternalTitle) $dan = max($dan, 9);
    return min($dan, 9);
}

// タイトル履歴の「保持シーズン一覧」（ソート済み）から、連続保持の最大値を求める。
// title_history由来のシーズン番号は、保持者交代があれば必ず歯抜けになるため、
// 単純に「並んだ整数の最長連続区間」を数えるだけで、保持者ごとの連続記録の最大値と等価になる
function max_consecutive_run(array $sortedSeasons) {
    if (empty($sortedSeasons)) return 0;
    $best = 1; $cur = 1;
    for ($i = 1; $i < count($sortedSeasons); $i++) {
        $cur = ($sortedSeasons[$i] === $sortedSeasons[$i - 1] + 1) ? $cur + 1 : 1;
        $best = max($best, $cur);
    }
    return $best;
}

// 複数個体分の段位をまとめて算出する（1個体ずつ算出する場合と違い、通算勝数・経験リーグ・
// タイトル実績のクエリをそれぞれ1回で済ませる）。$currentLeagueById（省略可）を渡すと、
// standingsにまだ反映されていない「現在の所属リーグ」もフロアとして加味する。
// 戻り値：id => 段位（1〜9）
function calc_dan_bulk($pdo, array $ids, array $currentLeagueById = []) {
    if (!$ids) return [];
    $placeholders = implode(',', array_fill(0, count($ids), '?'));

    $recStmt = $pdo->prepare(
        "SELECT pid, SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS win
         FROM (
            SELECT individual_a_id AS pid, result AS outcome
            FROM matches WHERE individual_a_id IN ($placeholders)
            UNION ALL
            SELECT individual_b_id AS pid,
                   CASE result WHEN 'win' THEN 'loss' WHEN 'loss' THEN 'win' ELSE 'draw' END AS outcome
            FROM matches WHERE individual_b_id IN ($placeholders)
         ) AS all_games
         GROUP BY pid"
    );
    $recStmt->execute(array_merge($ids, $ids));
    $winById = [];
    foreach ($recStmt->fetchAll() as $r) { $winById[$r['pid']] = (int)$r['win']; }

    $lrStmt = $pdo->prepare(
        "SELECT DISTINCT individual_id, league FROM standings
         WHERE individual_id IN ($placeholders) AND league IN ('A', 'B', 'C', 'D')"
    );
    $lrStmt->execute($ids);
    $leaguesById = [];
    foreach ($lrStmt->fetchAll() as $lr) { $leaguesById[$lr['individual_id']][] = $lr['league']; }
    foreach ($currentLeagueById as $id => $league) {
        if (in_array($league, ['A', 'B', 'C', 'D'], true)) $leaguesById[$id][] = $league;
    }

    $thStmt = $pdo->query(
        "SELECT th.season, th.title, COALESCE(th.holder_id, i.id, ra.id) AS holder_id
         FROM title_history th
         LEFT JOIN individuals i ON i.display_name = th.holder_name
         LEFT JOIN retired_archive ra ON ra.display_name = th.holder_name
         ORDER BY th.title, th.season ASC"
    );
    $titleCountsByTitle = [];
    $maxStreak = [];
    $streakId = null; $streakTitle = null; $streakCount = 0;
    foreach ($thStmt->fetchAll() as $row) {
        $hid = $row['holder_id'];
        if ($hid !== null) {
            $titleCountsByTitle[$hid][$row['title']] = ($titleCountsByTitle[$hid][$row['title']] ?? 0) + 1;
        }
        if ($row['title'] !== $streakTitle || $hid !== $streakId) {
            $streakTitle = $row['title']; $streakId = $hid; $streakCount = 1;
        } else {
            $streakCount++;
        }
        if ($hid !== null && (!isset($maxStreak[$hid][$row['title']]) || $streakCount > $maxStreak[$hid][$row['title']])) {
            $maxStreak[$hid][$row['title']] = $streakCount;
        }
    }
    $eternalIds = [];
    foreach ($titleCountsByTitle as $hid => $byTitle) {
        foreach ($byTitle as $title => $total) {
            if (is_eternal_title($title, $total, $maxStreak[$hid][$title] ?? 0)) { $eternalIds[$hid] = true; break; }
        }
    }

    $danById = [];
    foreach ($ids as $id) {
        $danById[$id] = calc_dan(
            $winById[$id] ?? 0, $leaguesById[$id] ?? [], $titleCountsByTitle[$id] ?? [], !empty($eternalIds[$id])
        );
    }
    return $danById;
}

// 年齢＝初期年齢＋通算シーズン数。initial_ageが未取込（過去データ移行前）の個体はnullを返す
function compute_age($initial_age, $total_seasons) {
    if ($initial_age === null) return null;
    return (int)$initial_age + (int)$total_seasons;
}

// タイトル戦ブラケット（白虎・玄武・朱雀予選）の参加者に添える補足情報を組み立てる。
// 全タイトル共通で「名前の左にリーグアイコン（タイトル保持者の場合はタイトルアイコン）、
// 右に(Elo)」の表示に揃えるため、その季のstandings（順位表）時点の所属リーグ・Eloと、
// この季の予選・挑戦者決定トーナメントが始まった時点（＝前季終了時点）でのタイトル保持状況
// （title_history）をあわせて返す
// （対局結果とは別にstandings/title_historyから逆算できるので、Python側にデータを追加しなくてよい）。
// タイトルはあえて「その季のtitle_history」ではなく「前季のtitle_history」から引く：
// 玄武戦の挑戦者決定トーナメントのように、このブラケット自体がその季のタイトル在位者を
// 決める対局そのものであるケースでは、対局開始時点ではまだ挑戦者は在位者ではないため、
// 対局後（その季のtitle_history確定後）の状態を参照すると、奪取した本人が
// トーナメント中もずっと在位者だったかのように表示されてしまう
// （＝そのトーナメントが始まった時点の肩書ではなくなってしまう）
// 戻り値: [individual_id => ['league' => 'A'|'B'|'C'|'D'|null, 'elo' => 数値|null, 'title' => タイトル名|null]]
function build_bracket_participant_info($pdo, $season, $ids) {
    $info = [];
    if (!$ids) return $info;
    $standStmt = $pdo->prepare(
        "SELECT individual_id, league, elo FROM standings WHERE season = :season"
    );
    $standStmt->execute(['season' => $season]);
    $standById = [];
    foreach ($standStmt->fetchAll() as $sr) { $standById[$sr['individual_id']] = $sr; }

    // 前季終了時点（＝このブラケットの対局が始まった時点）で保持していたタイトル
    // （複数冠の場合はTITLE_NAMES_LISTの順で1つだけ採用）。第1季（前季が存在しない）は
    // 該当行が無いため、誰もタイトルを保持していない扱いになる（実際その通り）
    $titleStmt = $pdo->prepare(
        "SELECT title, holder_id FROM title_history WHERE season = :prev_season AND holder_id IS NOT NULL"
    );
    $titleStmt->execute(['prev_season' => $season - 1]);
    $titlesById = [];
    foreach ($titleStmt->fetchAll() as $t) {
        $titlesById[$t['holder_id']][] = $t['title'];
    }

    foreach ($ids as $id) {
        if (!isset($standById[$id])) continue;
        $heldTitle = null;
        if (isset($titlesById[$id])) {
            foreach (TITLE_NAMES_LIST as $t) {
                if (in_array($t, $titlesById[$id], true)) { $heldTitle = $t; break; }
            }
        }
        $info[$id] = [
            'league' => $standById[$id]['league'],
            'elo' => round((float)$standById[$id]['elo']),
            'title' => $heldTitle,
        ];
    }
    return $info;
}

$pdo = league_db_connect();
$action = $_GET['action'] ?? 'leagues';

switch ($action) {

    // --------------------------------------------------------
    case 'season_awards':
        // 指定シーズン（省略時は最新）の個人成績ランキング（対局数・勝数・勝率・連勝）を返す。
        // 対個体戦（ベンチマーク等ではないもの）のみを対象に、matchesテーブルから直接集計する。
        $season = (int)($_GET['season'] ?? 0);
        if ($season <= 0) {
            $season = (int)($pdo->query("SELECT MAX(season) AS m FROM standings")->fetch()['m'] ?? 0);
        }
        if ($season <= 0) {
            json_out(['season' => 0, 'min_games_for_rate' => 0, 'games' => [], 'wins' => [], 'win_rate' => [], 'streak' => []]);
            break;
        }

        $mStmt = $pdo->prepare(
            "SELECT m.id, m.individual_a_id, m.individual_b_id, m.result,
                    COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS a_name,
                    COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS b_name
             FROM matches m
             LEFT JOIN individuals ia ON ia.id = m.individual_a_id
             LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
             LEFT JOIN individuals ib ON ib.id = m.individual_b_id
             LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
             WHERE m.season = :s
             ORDER BY m.id ASC"
        );
        $mStmt->execute(['s' => $season]);

        $stats = []; // individual_id => 集計
        foreach ($mStmt->fetchAll() as $m) {
            if ($m['individual_b_id'] === null) continue; // ベンチマーク等、対個体戦でないものは対象外
            $aId = $m['individual_a_id']; $bId = $m['individual_b_id'];
            foreach ([[$aId, $m['a_name']], [$bId, $m['b_name']]] as $p) {
                if (!isset($stats[$p[0]])) {
                    $stats[$p[0]] = ['id' => $p[0], 'name' => $p[1], 'games' => 0, 'win' => 0, 'loss' => 0, 'draw' => 0, 'cur_streak' => 0, 'best_streak' => 0];
                }
            }
            $stats[$aId]['games']++; $stats[$bId]['games']++;
            if ($m['result'] === 'win') {
                $stats[$aId]['win']++; $stats[$bId]['loss']++;
                $stats[$aId]['cur_streak']++; $stats[$bId]['cur_streak'] = 0;
            } elseif ($m['result'] === 'loss') {
                $stats[$aId]['loss']++; $stats[$bId]['win']++;
                $stats[$bId]['cur_streak']++; $stats[$aId]['cur_streak'] = 0;
            } else {
                $stats[$aId]['draw']++; $stats[$bId]['draw']++;
                $stats[$aId]['cur_streak'] = 0; $stats[$bId]['cur_streak'] = 0;
            }
            $stats[$aId]['best_streak'] = max($stats[$aId]['best_streak'], $stats[$aId]['cur_streak']);
            $stats[$bId]['best_streak'] = max($stats[$bId]['best_streak'], $stats[$bId]['cur_streak']);
        }

        $all = array_values($stats);
        foreach ($all as &$p) { unset($p['cur_streak']); }
        unset($p);

        $MIN_GAMES_FOR_RATE = 5; // 対局数が極端に少ない個体が100%勝率で上位独占するのを防ぐための足切り

        $gamesRank = $all;
        usort($gamesRank, fn($a, $b) => $b['games'] <=> $a['games']);

        $winsRank = $all;
        usort($winsRank, fn($a, $b) => $b['win'] <=> $a['win']);

        $rateRank = array_values(array_filter($all, fn($p) => ($p['win'] + $p['loss']) >= $MIN_GAMES_FOR_RATE));
        foreach ($rateRank as &$p) { $p['win_rate'] = round($p['win'] / ($p['win'] + $p['loss']), 4); }
        unset($p);
        usort($rateRank, fn($a, $b) => $b['win_rate'] <=> $a['win_rate']);

        $streakRank = $all;
        usort($streakRank, fn($a, $b) => $b['best_streak'] <=> $a['best_streak']);

        // シーズン終了時点のEloランキング（standingsテーブルに記録された、その季終了時点のElo値を使う）。
        // A〜D以外（朱雀紅白リーグ）の記録も同じテーブルにあるため、league絞り込みが無いと
        // 紅白リーグ参加者が二重に並んでしまう
        $eloStmt = $pdo->prepare(
            "SELECT individual_id AS id, display_name AS name, elo
             FROM standings WHERE season = :s AND elo IS NOT NULL AND league IN ('A', 'B', 'C', 'D')
             ORDER BY elo DESC"
        );
        $eloStmt->execute(['s' => $season]);
        $eloRank = $eloStmt->fetchAll();
        foreach ($eloRank as &$e) { $e['elo'] = round((float)$e['elo'], 1); }
        unset($e);

        json_out([
            'season' => $season,
            'min_games_for_rate' => $MIN_GAMES_FOR_RATE,
            'games' => $gamesRank,
            'wins' => $winsRank,
            'win_rate' => $rateRank,
            'streak' => $streakRank,
            'elo_end' => $eloRank,
        ]);
        break;

    // --------------------------------------------------------
    case 'leagues':
        // 現役ロスターを4リーグ分まとめて返す。
        // 並び順は「直近シーズンの順位を継承」：残留者はその順位のまま、
        // 昇格者は昇格先の最下位、降格者は降格先の最上位、新規参入者はDの最下位に接続する。
        $latest = $pdo->query("SELECT MAX(season) AS m FROM standings")->fetch();
        $latest_season = (int)($latest['m'] ?? 0);

        $indStmt = $pdo->query(
            "SELECT id, display_name, league, elo_rating, generation,
                    seasons_in_league, total_seasons, initial_age
             FROM individuals WHERE retired = 0"
        );
        $individualsById = [];
        foreach ($indStmt->fetchAll() as $row) {
            $row['elo_rating'] = round((float)$row['elo_rating'], 1);
            $row['age'] = compute_age($row['initial_age'], $row['total_seasons']);
            $individualsById[$row['id']] = $row;
        }

        // 段位（リーグタブでは、タイトル非保持者の名前右に表示する）
        $currentLeagueById = array_map(fn($r) => $r['league'], $individualsById);
        $danById = calc_dan_bulk($pdo, array_keys($individualsById), $currentLeagueById);
        foreach ($individualsById as $iid => &$ind) { $ind['dan'] = $danById[$iid] ?? 4; }
        unset($ind);

        $byLeague = ["A" => [], "B" => [], "C" => [], "D" => []];
        $placed = [];
        $standRows = [];

        if ($latest_season > 0) {
            // 直近シーズンの記録を取得し、「今のリーグに残留している人だけ」を順位順に並べる
            $standStmt = $pdo->prepare(
                "SELECT league, `rank`, individual_id, movement, no_roundrobin FROM standings
                 WHERE season = :season ORDER BY FIELD(league,'A','B','C','D'), `rank` ASC"
            );
            $standStmt->execute(['season' => $latest_season]);
            $standRows = $standStmt->fetchAll();

            foreach ($standRows as $s) {
                $iid = $s['individual_id'];
                if (!isset($individualsById[$iid])) continue; // 引退済み等
                $currentLeague = $individualsById[$iid]['league'];
                // 残留者：現在のリーグと、直近シーズンの記録上のリーグが一致し、
                // かつ昇格・降格・引退のいずれでもない場合（季1組の"new"単体タグも、
                // リーグを移動していない限りはここに含める。完全一致(==='stay')だけで判定すると
                // 季1組が全員movemententa='new'のため誰も残留者に乗らず、
                // 順序が崩れてしまうため、部分一致の除外方式に変更した）
                $hasMoved = str_contains($s['movement'], 'promoted')
                         || str_contains($s['movement'], 'relegated')
                         || str_contains($s['movement'], 'retired');
                if ($currentLeague === $s['league'] && !$hasMoved) {
                    $byLeague[$currentLeague][] = $individualsById[$iid];
                    $placed[$iid] = true;
                }
            }

            // 昇格者：昇格先リーグの最下位に追加（「新規」と複合の場合も含む）
            foreach ($standRows as $s) {
                $iid = $s['individual_id'];
                if (isset($placed[$iid]) || !isset($individualsById[$iid])) continue;
                if (str_contains($s['movement'], 'promoted')) {
                    $byLeague[$individualsById[$iid]['league']][] = $individualsById[$iid];
                    $placed[$iid] = true;
                }
            }
            // 降格者：降格先リーグの最上位に挿入（「新規」と複合の場合も含む）
            foreach ($standRows as $s) {
                $iid = $s['individual_id'];
                if (isset($placed[$iid]) || !isset($individualsById[$iid])) continue;
                if (str_contains($s['movement'], 'relegated')) {
                    array_unshift($byLeague[$individualsById[$iid]['league']], $individualsById[$iid]);
                    $placed[$iid] = true;
                }
            }
        }

        // 新規参入者（＝このシーズンが初参戦）の扱い：
        //   1シーズン目（創設メンバー）は、全員が横一線のスタートなので今季の成績順にそのまま並べる
        //   2シーズン目以降の新弟子は、既存メンバーがいる中への割り込みなので、実力に関わらず最下位からスタートする
        //   （昇格・降格を伴わない「新規のみ」の個体がここに該当する。複合の場合は上のpromoted/relegatedで既に配置済み）
        $new_entries = [];
        foreach ($standRows as $s) {
            $iid = $s['individual_id'];
            if (isset($placed[$iid]) || !isset($individualsById[$iid])) continue;
            if (str_contains($s['movement'], 'new')) {
                $new_entries[] = ['ind' => $individualsById[$iid], 'rank' => (int)($s['rank'] ?? 999)];
                $placed[$iid] = true;
            }
        }
        if ($latest_season <= 1) {
            usort($new_entries, fn($a, $b) => $a['rank'] <=> $b['rank']);
        }
        foreach ($new_entries as $e) {
            $e['ind']['is_new'] = ($latest_season > 1); // 創設メンバーには「新」マークを付けない
            $byLeague[$e['ind']['league']][] = $e['ind'];
        }

        // それでも記録が無かった個体（イレギュラーな場合の保険）は、現在リーグの最下位に追加
        foreach ($individualsById as $iid => $ind) {
            if (isset($placed[$iid])) continue;
            $ind['is_new'] = true;
            $byLeague[$ind['league']][] = $ind;
        }

        // 青龍在位者は、Aリーグの通常順位リストから分離する（防衛専念枠のため、番号付き順位を振らない）。
        // 「リーグタブ」は"次シーズンに向けた現在の状態"を見せる画面なので、判定は
        // title_historyの"現在の（最新の）青龍保持者"を無条件で分離する（過去に奪取か防衛かは問わない）。
        // 現保持者は、理由を問わず必ず次シーズンの総当たりを休むことになるため。
        // （standingsのno_roundrobinは"過去のあるシーズンで実際に何が起きたか"の記録であり、
        //   "これから何が起きるか"を示すものではないため、リーグタブの判定には使わない。
        //   no_roundrobinは「順位」タブ側で、過去シーズンの実際の状態を示すために使う）
        // 変数名・JSONキー名は既存互換のため rikuou のまま（中身は青龍在位者を指す）
        $titleholders = get_current_titleholders($pdo);
        $rikuou_id = null;
        foreach ($titleholders['by_id'] as $tid => $titles) {
            if (in_array('青龍', $titles)) { $rikuou_id = $tid; break; }
        }

        $rikuou_entry = null;
        if ($rikuou_id) {
            foreach ($byLeague['A'] as $i => $ind) {
                if ($ind['id'] === $rikuou_id) {
                    $rikuou_entry = $ind;
                    array_splice($byLeague['A'], $i, 1);
                    break;
                }
            }
        }

        // 朱雀戦（紅白リーグ）：前季の確定成績（勝敗）ではなく、前季末の陥落・入れ替え戦の
        // 結果を反映した「今季（来季開始時点）のメンバー」を一覧で表示する。紅白の組分け自体は
        // Python側でランダムに行われ対局結果からは再現できないため、season_state.json由来の
        // 現在の組分け（suzaku_league_json）をそのまま使い、各個体の現在のリーグ・Eloを添える。
        // 前季の紅白リーグに在籍していなかった個体（＝入れ替え戦を勝ち上がって今季から新規加入）
        // には新人マークを付けられるよう、前季在籍者のID集合も添えて返す
        // このブロックで想定外の例外が起きても、リーグタブ全体（A〜D）がHTTP 500で
        // 丸ごと落ちることがないよう、必ずcatchしてsuzaku_membersを空のまま続行する。
        // デバッグ用に例外メッセージも一時的に返す（原因判明後は削除する）
        $suzakuMembers = [];
        $suzakuDebugError = null;
        try {
            $suzakuPrevIds = [];
            $suzakuStateRow = $pdo->query("SELECT suzaku_league_json FROM season_state WHERE id = 1")->fetch();
            $suzakuLeagueState = $suzakuStateRow && $suzakuStateRow['suzaku_league_json']
                ? json_decode($suzakuStateRow['suzaku_league_json'], true) : null;
            if ($suzakuLeagueState) {
                $memberRow = function ($id, $group) use ($individualsById) {
                    $ind = $individualsById[$id] ?? null;
                    if (!$ind) return null;
                    return [
                        'individual_id' => $id, 'display_name' => $ind['display_name'],
                        'league' => $ind['league'], 'elo' => $ind['elo_rating'], 'group' => $group,
                    ];
                };
                foreach (($suzakuLeagueState['red'] ?? []) as $id) {
                    $row = $memberRow($id, 'red');
                    if ($row) $suzakuMembers[] = $row;
                }
                foreach (($suzakuLeagueState['white'] ?? []) as $id) {
                    $row = $memberRow($id, 'white');
                    if ($row) $suzakuMembers[] = $row;
                }

                if ($latest_season > 0) {
                    $prevStmt = $pdo->prepare(
                        "SELECT individual_id FROM suzaku_group_standings
                         WHERE season = :season AND league IN ('朱雀紅組', '朱雀白組') AND no_roundrobin = 0"
                    );
                    $prevStmt->execute(['season' => $latest_season]);
                    $suzakuPrevIds = array_column($prevStmt->fetchAll(), 'individual_id');
                }
                $suzakuPrevIdSet = array_flip($suzakuPrevIds);

                // 連続在籍季（来季開始時点で「◯季目」になるか）：防衛専念枠（no_roundrobin）で
                // 在籍していた季も含め、suzaku_group_standingsに記録されている全季を対象に、
                // 直近シーズンから遡って何季連続で在籍しているかを数える
                $szHistStmt = $pdo->query(
                    "SELECT season, individual_id FROM suzaku_group_standings
                     WHERE league IN ('朱雀紅組', '朱雀白組') ORDER BY individual_id, season ASC"
                );
                $suzakuSeasonsById = [];
                foreach ($szHistStmt->fetchAll() as $row) {
                    $suzakuSeasonsById[$row['individual_id']][] = (int)$row['season'];
                }

                foreach ($suzakuMembers as &$m) {
                    $m['is_new'] = $latest_season > 0 && !isset($suzakuPrevIdSet[$m['individual_id']]);
                    $seasonSet = array_flip($suzakuSeasonsById[$m['individual_id']] ?? []);
                    $streak = 0;
                    for ($s = $latest_season; isset($seasonSet[$s]); $s--) { $streak++; }
                    $m['seasons_in_league'] = $streak + 1; // 来季で+1季目になる
                }
                unset($m);

                // 新メンバーを一番下にする（紅組→白組という既存の並びは、新メンバー以外は維持する）
                $suzakuMembers = array_merge(
                    array_values(array_filter($suzakuMembers, fn($m) => !$m['is_new'])),
                    array_values(array_filter($suzakuMembers, fn($m) => $m['is_new']))
                );
            }
        } catch (\Throwable $e) {
            $suzakuMembers = [];
            $suzakuDebugError = $e->getMessage() . ' @ ' . $e->getFile() . ':' . $e->getLine();
        }

        json_out([
            'leagues' => $byLeague, 'rikuou' => $rikuou_entry, 'titleholders' => $titleholders,
            'next_season' => $latest_season + 1,
            'suzaku_members' => $suzakuMembers,
            'suzaku_debug_error' => $suzakuDebugError,
        ]);
        break;

    // --------------------------------------------------------
    case 'clans':
        // 一門ごとの集計：開祖の名前・総人数・現役人数・歴代最高Elo。
        // 開祖本人を除いて弟子が2名以上いない一門は、まだ「一門」として成立していないので一覧から除外する
        // （member_countには開祖本人も含まれるため、弟子2名以上＝総人数3名以上が条件）
        $stmt = $pdo->query(
            "SELECT clan_root_id,
                    COUNT(*) AS member_count,
                    SUM(CASE WHEN retired = 0 THEN 1 ELSE 0 END) AS active_count,
                    MAX(peak_elo) AS best_peak_elo
             FROM (
                SELECT clan_root_id, peak_elo, 0 AS retired FROM individuals WHERE clan_root_id IS NOT NULL AND retired = 0
                UNION ALL
                SELECT clan_root_id, peak_elo, 1 AS retired FROM retired_archive WHERE clan_root_id IS NOT NULL
             ) AS all_members
             GROUP BY clan_root_id
             HAVING COUNT(*) >= 3
             ORDER BY best_peak_elo DESC"
        );
        $rows = $stmt->fetchAll();

        $founderIds = array_column($rows, 'clan_root_id');
        $founderMap = [];
        if ($founderIds) {
            $ph = implode(',', array_fill(0, count($founderIds), '?'));
            $fStmt = $pdo->prepare(
                "SELECT id, display_name, 0 AS retired FROM individuals WHERE id IN ($ph)
                 UNION ALL
                 SELECT id, display_name, 1 AS retired FROM retired_archive WHERE id IN ($ph)"
            );
            $fStmt->execute(array_merge($founderIds, $founderIds));
            foreach ($fStmt->fetchAll() as $row) { $founderMap[$row['id']] = $row; }
        }

        $clans = [];
        foreach ($rows as $r) {
            $founder = $founderMap[$r['clan_root_id']] ?? null;
            $clans[] = [
                'clan_root_id' => $r['clan_root_id'],
                'founder_name' => $founder['display_name'] ?? $r['clan_root_id'],
                'founder_retired' => $founder ? (int)$founder['retired'] : null,
                'member_count' => (int)$r['member_count'],
                'active_count' => (int)$r['active_count'],
                'best_peak_elo' => round((float)$r['best_peak_elo'], 1),
            ];
        }
        json_out(['clans' => $clans]);
        break;

    // --------------------------------------------------------
    case 'clan':
        // 一門の詳細：全メンバー（現役・引退問わず）をフラットに返す。
        // 系譜ツリーはフロント側でparent_a_idを辿って組み立てる
        $clanId = $_GET['id'] ?? '';
        if ($clanId === '') json_error('id パラメータが必要です');
        $stmt = $pdo->prepare(
            "SELECT id, display_name, league, elo_rating, peak_elo, parent_a_id, generation, 0 AS retired
             FROM individuals WHERE clan_root_id = :id AND retired = 0
             UNION ALL
             SELECT id, display_name, league, elo_rating, peak_elo, parent_a_id, NULL AS generation, 1 AS retired
             FROM retired_archive WHERE clan_root_id = :id2"
        );
        $stmt->execute(['id' => $clanId, 'id2' => $clanId]);
        $members = $stmt->fetchAll();
        if (empty($members)) json_error('一門が見つかりません', 404);

        foreach ($members as &$m) {
            $m['elo_rating'] = round((float)$m['elo_rating'], 1);
            $m['peak_elo'] = round((float)($m['peak_elo'] ?? $m['elo_rating']), 1);
        }
        unset($m);

        // この一門の開祖（=clanId本人）が師匠を持っている場合、それは「分岐」で独立した一門ということ。
        // どの一門からどう分岐したのかを辿れるよう、元の師匠の情報（と、その師匠が今どの一門にいるか）を添える
        $branchedFrom = null;
        $rootMember = null;
        foreach ($members as $m) { if ($m['id'] === $clanId) { $rootMember = $m; break; } }
        if ($rootMember && !empty($rootMember['parent_a_id'])) {
            $masterStmt = $pdo->prepare(
                "SELECT id, display_name, clan_root_id, 0 AS retired FROM individuals WHERE id = :id AND retired = 0
                 UNION ALL
                 SELECT id, display_name, clan_root_id, 1 AS retired FROM retired_archive WHERE id = :id2
                 LIMIT 1"
            );
            $masterStmt->execute(['id' => $rootMember['parent_a_id'], 'id2' => $rootMember['parent_a_id']]);
            $master = $masterStmt->fetch();
            if ($master) {
                $branchedFrom = [
                    'master_id' => $master['id'],
                    'master_name' => $master['display_name'],
                    'master_retired' => (int)$master['retired'],
                    'origin_clan_root_id' => $master['clan_root_id'],
                ];
            }
        }

        // この一門のメンバーを師匠に持ちながら、別の一門（分岐先）に属している弟子を探す。
        // 「◯◯の下でLに分岐」のような表示をフロント側で組み立てるために使う。
        // ただし、分岐した本人にまだ弟子が2名以上いない（一門一覧と同じ基準で「一門」として
        // 成立していない）場合は、分岐そのものが確定していないものとして一覧から除外する
        // （弟子がいないのに「独立した」と表示されるのは実態と合わないため）
        $memberIds = array_column($members, 'id');
        $branchesOut = [];
        if ($memberIds) {
            $ph = implode(',', array_fill(0, count($memberIds), '?'));
            $branchStmt = $pdo->prepare(
                "SELECT id, display_name, parent_a_id, clan_root_id, 0 AS retired FROM individuals
                 WHERE parent_a_id IN ($ph) AND clan_root_id != ? AND retired = 0
                 UNION ALL
                 SELECT id, display_name, parent_a_id, clan_root_id, 1 AS retired FROM retired_archive
                 WHERE parent_a_id IN ($ph) AND clan_root_id != ?"
            );
            $branchStmt->execute(array_merge($memberIds, [$clanId], $memberIds, [$clanId]));
            $rawBranches = $branchStmt->fetchAll();

            $branchClanIds = array_values(array_unique(array_column($rawBranches, 'clan_root_id')));
            $establishedClanIds = [];
            if ($branchClanIds) {
                $bph = implode(',', array_fill(0, count($branchClanIds), '?'));
                $countStmt = $pdo->prepare(
                    "SELECT clan_root_id, COUNT(*) AS member_count FROM (
                        SELECT clan_root_id FROM individuals WHERE clan_root_id IN ($bph)
                        UNION ALL
                        SELECT clan_root_id FROM retired_archive WHERE clan_root_id IN ($bph)
                     ) AS all_members
                     GROUP BY clan_root_id
                     HAVING COUNT(*) >= 3"
                );
                $countStmt->execute(array_merge($branchClanIds, $branchClanIds));
                $establishedClanIds = array_column($countStmt->fetchAll(), 'clan_root_id');
                $establishedClanIds = array_flip($establishedClanIds);
            }

            foreach ($rawBranches as $b) {
                if (!isset($establishedClanIds[$b['clan_root_id']])) continue;
                $branchesOut[] = [
                    'under_id' => $b['parent_a_id'],
                    'branch_id' => $b['id'],
                    'branch_name' => $b['display_name'],
                    'branch_clan_root_id' => $b['clan_root_id'],
                    'branch_retired' => (int)$b['retired'],
                ];
            }
        }

        json_out([
            'clan_root_id' => $clanId, 'members' => $members, 'branched_from' => $branchedFrom,
            'branches_out' => $branchesOut,
        ]);
        break;

    // --------------------------------------------------------
    case 'individual':
        $id = $_GET['id'] ?? '';
        if ($id === '') json_error('id パラメータが必要です');
        $stmt = $pdo->prepare("SELECT * FROM individuals WHERE id = :id");
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch();
        if (!$row) {
            // 引退済みの可能性があるのでアーカイブも見る
            $stmt2 = $pdo->prepare("SELECT * FROM retired_archive WHERE id = :id");
            $stmt2->execute(['id' => $id]);
            $row = $stmt2->fetch();
            if (!$row) json_error('個体が見つかりません', 404);
            $row['retired'] = 1;
        }
        $row['params'] = json_decode($row['params_json'], true);
        unset($row['params_json']);
        $row['elo_rating'] = round((float)$row['elo_rating'], 1);
        $row['age'] = compute_age($row['initial_age'] ?? null, $row['total_seasons'] ?? 0);

        // 所属する一門が「一門」として成立しているか（一門一覧と同じ基準：開祖含め3名以上＝
        // 弟子2名以上）。成立していない場合、フロント側では「一門を見る」リンクを出さない
        // （分岐直後でまだ弟子がいない個体を、実態のない一門の開祖であるかのように見せないため）
        $row['clan_established'] = false;
        if (!empty($row['clan_root_id'])) {
            $ccStmt = $pdo->prepare(
                "SELECT COUNT(*) FROM (
                    SELECT clan_root_id FROM individuals WHERE clan_root_id = :id1
                    UNION ALL
                    SELECT clan_root_id FROM retired_archive WHERE clan_root_id = :id2
                 ) AS all_members"
            );
            $ccStmt->execute(['id1' => $row['clan_root_id'], 'id2' => $row['clan_root_id']]);
            $row['clan_established'] = ((int)$ccStmt->fetchColumn()) >= 3;
        }

        // 系譜：師匠 → その師匠 → ...と、辿れるところまで遡る（開祖・新規開祖で親がいなければ空配列）。
        // elo_ratingは、引退済みならretired_archiveの値（＝引退時点のelo）、現役なら現在のeloになる。
        // 循環参照が万一発生しても無限ループしないよう、訪問済みIDの記録と深さ上限で保険をかける。
        $ancestors = [];
        $curParentId = $row['parent_a_id'] ?? null;
        $visitedIds = [$id => true];
        while (!empty($curParentId) && count($ancestors) < 200) {
            if (isset($visitedIds[$curParentId])) break;
            $visitedIds[$curParentId] = true;
            $ancStmt = $pdo->prepare(
                "SELECT id, display_name, elo_rating, parent_a_id, 0 AS retired FROM individuals WHERE id = :id
                 UNION ALL
                 SELECT id, display_name, elo_rating, parent_a_id, 1 AS retired FROM retired_archive WHERE id = :id2
                 LIMIT 1"
            );
            $ancStmt->execute(['id' => $curParentId, 'id2' => $curParentId]);
            $anc = $ancStmt->fetch();
            if (!$anc) break;
            $anc['elo_rating'] = round((float)$anc['elo_rating'], 1);
            $curParentId = $anc['parent_a_id'];
            unset($anc['parent_a_id']);
            $ancestors[] = $anc;
        }
        $parent = $ancestors[0] ?? null;

        // 弟子一覧：この個体が親として生成した個体（引退済みも含む）。
        // individualsテーブルはretired=1の行も削除されずretired_archiveに二重登録されるため、
        // individuals側はretired=0に絞らないと同じ個体が2回表示されてしまう
        $childrenStmt = $pdo->prepare(
            "SELECT id, display_name, league, elo_rating, peak_elo, 0 AS retired FROM individuals
             WHERE parent_a_id = :id AND retired = 0
             UNION ALL
             SELECT id, display_name, league, elo_rating, peak_elo, 1 AS retired FROM retired_archive
             WHERE parent_a_id = :id2
             ORDER BY retired ASC, id ASC"
        );
        $childrenStmt->execute(['id' => $id, 'id2' => $id]);
        $children = $childrenStmt->fetchAll();
        foreach ($children as &$ch) {
            $ch['elo_rating'] = round((float)$ch['elo_rating'], 1);
            $ch['peak_elo'] = round((float)($ch['peak_elo'] ?? $ch['elo_rating']), 1);
        }
        unset($ch);

        // 通算成績（全対局、勝敗分け）
        $recordStmt = $pdo->prepare(
            "SELECT individual_a_id, result FROM matches
             WHERE individual_a_id = :idA OR individual_b_id = :idB"
        );
        $recordStmt->execute(['idA' => $id, 'idB' => $id]);
        $record = ['total' => 0, 'win' => 0, 'loss' => 0, 'draw' => 0];
        foreach ($recordStmt->fetchAll() as $m) {
            $result = $m['result'];
            if ($m['individual_a_id'] !== $id) {
                $result = ['win' => 'loss', 'loss' => 'win', 'draw' => 'draw'][$result] ?? $result;
            }
            $record['total']++;
            if (isset($record[$result])) $record[$result]++;
        }

        // 対戦相手別・同一カード成績（通算、全対局対象。表示は多い順）
        // 集計はSQLのGROUP BYではなくPHP側で行う（ANY_VALUE()未対応の環境でも確実に動くように）
        $oppRawStmt = $pdo->prepare(
            "SELECT m.individual_a_id, m.individual_b_id, m.result,
                    COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS individual_a_name,
                    COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS individual_b_name
             FROM matches m
             LEFT JOIN individuals ia ON ia.id = m.individual_a_id
             LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
             LEFT JOIN individuals ib ON ib.id = m.individual_b_id
             LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
             WHERE m.individual_a_id = :idA OR m.individual_b_id = :idB"
        );
        $oppRawStmt->execute(['idA' => $id, 'idB' => $id]);
        $opponentMap = []; // pid => ['opponent_name'=>, 'win'=>, 'loss'=>, 'draw'=>]
        foreach ($oppRawStmt->fetchAll() as $m) {
            $isA = ($m['individual_a_id'] === $id);
            $pid = $isA ? $m['individual_b_id'] : $m['individual_a_id'];
            $opponentName = $isA ? $m['individual_b_name'] : $m['individual_a_name'];
            $outcome = $m['result'];
            if (!$isA) {
                $outcome = ['win' => 'loss', 'loss' => 'win', 'draw' => 'draw'][$outcome] ?? $outcome;
            }
            if (!isset($opponentMap[$pid])) {
                $opponentMap[$pid] = ['pid' => $pid, 'opponent_name' => $opponentName, 'win' => 0, 'loss' => 0, 'draw' => 0];
            }
            if (isset($opponentMap[$pid][$outcome])) $opponentMap[$pid][$outcome]++;
        }
        $opponentRecords = array_values($opponentMap);
        usort($opponentRecords, fn($a, $b) => ($b['win'] + $b['loss'] + $b['draw']) <=> ($a['win'] + $a['loss'] + $a['draw']));

        // タイトル獲得歴：保持していたシーズンの一覧から、連続区間をまとめて表示する
        // （例：「1-3シーズン、5シーズン〜」のように。奪取/防衛の区別は表示しない）。
        // あわせて、段位（$row['dan']）のタイトルフロア・永世称号判定に使う
        // 「タイトル別通算期数」「永世称号を得ているか」もここで一緒に集計する
        $titleRanges = [];
        $titleTotalsByTitle = [];
        $hasEternalTitle = false;
        // 段位履歴用：タイトルごとの保持季一覧・青龍の初獲得季・他タイトルの(季,タイトル)一覧を集める
        $seiryuFirstSeason = null;
        $otherTitleEvents = []; // [[season, title], ...]（朱雀・白虎・玄武）
        $danCandidates = []; // [['season'=>, 'dan'=>, 'reason'=>], ...]
        foreach (TITLE_NAMES_LIST as $title) {
            $holdStmt = $pdo->prepare(
                "SELECT season, holder_id, holder_name FROM title_history
                 WHERE title = :title ORDER BY season ASC"
            );
            $holdStmt->execute(['title' => $title]);
            $allSeasons = $holdStmt->fetchAll();
            $mySeasons = [];
            $lastSeason = 0;
            foreach ($allSeasons as $row2) {
                $isMe = ($row2['holder_id'] && $row2['holder_id'] === $id) || ($row2['holder_name'] === ($row['display_name'] ?? null));
                if ($isMe) $mySeasons[] = (int)$row2['season'];
                $lastSeason = max($lastSeason, (int)$row2['season']);
            }
            if (empty($mySeasons)) continue;
            $rangeText = format_season_ranges($mySeasons, $lastSeason);
            $titleRanges[] = ['title' => $title, 'ranges' => $rangeText, 'total' => count($mySeasons)];
            $titleTotalsByTitle[$title] = count($mySeasons);
            if (is_eternal_title($title, count($mySeasons), max_consecutive_run($mySeasons))) {
                $hasEternalTitle = true;
            }

            if ($title === '青龍') {
                $seiryuFirstSeason = min($mySeasons);
            } else {
                foreach ($mySeasons as $s) { $otherTitleEvents[] = [$s, $title]; }
            }

            // 永世称号：保持季を1季ずつ辿り、is_eternal_title()を初めて満たした季を求める
            $consec = 0; $prevSeason = null;
            foreach ($mySeasons as $idx => $s) {
                $consec = ($prevSeason !== null && $s === $prevSeason + 1) ? $consec + 1 : 1;
                $prevSeason = $s;
                if (is_eternal_title($title, $idx + 1, $consec)) {
                    $danCandidates[] = ['season' => $s, 'dan' => 9, 'reason' => "永世{$title}"];
                    break;
                }
            }
        }
        $titleTotalSeasons = array_sum(array_column($titleRanges, 'total'));
        if ($seiryuFirstSeason !== null) {
            $danCandidates[] = ['season' => $seiryuFirstSeason, 'dan' => 8, 'reason' => '青龍位獲得'];
        }
        if (!empty($otherTitleEvents)) {
            usort($otherTitleEvents, fn($a, $b) => $a[0] <=> $b[0]);
            $cnt = 0;
            foreach ($otherTitleEvents as [$s, $t]) {
                $cnt++;
                if ($cnt >= 2) {
                    $danCandidates[] = ['season' => $s, 'dan' => 8, 'reason' => 'タイトル通算2期'];
                    break;
                }
            }
        }

        // 段位：通算勝数（$record、下で算出）・経験リーグ・タイトル実績から算出する
        $leagueReachedStmt = $pdo->prepare(
            "SELECT DISTINCT league FROM standings WHERE individual_id = :id AND league IN ('A', 'B', 'C', 'D')"
        );
        $leagueReachedStmt->execute(['id' => $id]);
        $leaguesReached = array_column($leagueReachedStmt->fetchAll(), 'league');
        if (in_array($row['league'], ['A', 'B', 'C', 'D'], true)) {
            $leaguesReached[] = $row['league']; // standingsに未反映の現シーズン分も念のため含める
        }
        $row['dan'] = calc_dan((int)$record['win'], $leaguesReached, $titleTotalsByTitle, $hasEternalTitle);

        // 段位履歴：経験リーグ（初めて所属した季）を昇段候補に追加。
        // 今季昇格したばかりでstandingsにまだ反映されていないリーグは、$row['dan']側の
        // フォールバック（下の$leaguesReachedと同じ考え方）に合わせ、直近の確定季の次の季として加える。
        // これをしないと、履歴が「現在の段位」より低い段で止まって見える不具合になる
        $leagueSeasonStmt = $pdo->prepare(
            "SELECT league, MIN(season) AS first_season FROM standings
             WHERE individual_id = :id AND league IN ('A', 'B', 'C', 'D') GROUP BY league"
        );
        $leagueSeasonStmt->execute(['id' => $id]);
        $leagueFirstSeason = [];
        foreach ($leagueSeasonStmt->fetchAll() as $r) { $leagueFirstSeason[$r['league']] = (int)$r['first_season']; }
        if (in_array($row['league'], ['A', 'B', 'C', 'D'], true) && !isset($leagueFirstSeason[$row['league']])) {
            $curSeasonStmt = $pdo->query("SELECT MAX(season) FROM standings");
            $leagueFirstSeason[$row['league']] = (int)$curSeasonStmt->fetchColumn() + 1;
        }
        foreach (DAN_LEAGUE_FLOOR as $lg => $floorDan) {
            if (isset($leagueFirstSeason[$lg])) {
                $danCandidates[] = ['season' => $leagueFirstSeason[$lg], 'dan' => $floorDan, 'reason' => "{$lg}リーグ昇格"];
            }
        }

        // 段位履歴：通算勝数が各閾値を初めて超えた季を昇段候補に追加
        $matchSeasonStmt = $pdo->prepare(
            "SELECT season, individual_a_id, result FROM matches
             WHERE individual_a_id = :idA OR individual_b_id = :idB ORDER BY season ASC"
        );
        $matchSeasonStmt->execute(['idA' => $id, 'idB' => $id]);
        $cumWinBySeason = []; // season => その季終了時点の累計勝数（季昇順のまま）
        $cumWin = 0;
        foreach ($matchSeasonStmt->fetchAll() as $m) {
            $result = $m['result'];
            if ($m['individual_a_id'] !== $id) {
                $result = ['win' => 'loss', 'loss' => 'win', 'draw' => 'draw'][$result] ?? $result;
            }
            if ($result === 'win') $cumWin++;
            $cumWinBySeason[(int)$m['season']] = $cumWin;
        }
        foreach (DAN_WIN_THRESHOLDS as $dan => $needWins) {
            foreach ($cumWinBySeason as $s => $cum) {
                if ($cum >= $needWins) {
                    $danCandidates[] = ['season' => $s, 'dan' => $dan, 'reason' => "通算{$needWins}勝"];
                    break;
                }
            }
        }

        // 段位履歴：デビュー季（4段スタート）を起点に加え、時系列で「それまでの最大値を更新した」
        // 昇段だけを残す（calc_dan()がmax()で決めているのと同じ考え方を、時系列に展開したもの）
        $debutStmt = $pdo->prepare("SELECT MIN(season) FROM standings WHERE individual_id = :id");
        $debutStmt->execute(['id' => $id]);
        $debutSeason = (int)($debutStmt->fetchColumn() ?: 1);
        $danCandidates[] = ['season' => $debutSeason, 'dan' => 4, 'reason' => '新規参入'];

        usort($danCandidates, fn($a, $b) => $a['season'] <=> $b['season'] ?: $a['dan'] <=> $b['dan']);
        $danHistory = [];
        $maxDanSoFar = 0;
        foreach ($danCandidates as $c) {
            if ($c['dan'] > $maxDanSoFar) {
                $maxDanSoFar = $c['dan'];
                $danHistory[] = $c;
            }
        }
        $row['dan_history'] = $danHistory;

        // タイトル挑戦記録：本戦に「挑戦者」または「防衛側（前季保持者）」として登場したシーズンをまとめる
        // （防衛戦も"登場"に含まれるため、登場回数は獲得合計以上になる）
        $challengeRanges = [];
        foreach (TITLE_NAMES_LIST as $title) {
            // 挑戦者として登場したシーズン
            $chStmt = $pdo->prepare(
                "SELECT DISTINCT season FROM matches
                 WHERE league = :title AND individual_a_id = :id ORDER BY season ASC"
            );
            $chStmt->execute(['title' => $title, 'id' => $id]);
            $challengerSeasons = array_map('intval', array_column($chStmt->fetchAll(), 'season'));

            // 防衛側として登場したシーズン：season(N)の防衛側は、season(N-1)時点の保持者
            // （season(N)が「初代襲名」の場合は防衛側が存在しないので対象外）
            $histStmt = $pdo->prepare(
                "SELECT season, event_type, holder_id, holder_name FROM title_history
                 WHERE title = :title ORDER BY season ASC"
            );
            $histStmt->execute(['title' => $title]);
            $hist = $histStmt->fetchAll();
            $defenderSeasons = [];
            for ($i = 1; $i < count($hist); $i++) {
                if ($hist[$i]['event_type'] === '初代襲名') continue;
                $prevHolder = $hist[$i - 1];
                $isMe = ($prevHolder['holder_id'] && $prevHolder['holder_id'] === $id)
                     || ($prevHolder['holder_name'] === ($row['display_name'] ?? null));
                if ($isMe) $defenderSeasons[] = (int)$hist[$i]['season'];
            }

            // 初代襲名（対局は発生しないが、その季に登場したこと自体はカウントする）
            $initialSeasons = [];
            foreach ($hist as $h) {
                if ($h['event_type'] !== '初代襲名') continue;
                $isMe = ($h['holder_id'] && $h['holder_id'] === $id) || ($h['holder_name'] === ($row['display_name'] ?? null));
                if ($isMe) $initialSeasons[] = (int)$h['season'];
            }

            $seasons = array_values(array_unique(array_merge($challengerSeasons, $defenderSeasons, $initialSeasons)));
            if (empty($seasons)) continue;
            sort($seasons);
            $lastSeason = !empty($hist) ? (int)end($hist)['season'] : 0;
            $rangeText = format_season_ranges($seasons, $lastSeason, false);
            $challengeRanges[] = ['title' => $title, 'ranges' => $rangeText, 'total' => count($seasons)];
        }
        $challengeTotalSeasons = array_sum(array_column($challengeRanges, 'total'));

        $matchStmt = $pdo->prepare(
            "SELECT m.id, m.season, m.league, m.individual_a_id, m.individual_b_id, m.result, m.played_at,
                    COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS individual_a_name,
                    COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS individual_b_name
             FROM matches m
             LEFT JOIN individuals ia ON ia.id = m.individual_a_id
             LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
             LEFT JOIN individuals ib ON ib.id = m.individual_b_id
             LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
             WHERE m.individual_a_id = :id OR m.individual_b_id = :id2
             ORDER BY m.season DESC, m.id DESC LIMIT 30"
        );
        $matchStmt->execute(['id' => $id, 'id2' => $id]);

        json_out([
            'individual' => $row, 'matches' => $matchStmt->fetchAll(),
            'record' => $record, 'opponent_records' => $opponentRecords,
            'parent' => $parent, 'ancestors' => $ancestors, 'children' => $children,
            'title_ranges' => $titleRanges, 'title_total_seasons' => $titleTotalSeasons,
            'challenge_ranges' => $challengeRanges, 'challenge_total_seasons' => $challengeTotalSeasons,
        ]);
        break;

    // --------------------------------------------------------
    case 'game':
        $id = $_GET['id'] ?? '';
        if ($id === '') json_error('id パラメータが必要です');
        $stmt = $pdo->prepare(
            "SELECT m.id, m.season, m.league, m.individual_a_id, m.individual_b_id, m.result, m.games_json, m.played_at,
                    COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS individual_a_name,
                    COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS individual_b_name
             FROM matches m
             LEFT JOIN individuals ia ON ia.id = m.individual_a_id
             LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
             LEFT JOIN individuals ib ON ib.id = m.individual_b_id
             LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
             WHERE m.id = :id"
        );
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch();
        if (!$row) json_error('対局が見つかりません', 404);
        $row['games'] = json_decode($row['games_json'], true);
        unset($row['games_json'], $row['individual_a_id'], $row['individual_b_id']);
        json_out(['match' => $row]);
        break;

    // --------------------------------------------------------
    case 'titles':
        $title_order = "FIELD(title, '青龍', '朱雀', '白虎', '玄武')";
        $current = $pdo->query(
            "SELECT title, holder_name, holder_id, event_type, season
             FROM title_history t1
             WHERE season = (SELECT MAX(season) FROM title_history t2 WHERE t2.title = t1.title)
             GROUP BY title
             ORDER BY {$title_order}"
        )->fetchAll();

        // 永世称号：タイトルごとに基準が異なる
        //   青龍＝通算5期（連続でなくてよい）
        //   朱雀＝連続5期 または 通算7期
        //   白虎・玄武＝連続5期
        // タイトル履歴を時系列に沿って辿り、保持者ごとの「連続保持の最大値」と「通算保持期数」を集計してから判定する。
        $all_history = $pdo->query(
            "SELECT season, title, event_type, holder_name FROM title_history ORDER BY title, season ASC"
        )->fetchAll();

        $total_by_holder = []; // holder_name => [title => 通算期数]
        $max_streak_by_holder = []; // holder_name => [title => 連続保持の最大値]
        $reign_start_season = []; // title => 現在の連続保持が始まったシーズン（保持者が変わるたびに更新）
        $streak_holder = null;
        $streak_count = 0;
        $streak_title = null;
        foreach ($all_history as $row) {
            $total_by_holder[$row['holder_name']][$row['title']] = ($total_by_holder[$row['holder_name']][$row['title']] ?? 0) + 1;
            if ($row['title'] !== $streak_title || $row['holder_name'] !== $streak_holder) {
                // 保持者交代（または新しいタイトルの処理開始）：連続記録をリセット
                $streak_title = $row['title'];
                $streak_holder = $row['holder_name'];
                $streak_count = 1;
                $reign_start_season[$row['title']] = (int)$row['season'];
            } else {
                $streak_count++;
            }
            if (!isset($max_streak_by_holder[$row['holder_name']][$row['title']]) ||
                $streak_count > $max_streak_by_holder[$row['holder_name']][$row['title']]) {
                $max_streak_by_holder[$row['holder_name']][$row['title']] = $streak_count;
            }
        }

        $eternal_titles = []; // holder_name => [title, ...]
        foreach ($total_by_holder as $holder => $byTitle) {
            foreach ($byTitle as $title => $total) {
                $consec = $max_streak_by_holder[$holder][$title] ?? 0;
                $isEternal = is_eternal_title($title, $total, $consec);
                if ($isEternal) {
                    if (!isset($eternal_titles[$holder])) $eternal_titles[$holder] = [];
                    $eternal_titles[$holder][] = $title;
                }
            }
        }
        foreach ($current as &$c) {
            $c['eternal'] = isset($eternal_titles[$c['holder_name']]) && in_array($c['title'], $eternal_titles[$c['holder_name']]);
            // 「第Xシーズン〜」の表示は"最後にイベントが起きたシーズン"ではなく
            // "今の連続保持が始まったシーズン"（奪取や初代襲名のシーズン）を使う
            $c['season'] = $reign_start_season[$c['title']] ?? $c['season'];
        }

        $history = $pdo->query(
            "SELECT season, title, event_type, holder_name, challenger_wins, titleholder_wins, achieved_at
             FROM title_history ORDER BY achieved_at DESC LIMIT 50"
        )->fetchAll();

        json_out(['current' => $current, 'history' => $history]);
        break;

    // --------------------------------------------------------
    case 'recent_matches':
        // qを指定すると、対局者名（部分一致・大文字小文字区別なし）で絞り込む。
        // 名前は結合先テーブルのCOALESCE結果（エイリアス）に対する絞り込みのため、
        // WHEREではなくHAVINGを使う（MySQLはSELECT句のエイリアスをHAVINGで参照できる）
        $limit = min(500, max(1, (int)($_GET['limit'] ?? 30)));
        $q = trim((string)($_GET['q'] ?? ''));
        $sql = "SELECT m.id, m.season, m.league, m.result, m.played_at,
                    COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS individual_a_name,
                    COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS individual_b_name
             FROM matches m
             LEFT JOIN individuals ia ON ia.id = m.individual_a_id
             LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
             LEFT JOIN individuals ib ON ib.id = m.individual_b_id
             LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id";
        if ($q !== '') {
            $sql .= " HAVING individual_a_name LIKE :q OR individual_b_name LIKE :q";
        }
        $sql .= " ORDER BY m.season DESC, m.id DESC LIMIT :limit";
        $stmt = $pdo->prepare($sql);
        if ($q !== '') {
            $stmt->bindValue('q', '%' . $q . '%', PDO::PARAM_STR);
        }
        $stmt->bindValue('limit', $limit, PDO::PARAM_INT);
        $stmt->execute();
        json_out(['matches' => $stmt->fetchAll()]);
        break;

    // --------------------------------------------------------
    case 'season_state':
        $row = $pdo->query("SELECT current_season, updated_at FROM season_state WHERE id = 1")->fetch();
        json_out(['season_state' => $row ?: ['current_season' => 0]]);
        break;

    // --------------------------------------------------------
    case 'newcomer_league':
        // for_season：新人リーグの勝者がDリーグへ新規参入する対象シーズン番号（未指定時は最新）
        $availableStmt = $pdo->query("SELECT for_season FROM newcomer_league_meta ORDER BY for_season DESC");
        $available = array_map('intval', array_column($availableStmt->fetchAll(), 'for_season'));

        if (empty($available)) {
            json_out(['for_season' => 0, 'meta' => null, 'standings' => [], 'available_seasons' => []]);
        }

        $forSeason = isset($_GET['for_season']) ? (int)$_GET['for_season'] : $available[0];

        $metaStmt = $pdo->prepare(
            "SELECT for_season, slots_needed, submission_count, imported_at FROM newcomer_league_meta WHERE for_season = :s"
        );
        $metaStmt->execute(['s' => $forSeason]);
        $meta = $metaStmt->fetch();

        $standStmt = $pdo->prepare(
            "SELECT `rank`, individual_id, display_name, win, loss, draw, auto_generated, promoted
             FROM newcomer_league_standings WHERE for_season = :s ORDER BY `rank` ASC"
        );
        $standStmt->execute(['s' => $forSeason]);
        $standings = resolve_newcomer_real_ids($pdo, $standStmt->fetchAll());

        json_out([
            'for_season' => $forSeason,
            'meta' => $meta ?: null,
            'standings' => $standings,
            'available_seasons' => $available,
        ]);
        break;

    // --------------------------------------------------------
    case 'newcomer_league_history':
        // 新人リーグ経由でDリーグへ新規参入した個体の、全季分の一覧（歴代記録）
        $rows = $pdo->query(
            "SELECT for_season, `rank`, individual_id, display_name, win, loss, draw, auto_generated, promoted
             FROM newcomer_league_standings WHERE promoted = 1 ORDER BY for_season DESC, `rank` ASC"
        )->fetchAll();
        json_out(['history' => resolve_newcomer_real_ids($pdo, $rows)]);
        break;

    // --------------------------------------------------------
    case 'character_creation_status':
        // キャラクリエイトタブ側が、募集状況と「待機メンバー一覧」を表示するためのエンドポイント
        $cnt = (int)$pdo->query("SELECT COUNT(*) FROM character_requests WHERE status = 'pending'")->fetchColumn();
        $cap = 40; // create_character.php の SUBMISSION_CAP と一致させること
        $pendingList = $pdo->query(
            "SELECT display_name, type_tendency, submitter_name, created_at FROM character_requests
             WHERE status = 'pending' ORDER BY created_at ASC"
        )->fetchAll();
        json_out([
            'pending_count' => $cnt, 'cap' => $cap, 'closed' => $cnt >= $cap,
            'pending_list' => $pendingList,
        ]);
        break;

    // --------------------------------------------------------
    case 'standings':
        // seasonを指定：そのシーズンの全リーグ順位一覧
        // individual_idを指定：その個体のシーズンごとの成績履歴
        if (isset($_GET['season'])) {
            $season = (int)$_GET['season'];
            $stmt = $pdo->prepare(
                "SELECT league, `rank`, individual_id, display_name, win, loss, draw, movement, no_roundrobin
                 FROM standings WHERE season = :season
                 ORDER BY FIELD(league,'A','B','C','D'), `rank` ASC"
            );
            $stmt->execute(['season' => $season]);
            $rows = $stmt->fetchAll();

            // その季に防衛専念枠だった青龍を、Aリーグの通常順位リストから分離する
            $rikuou_entry = null;
            foreach ($rows as $i => $r) {
                if ($r['league'] === 'A' && !empty($r['no_roundrobin'])) {
                    $rikuou_entry = $r;
                    array_splice($rows, $i, 1);
                    break;
                }
            }

            // その季に実際にタイトルを保持していた人（今現在の保持者ではなく、"その季"時点の記録）
            $seasonTitleStmt = $pdo->prepare(
                "SELECT title, holder_id, holder_name FROM title_history WHERE season = :season"
            );
            $seasonTitleStmt->execute(['season' => $season]);
            $seasonTitleholders = ['by_id' => [], 'by_name' => []];
            foreach ($seasonTitleStmt->fetchAll() as $t) {
                if ($t['holder_id']) $seasonTitleholders['by_id'][$t['holder_id']][] = $t['title'];
                if ($t['holder_name']) $seasonTitleholders['by_name'][$t['holder_name']][] = $t['title'];
            }

            // 段位（現時点の段位。タイトル非保持者の名前右に表示する）
            $danIds = array_column($rows, 'individual_id');
            if ($rikuou_entry) $danIds[] = $rikuou_entry['individual_id'];
            $danById = calc_dan_bulk($pdo, array_values(array_unique($danIds)));

            json_out([
                'standings' => $rows, 'season' => $season, 'rikuou' => $rikuou_entry,
                'titleholders' => $seasonTitleholders, 'dan' => $danById,
            ]);
        } elseif (isset($_GET['individual_id'])) {
            $iid = $_GET['individual_id'];
            // シーズンごとの成績は、従来通りA〜Dリーグの所属・昇降格のみを表示する
            // （朱雀紅白リーグの記録もstandingsテーブルに入っているが、ここでは対象外にする）
            $stmt = $pdo->prepare(
                "SELECT season, league, `rank`, win, loss, draw, movement, no_roundrobin, elo FROM standings
                 WHERE individual_id = :id AND league IN ('A', 'B', 'C', 'D') ORDER BY season ASC"
            );
            $stmt->execute(['id' => $iid]);
            $rows = $stmt->fetchAll();

            // Aリーグで青龍が防衛専念枠（no_roundrobin）として別枠分離されているシーズンは、
            // 総当たり参加者側のrankに欠番（1位分の空き）が残ったままDBに入っている。
            // 順位タブ（standings&season=）はJS側で振り直しているが、こちらは個体単位の取得のため
            // ここで同様の補正を行い、実際の総当たり順位（欠番なし）に直す。
            $byeCheckStmt = $pdo->prepare(
                "SELECT COUNT(*) FROM standings WHERE season = :season AND league = 'A' AND no_roundrobin = 1"
            );
            $rankFixStmt = $pdo->prepare(
                "SELECT COUNT(*) FROM standings
                 WHERE season = :season AND league = 'A'
                   AND (no_roundrobin = 0 OR no_roundrobin IS NULL) AND `rank` <= :rank"
            );
            foreach ($rows as &$r) {
                if ($r['league'] === 'A' && empty($r['no_roundrobin']) && $r['rank'] !== null) {
                    $byeCheckStmt->execute(['season' => $r['season']]);
                    if ((int)$byeCheckStmt->fetchColumn() > 0) {
                        $rankFixStmt->execute(['season' => $r['season'], 'rank' => $r['rank']]);
                        $r['rank'] = (int)$rankFixStmt->fetchColumn();
                    }
                }
                if ($r['elo'] !== null) $r['elo'] = round((float)$r['elo'], 1);
            }
            unset($r);

            // この個体の表示名を取得（title_historyはholder_nameでも突合するため）
            $nameStmt = $pdo->prepare(
                "SELECT display_name FROM individuals WHERE id = :id
                 UNION ALL SELECT display_name FROM retired_archive WHERE id = :id2 LIMIT 1"
            );
            $nameStmt->execute(['id' => $iid, 'id2' => $iid]);
            $indName = $nameStmt->fetchColumn();

            // この個体がタイトル保持者として記録されている(season, title, event_type)を全て取得し、
            // シーズンごとにまとめる（獲得歴と同じ title_history を直接参照することで、
            // no_roundrobinベースの旧表示との季ズレを解消する）
            $titleEventStmt = $pdo->prepare(
                "SELECT season, title, event_type FROM title_history
                 WHERE holder_id = :id OR holder_name = :name"
            );
            $titleEventStmt->execute(['id' => $iid, 'name' => $indName ?: '']);
            $titleEventsBySeason = [];
            foreach ($titleEventStmt->fetchAll() as $te) {
                $titleEventsBySeason[(int)$te['season']][] = ['title' => $te['title'], 'event_type' => $te['event_type']];
            }

            // 防衛失敗の判定：3タイトル共通で「前季の保持者だったが、今季は保持者でなくなった」シーズンを検出する
            // （青龍だけの特別扱い(no_roundrobin)をやめ、他タイトルにも同じロジックを適用する）
            $defenseFailBySeason = [];
            foreach (TITLE_NAMES_LIST as $title) {
                $histStmt = $pdo->prepare(
                    "SELECT season, event_type, holder_id, holder_name FROM title_history
                     WHERE title = :title ORDER BY season ASC"
                );
                $histStmt->execute(['title' => $title]);
                $hist = $histStmt->fetchAll();
                for ($i = 1; $i < count($hist); $i++) {
                    if ($hist[$i]['event_type'] === '初代襲名') continue;
                    $prevHolder = $hist[$i - 1];
                    $wasMe = ($prevHolder['holder_id'] && $prevHolder['holder_id'] === $iid)
                          || ($prevHolder['holder_name'] === ($indName ?: null));
                    $curHolder = $hist[$i];
                    $isMeNow = ($curHolder['holder_id'] && $curHolder['holder_id'] === $iid)
                            || ($curHolder['holder_name'] === ($indName ?: null));
                    if ($wasMe && !$isMeNow) {
                        $defenseFailBySeason[(int)$curHolder['season']][] = $title;
                    }
                }
            }

            // シーズンごとの「全対局」の合算（リーグ戦＋タイトル戦）を別途集計する。
            // standings.win/loss/drawはリーグ戦のみ（順位タブ用）に留め、
            // 個体詳細ページの「シーズンごとの成績」はタイトル戦を含めた合計を表示したいため、
            // matchesテーブルから直接、a側・b側両方を正しく合算する。
            $combinedStmt = $pdo->prepare(
                "SELECT season,
                    SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS win,
                    SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) AS loss,
                    SUM(CASE WHEN outcome = 'draw' THEN 1 ELSE 0 END) AS draw
                 FROM (
                    SELECT season, result AS outcome FROM matches WHERE individual_a_id = :id
                    UNION ALL
                    SELECT season,
                           CASE result WHEN 'win' THEN 'loss' WHEN 'loss' THEN 'win' ELSE 'draw' END AS outcome
                    FROM matches WHERE individual_b_id = :id2
                 ) AS all_games
                 GROUP BY season"
            );
            $combinedStmt->execute(['id' => $iid, 'id2' => $iid]);
            $combinedBySeason = [];
            foreach ($combinedStmt->fetchAll() as $c) {
                $combinedBySeason[(int)$c['season']] = $c;
            }

            foreach ($rows as &$r) {
                $r['title_events'] = $titleEventsBySeason[(int)$r['season']] ?? [];
                $r['defense_fail_titles'] = $defenseFailBySeason[(int)$r['season']] ?? [];
                $combined = $combinedBySeason[(int)$r['season']] ?? null;
                if ($combined) {
                    $r['win'] = (int)$combined['win'];
                    $r['loss'] = (int)$combined['loss'];
                    $r['draw'] = (int)$combined['draw'];
                }
            }
            unset($r);

            // 朱雀戦（紅白リーグ）通算在籍期数（連続でなくてよい）。防衛専念枠だった季も在籍に含める
            $suzakuTotalStmt = $pdo->prepare(
                "SELECT COUNT(*) FROM suzaku_group_standings
                 WHERE individual_id = :id AND league IN ('朱雀紅組', '朱雀白組')"
            );
            $suzakuTotalStmt->execute(['id' => $iid]);
            $suzakuSeasonsTotal = (int)$suzakuTotalStmt->fetchColumn();

            json_out(['history' => $rows, 'suzaku_seasons_total' => $suzakuSeasonsTotal]);
        } else {
            $max = $pdo->query("SELECT MAX(season) AS m FROM standings")->fetch();
            json_out(['latest_season' => (int)($max['m'] ?? 0)]);
        }
        break;

    // --------------------------------------------------------
    case 'title_history_detail':
        // 指定タイトル（青龍/朱雀/白虎/玄武）の全シーズン分の記録を、挑戦者名・番勝負スコア付きで返す
        $title = $_GET['title'] ?? '';
        if (!in_array($title, TITLE_NAMES_LIST, true)) {
            json_error('title（青龍/朱雀/白虎/玄武）が必要です');
        }

        $histStmt = $pdo->prepare(
            "SELECT season, event_type, holder_name, holder_id FROM title_history
             WHERE title = :title ORDER BY season ASC"
        );
        $histStmt->execute(['title' => $title]);
        $hist = $histStmt->fetchAll();

        $gameStmt = $pdo->prepare(
            "SELECT m.result, m.individual_a_id AS a_id, m.individual_b_id AS b_id,
                    COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS a_name,
                    COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS b_name
             FROM matches m
             LEFT JOIN individuals ia ON ia.id = m.individual_a_id
             LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
             LEFT JOIN individuals ib ON ib.id = m.individual_b_id
             LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
             WHERE m.season = :season AND m.league = :title
             ORDER BY m.id ASC"
        );

        $rows = [];
        foreach ($hist as $h) {
            $season = (int)$h['season'];
            $entry = [
                'season' => $season, 'event_type' => $h['event_type'],
                // 「保持者」列は常に"そのシーズンの対局が始まる前の時点の肩書"（＝防衛側）を表示する。
                // 初代襲名のみ対局が無いので、title_historyのholder_name/holder_idをそのまま使う。
                'holder_name' => ($h['event_type'] === '初代襲名') ? $h['holder_name'] : null,
                'holder_id' => ($h['event_type'] === '初代襲名') ? $h['holder_id'] : null,
                'opponent_name' => null, 'opponent_id' => null, 'holder_wins' => 0, 'opponent_wins' => 0,
            ];
            if ($h['event_type'] !== '初代襲名') {
                $gameStmt->execute(['season' => $season, 'title' => $title]);
                $games = $gameStmt->fetchAll();
                $aWins = 0; $bWins = 0; $aName = null; $bName = null; $aId = null; $bId = null;
                foreach ($games as $g) {
                    if ($aName === null) { $aName = $g['a_name']; $bName = $g['b_name']; $aId = $g['a_id']; $bId = $g['b_id']; }
                    if ($g['result'] === 'win') $aWins++;
                    elseif ($g['result'] === 'loss') $bWins++;
                }
                // matchesの対局規約：個体A＝挑戦者（今季の新規参戦側）、個体B＝防衛側（前季末時点の保持者）。
                // 勝敗に関わらずこの対応は固定なので、奪取・防衛のどちらでも取り違えは起きない。
                if ($aName !== null) {
                    $entry['holder_name'] = $bName;       // 防衛側＝シーズン開始前の保持者
                    $entry['holder_id'] = $bId;
                    $entry['opponent_name'] = $aName;      // 挑戦者
                    $entry['opponent_id'] = $aId;
                    $entry['holder_wins'] = $bWins;        // 防衛側の勝数
                    $entry['opponent_wins'] = $aWins;      // 挑戦者の勝数
                }
            }
            $rows[] = $entry;
        }

        json_out(['title' => $title, 'history' => $rows]);
        break;

    // --------------------------------------------------------
    case 'title_bracket':
        // 指定シーズン・指定タイトル（青龍/朱雀/白虎/玄武）の予選ブラケット対局一覧を返す
        $season = (int)($_GET['season'] ?? 0);
        $title = $_GET['title'] ?? '';
        if ($season <= 0 || !in_array($title, TITLE_NAMES_LIST)) {
            json_error('season と title（青龍/朱雀/白虎/玄武）が必要です');
        }

        // 朱雀戦：紅白リーグ制（青龍〜玄武のブラケット方式とは形式が異なるため、専用に組み立てて返す）
        if ($title === '朱雀') {
            $groupStmt = $pdo->prepare(
                "SELECT m.id, m.result, m.individual_a_id, m.individual_b_id,
                        COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS individual_a_name,
                        COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS individual_b_name
                 FROM matches m
                 LEFT JOIN individuals ia ON ia.id = m.individual_a_id
                 LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
                 LEFT JOIN individuals ib ON ib.id = m.individual_b_id
                 LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
                 WHERE m.season = :season AND m.league = :league
                 ORDER BY m.id ASC"
            );
            $fetchGroup = function ($league) use ($groupStmt, $season) {
                $groupStmt->execute(['season' => $season, 'league' => $league]);
                return $groupStmt->fetchAll();
            };

            $decisionMatches = $fetchGroup('朱雀挑戦者決定戦');
            $qualifierMatches = $fetchGroup('朱雀予選');
            // 紅白リーグがまだ無い第1季のみ発生する「初代紅白メンバー決定トーナメント」。
            // 通常の入れ替え戦（朱雀予選）とは別リーグ名で記録しているため、別途取得する
            $bootstrapMatches = $fetchGroup('朱雀紅白決定戦');

            // 紅組・白組の順位・残留/陥落は、対局結果からサイト側で再集計するのではなく、
            // run_season.py側が確定させた値をそのまま読む（suzaku_group_standingsテーブル。
            // standingsとは別テーブル：同じ個体が同じ季にA〜Dリーグと紅白リーグの両方に
            // 記録されるケースがあり、同じテーブルに入れるとUNIQUE KEY(season, individual_id)が
            // 衝突してA〜Dリーグ側の記録が上書きされて消えてしまうバグがあったため分離した）。
            // 同率順位の並び（1位決定戦や、陥落境界での同率）はPython側の実際の判定と
            // 完全に一致している必要があり、対局ログから独自に再計算すると一致しない
            // 恐れがあるため（同率1位決定戦の結果を考慮しても、陥落境界の同率までは
            // 対局ログだけからは再現できない）
            $groupStandStmt = $pdo->prepare(
                "SELECT league, `rank`, individual_id, display_name, elo, win, loss, draw, no_roundrobin, movement
                 FROM suzaku_group_standings WHERE season = :season AND league IN ('朱雀紅組', '朱雀白組')
                 ORDER BY league, `rank` ASC"
            );
            $groupStandStmt->execute(['season' => $season]);
            $groupStandRows = $groupStandStmt->fetchAll();
            // 防衛専念枠（在位者）はこの季の総当たりに参加していないため、順位表からは除く
            $redStandings = array_values(array_filter($groupStandRows, fn($r) => $r['league'] === '朱雀紅組' && !$r['no_roundrobin']));
            $whiteStandings = array_values(array_filter($groupStandRows, fn($r) => $r['league'] === '朱雀白組' && !$r['no_roundrobin']));

            // suzaku_group_standingsテーブルに記録が無い季（この仕組み導入より前の過去シーズン）は、
            // 対局ログから順位を再集計するフォールバックで表示する。この場合、1位タイは
            // 挑戦者決定戦（"紅組-playoff"等）の結果で正しく並べ替えられるが、
            // 陥落境界（3位・4位）での同率までは対局ログだけからは正確に復元できない
            if (empty($redStandings) && empty($whiteStandings)) {
                $redMatches = $fetchGroup('朱雀紅組');
                $whiteMatches = $fetchGroup('朱雀白組');
                $redPlayoff = $fetchGroup('朱雀紅組-playoff');
                $whitePlayoff = $fetchGroup('朱雀白組-playoff');

                $buildGroupStandings = function ($matches) {
                    $stats = [];
                    foreach ($matches as $m) {
                        $aId = $m['individual_a_id'];
                        $bId = $m['individual_b_id'];
                        foreach ([[$aId, $m['individual_a_name']], [$bId, $m['individual_b_name']]] as [$id, $name]) {
                            if (!isset($stats[$id])) $stats[$id] = ['individual_id' => $id, 'display_name' => $name, 'win' => 0, 'loss' => 0, 'draw' => 0];
                        }
                        if ($m['result'] === 'win') { $stats[$aId]['win']++; $stats[$bId]['loss']++; }
                        elseif ($m['result'] === 'loss') { $stats[$bId]['win']++; $stats[$aId]['loss']++; }
                        else { $stats[$aId]['draw']++; $stats[$bId]['draw']++; }
                    }
                    $list = array_values($stats);
                    usort($list, fn($x, $y) => ($y['win'] * 2 + $y['draw']) <=> ($x['win'] * 2 + $x['draw']));
                    foreach ($list as $i => &$row) {
                        $row['rank'] = $i + 1;
                        $row['movement'] = ($i >= count($list) - 2) ? 'relegated' : 'stay';
                    }
                    unset($row);
                    return $list;
                };

                $applyPlayoffOrder = function (&$standings, $playoffMatches) {
                    if (empty($playoffMatches) || count($standings) < 2) return;
                    $tieScore = [];
                    foreach ($playoffMatches as $m) {
                        $aId = $m['individual_a_id'];
                        $bId = $m['individual_b_id'];
                        if (!isset($tieScore[$aId])) $tieScore[$aId] = 0.0;
                        if (!isset($tieScore[$bId])) $tieScore[$bId] = 0.0;
                        if ($m['result'] === 'win') $tieScore[$aId] += 1.0;
                        elseif ($m['result'] === 'loss') $tieScore[$bId] += 1.0;
                        else { $tieScore[$aId] += 0.5; $tieScore[$bId] += 0.5; }
                    }
                    if (empty($tieScore)) return;
                    $tiedIds = array_keys($tieScore);
                    $tiedPositions = [];
                    foreach ($standings as $i => $row) {
                        if (in_array($row['individual_id'], $tiedIds, true)) $tiedPositions[] = $i;
                    }
                    if (count($tiedPositions) < 2) return;
                    $tiedRows = array_map(fn($i) => $standings[$i], $tiedPositions);
                    usort($tiedRows, fn($x, $y) => $tieScore[$y['individual_id']] <=> $tieScore[$x['individual_id']]);
                    foreach ($tiedPositions as $k => $pos) {
                        $tiedRows[$k]['rank'] = $standings[$pos]['rank'];
                        $standings[$pos] = $tiedRows[$k];
                    }
                };

                $redStandings = $buildGroupStandings($redMatches);
                $whiteStandings = $buildGroupStandings($whiteMatches);
                $applyPlayoffOrder($redStandings, $redPlayoff);
                $applyPlayoffOrder($whiteStandings, $whitePlayoff);
            }

            $decisionRedName = null;
            $decisionWhiteName = null;
            $decisionRedWins = 0;
            $decisionWhiteWins = 0;
            foreach ($decisionMatches as $g) {
                if ($decisionRedName === null) {
                    $decisionRedName = $g['individual_a_name'];
                    $decisionWhiteName = $g['individual_b_name'];
                }
                if ($g['result'] === 'win') $decisionRedWins++;
                elseif ($g['result'] === 'loss') $decisionWhiteWins++;
            }

            // 本戦（挑戦者 vs 朱雀ホルダー）の番勝負
            $finalStmt = $pdo->prepare(
                "SELECT m.id, m.result,
                        COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS challenger_name,
                        COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS holder_name
                 FROM matches m
                 LEFT JOIN individuals ia ON ia.id = m.individual_a_id
                 LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
                 LEFT JOIN individuals ib ON ib.id = m.individual_b_id
                 LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
                 WHERE m.season = :season AND m.league = :title
                 ORDER BY m.id ASC"
            );
            $finalStmt->execute(['season' => $season, 'title' => $title]);
            $finalGames = $finalStmt->fetchAll();

            $challengerWins = 0;
            $holderWins = 0;
            $challengerName = null;
            $holderName = null;
            foreach ($finalGames as $g) {
                if ($g['result'] === 'win') $challengerWins++;
                elseif ($g['result'] === 'loss') $holderWins++;
                if ($challengerName === null) { $challengerName = $g['challenger_name']; $holderName = $g['holder_name']; }
            }

            $qualifierParticipantInfo = build_bracket_participant_info($pdo, $season, array_unique(array_filter(array_merge(
                array_column($qualifierMatches, 'individual_a_id'), array_column($qualifierMatches, 'individual_b_id')
            ))));
            $bootstrapParticipantInfo = build_bracket_participant_info($pdo, $season, array_unique(array_filter(array_merge(
                array_column($bootstrapMatches, 'individual_a_id'), array_column($bootstrapMatches, 'individual_b_id')
            ))));

            json_out([
                'format' => 'suzaku_v2',
                'red_standings' => $redStandings, 'white_standings' => $whiteStandings,
                'decision_matches' => $decisionMatches,
                'decision_red_name' => $decisionRedName, 'decision_white_name' => $decisionWhiteName,
                'decision_red_wins' => $decisionRedWins, 'decision_white_wins' => $decisionWhiteWins,
                'qualifier_bracket' => $qualifierMatches,
                'qualifier_participant_info' => $qualifierParticipantInfo,
                'bootstrap_bracket' => $bootstrapMatches,
                'bootstrap_participant_info' => $bootstrapParticipantInfo,
                'final_games' => $finalGames,
                'challenger_name' => $challengerName, 'holder_name' => $holderName,
                'challenger_wins' => $challengerWins, 'holder_wins' => $holderWins,
            ]);
            break;
        }

        $stmt = $pdo->prepare(
            "SELECT m.id, m.result, m.stage, m.block_no, m.individual_a_id, m.individual_b_id,
                    COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS individual_a_name,
                    COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS individual_b_name
             FROM matches m
             LEFT JOIN individuals ia ON ia.id = m.individual_a_id
             LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
             LEFT JOIN individuals ib ON ib.id = m.individual_b_id
             LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
             WHERE m.season = :season AND m.league = :league
             ORDER BY m.id ASC"
        );
        $stmt->execute(['season' => $season, 'league' => $title . '予選']);
        $bracket = $stmt->fetchAll();

        // 玄武戦のみ：8ブロックの予選＋挑戦者決定トーナメントとして分けて表示するため、
        // stage/block_noでブロックごと・最終段階ごとに振り分ける（他タイトルはstageが
        // 常にNULLのため何もしない＝従来通り単一ブラケットのまま）
        $genbuBlocks = null;
        $genbuFinal = null;
        if ($title === '玄武') {
            $blocksTmp = [];
            $finalTmp = [];
            foreach ($bracket as $m) {
                if ($m['stage'] === 'block') {
                    $b = (int)$m['block_no'];
                    if (!isset($blocksTmp[$b])) $blocksTmp[$b] = [];
                    $blocksTmp[$b][] = $m;
                } elseif ($m['stage'] === 'final') {
                    $finalTmp[] = $m;
                }
            }
            if (!empty($blocksTmp)) {
                ksort($blocksTmp);
                $genbuBlocks = array_values($blocksTmp);
                $genbuFinal = $finalTmp;
            }
        }

        // 本戦（挑戦者 vs ホルダー）の番勝負：全局を時系列で取得し、スコアカード表示用に整形する
        $finalStmt = $pdo->prepare(
            "SELECT m.id, m.result,
                    COALESCE(ia.display_name, ra.display_name, m.individual_a_id) AS challenger_name,
                    COALESCE(ib.display_name, rb.display_name, m.individual_b_id) AS holder_name
             FROM matches m
             LEFT JOIN individuals ia ON ia.id = m.individual_a_id
             LEFT JOIN retired_archive ra ON ra.id = m.individual_a_id
             LEFT JOIN individuals ib ON ib.id = m.individual_b_id
             LEFT JOIN retired_archive rb ON rb.id = m.individual_b_id
             WHERE m.season = :season AND m.league = :title
             ORDER BY m.id ASC"
        );
        $finalStmt->execute(['season' => $season, 'title' => $title]);
        $finalGames = $finalStmt->fetchAll();

        $challengerWins = 0;
        $holderWins = 0;
        $challengerName = null;
        $holderName = null;
        foreach ($finalGames as $g) {
            if ($g['result'] === 'win') $challengerWins++;
            elseif ($g['result'] === 'loss') $holderWins++;
            if ($challengerName === null) { $challengerName = $g['challenger_name']; $holderName = $g['holder_name']; }
        }

        // 予選ブラケット参加者に添える補足情報：所属リーグ（アイコン用）＋その季のタイトル戦
        // 開始前時点のElo。全タイトル共通の表示形式（名前の左にリーグアイコン、右に(Elo)）に
        // 揃えるため、朱雀の予選ブラケットも含めて共通ヘルパーで組み立てる
        $participantInfo = build_bracket_participant_info($pdo, $season, array_unique(array_filter(array_merge(
            array_column($bracket, 'individual_a_id'), array_column($bracket, 'individual_b_id')
        ))));

        json_out([
            'bracket' => $bracket,
            'genbu_blocks' => $genbuBlocks,
            'genbu_final' => $genbuFinal,
            'final_games' => $finalGames,
            'challenger_name' => $challengerName,
            'holder_name' => $holderName,
            'challenger_wins' => $challengerWins,
            'holder_wins' => $holderWins,
            'participant_info' => $participantInfo,
        ]);
        break;

    // --------------------------------------------------------
    case 'retired_archive':
        // タイトル獲得数順に並べるため、表示件数で絞る前に引退者全員を取得する
        // （retired_atで先にLIMITしてしまうと、昔に引退した歴代タイトル王が
        //   表示対象から漏れてランキング上位なのに表示されない、というバグになる）
        $limit = min(200, max(1, (int)($_GET['limit'] ?? 30)));
        $stmt = $pdo->query(
            "SELECT id, display_name, league, clan_root_id, elo_rating, peak_elo, total_seasons, initial_age, retired_season, retired_at
             FROM retired_archive ORDER BY retired_at DESC"
        );
        $rows = $stmt->fetchAll();

        $ids = array_column($rows, 'id');
        $records = [];
        if ($ids) {
            $placeholders = implode(',', array_fill(0, count($ids), '?'));
            $recStmt = $pdo->prepare(
                "SELECT pid,
                    COUNT(*) AS total,
                    SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS win,
                    SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) AS loss,
                    SUM(CASE WHEN outcome = 'draw' THEN 1 ELSE 0 END) AS draw
                 FROM (
                    SELECT individual_a_id AS pid, result AS outcome
                    FROM matches WHERE individual_a_id IN ($placeholders)
                    UNION ALL
                    SELECT individual_b_id AS pid,
                           CASE result WHEN 'win' THEN 'loss' WHEN 'loss' THEN 'win' ELSE 'draw' END AS outcome
                    FROM matches WHERE individual_b_id IN ($placeholders)
                 ) AS all_games
                 GROUP BY pid"
            );
            $recStmt->execute(array_merge($ids, $ids));
            foreach ($recStmt->fetchAll() as $row2) {
                $records[$row2['pid']] = $row2;
            }
        }

        // 通算タイトル数（合計・タイトル別内訳）と、連続保持の最大値。
        // title_historyの初代襲名行はholder_idが入っていないため、holder_nameで個体を突き合わせて補完する
        // （補完しないと、初代襲名を含む在位が1期分足りない扱いになり、通算期数がずれる）。
        $thStmt2 = $pdo->query(
            "SELECT th.season, th.title,
                    COALESCE(th.holder_id, i.id, ra2.id) AS holder_id
             FROM title_history th
             LEFT JOIN individuals i ON i.display_name = th.holder_name
             LEFT JOIN retired_archive ra2 ON ra2.display_name = th.holder_name
             ORDER BY th.title, th.season ASC"
        );
        $titleCounts = [];
        $titleCountsByTitle = [];
        $maxStreak2 = [];
        $streakId2 = null; $streakTitle2 = null; $streakCount2 = 0;
        foreach ($thStmt2->fetchAll() as $row5) {
            $hid = $row5['holder_id'];
            if ($hid !== null) {
                $titleCounts[$hid] = ($titleCounts[$hid] ?? 0) + 1;
                if (!isset($titleCountsByTitle[$hid])) $titleCountsByTitle[$hid] = [];
                $titleCountsByTitle[$hid][$row5['title']] = ($titleCountsByTitle[$hid][$row5['title']] ?? 0) + 1;
            }
            if ($row5['title'] !== $streakTitle2 || $hid !== $streakId2) {
                $streakTitle2 = $row5['title']; $streakId2 = $hid; $streakCount2 = 1;
            } else {
                $streakCount2++;
            }
            if ($hid !== null && (!isset($maxStreak2[$hid][$row5['title']]) || $streakCount2 > $maxStreak2[$hid][$row5['title']])) {
                $maxStreak2[$hid][$row5['title']] = $streakCount2;
            }
        }

        // 永世称号：タイトルごとに基準が異なる（is_eternal_title()参照）
        $eternalIds2 = [];
        foreach ($titleCountsByTitle as $hid => $byTitle) {
            foreach ($byTitle as $title => $total) {
                $consec = $maxStreak2[$hid][$title] ?? 0;
                $isEternal = is_eternal_title($title, $total, $consec);
                if ($isEternal) {
                    if (!isset($eternalIds2[$hid])) $eternalIds2[$hid] = [];
                    $eternalIds2[$hid][] = $title;
                }
            }
        }

        // 段位算出用：経験リーグ一覧（id => [league, ...]）
        $leaguesReachedMap2 = [];
        if ($ids) {
            $placeholders2 = implode(',', array_fill(0, count($ids), '?'));
            $lrStmt2 = $pdo->prepare(
                "SELECT DISTINCT individual_id, league FROM standings
                 WHERE individual_id IN ($placeholders2) AND league IN ('A', 'B', 'C', 'D')"
            );
            $lrStmt2->execute($ids);
            foreach ($lrStmt2->fetchAll() as $lr) {
                $leaguesReachedMap2[$lr['individual_id']][] = $lr['league'];
            }
        }

        foreach ($rows as &$r) {
            $r['elo_rating'] = round((float)$r['elo_rating'], 1);
            $r['peak_elo'] = round((float)($r['peak_elo'] ?? $r['elo_rating']), 1);
            $rec = $records[$r['id']] ?? ['total' => 0, 'win' => 0, 'loss' => 0, 'draw' => 0];
            $r['win'] = (int)$rec['win'];
            $r['loss'] = (int)$rec['loss'];
            $r['draw'] = (int)$rec['draw'];
            $r['title_count'] = $titleCounts[$r['id']] ?? 0;
            $r['title_count_by_title'] = $titleCountsByTitle[$r['id']] ?? [];
            $r['eternal_titles'] = $eternalIds2[$r['id']] ?? [];
            $leaguesReached2 = $leaguesReachedMap2[$r['id']] ?? [];
            if (in_array($r['league'], ['A', 'B', 'C', 'D'], true)) $leaguesReached2[] = $r['league'];
            $r['dan'] = calc_dan(
                $r['win'], $leaguesReached2, $titleCountsByTitle[$r['id']] ?? [],
                !empty($eternalIds2[$r['id']])
            );
            // 在籍シーズン範囲（例：2-16）。継続参加を前提に、引退季と通算季数から逆算する
            $r['debut_season'] = (int)$r['retired_season'] - (int)$r['total_seasons'] + 1;
            // 引退時点の年齢
            $r['retired_age'] = compute_age($r['initial_age'], $r['total_seasons']);
        }
        unset($r);

        // タイトル獲得数が多い順（同数なら引退が新しい順）に並べ替えてから表示件数で絞る
        usort($rows, fn($a, $b) => $b['title_count'] <=> $a['title_count'] ?: strcmp($b['retired_at'], $a['retired_at']));
        $rows = array_slice($rows, 0, $limit);

        json_out(['retired' => $rows]);
        break;

    // --------------------------------------------------------
    case 'awakened_list':
        // 覚醒（awakened_param）を達成した個体の一覧。現時点ではindividualsテーブル（現役）のみ対応
        // （retired_archiveにawakened_param列が無い場合はここに含まれない）
        $stmt = $pdo->query(
            "SELECT id, display_name, league, elo_rating, awakened_param
             FROM individuals
             WHERE awakened_param IS NOT NULL AND awakened_param != ''
             ORDER BY elo_rating DESC"
        );
        $rows = $stmt->fetchAll();
        foreach ($rows as &$r) { $r['elo_rating'] = round((float)$r['elo_rating'], 1); }
        json_out(['awakened' => $rows]);
        break;

    // --------------------------------------------------------
    case 'hall_of_fame':
        // 殿堂ランキング（現役・引退を問わず）。1本のテーブルにElo・タイトル数・対局数・勝敗・勝率を
        // 全て載せ、サイト側で列ヘッダクリックによる並び替えができるようにする（従来は指標ごとに
        // 別々のサブタブ＝別々の上位N件クエリだったが、それだと同じ人物でも指標間で対象集合が
        // 揃わず不便だったため統一した）。全個体数が極端に多くなった場合の保険として上限だけ設ける
        $stmt = $pdo->query(
            "SELECT id, display_name, clan_root_id, elo_rating, peak_elo, total_seasons, initial_age, league, 0 AS retired
             FROM individuals WHERE retired = 0
             UNION ALL
             SELECT id, display_name, clan_root_id, elo_rating, peak_elo, total_seasons, initial_age, league, 1 AS retired
             FROM retired_archive
             ORDER BY peak_elo DESC
             LIMIT 300"
        );
        $ranked = $stmt->fetchAll();

        $ids = array_column($ranked, 'id');
        $records = [];
        if ($ids) {
            $placeholders = implode(',', array_fill(0, count($ids), '?'));
            $recStmt = $pdo->prepare(
                "SELECT pid,
                    COUNT(*) AS total,
                    SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS win,
                    SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) AS loss,
                    SUM(CASE WHEN outcome = 'draw' THEN 1 ELSE 0 END) AS draw
                 FROM (
                    SELECT individual_a_id AS pid, result AS outcome
                    FROM matches WHERE individual_a_id IN ($placeholders)
                    UNION ALL
                    SELECT individual_b_id AS pid,
                           CASE result WHEN 'win' THEN 'loss' WHEN 'loss' THEN 'win' ELSE 'draw' END AS outcome
                    FROM matches WHERE individual_b_id IN ($placeholders)
                 ) AS all_games
                 GROUP BY pid"
            );
            $recStmt->execute(array_merge($ids, $ids));
            foreach ($recStmt->fetchAll() as $row) {
                $records[$row['pid']] = $row;
            }
        }

        // タイトル履歴を1本のクエリで取得。初代襲名の行はholder_idが入っていない
        // （run_season.py側でIDを渡していないため）ので、holder_nameで個体を突き合わせて補完する。
        // これを補完しないと、初代襲名を含む在位が「1期分足りない」扱いになり、
        // 通算期数がずれたり、永世称号（5期）がちょうど5期の人だけ判定漏れしたりする。
        $thStmt = $pdo->query(
            "SELECT th.season, th.title,
                    COALESCE(th.holder_id, i.id, ra2.id) AS holder_id
             FROM title_history th
             LEFT JOIN individuals i ON i.display_name = th.holder_name
             LEFT JOIN retired_archive ra2 ON ra2.display_name = th.holder_name
             ORDER BY th.title, th.season ASC"
        );

        // 通算タイトル数（合計・タイトル別内訳）と、連続保持の最大値を1パスで集計する
        $titleCounts = [];        // id => 合計期数
        $titleCountsByTitle = []; // id => ['青龍' => n, '朱雀' => n, '白虎' => n, '玄武' => n]
        $maxStreak = [];          // id => ['青龍' => 連続保持の最大値, ...]
        $streakId = null; $streakTitle = null; $streakCount = 0;
        foreach ($thStmt->fetchAll() as $row4) {
            $hid = $row4['holder_id'];
            if ($hid !== null) {
                $titleCounts[$hid] = ($titleCounts[$hid] ?? 0) + 1;
                if (!isset($titleCountsByTitle[$hid])) $titleCountsByTitle[$hid] = [];
                $titleCountsByTitle[$hid][$row4['title']] = ($titleCountsByTitle[$hid][$row4['title']] ?? 0) + 1;
            }
            if ($row4['title'] !== $streakTitle || $hid !== $streakId) {
                $streakTitle = $row4['title']; $streakId = $hid; $streakCount = 1;
            } else {
                $streakCount++;
            }
            if ($hid !== null && (!isset($maxStreak[$hid][$row4['title']]) || $streakCount > $maxStreak[$hid][$row4['title']])) {
                $maxStreak[$hid][$row4['title']] = $streakCount;
            }
        }

        // 永世称号：タイトルごとに基準が異なる（is_eternal_title()参照）
        $eternalIds = []; // id => [永世称号を得たタイトル, ...]
        foreach ($titleCountsByTitle as $hid => $byTitle) {
            foreach ($byTitle as $title => $total) {
                $consec = $maxStreak[$hid][$title] ?? 0;
                $isEternal = is_eternal_title($title, $total, $consec);
                if ($isEternal) {
                    if (!isset($eternalIds[$hid])) $eternalIds[$hid] = [];
                    $eternalIds[$hid][] = $title;
                }
            }
        }

        // 勝率ランキングで対局数が極端に少ない個体が勝率100%で独占するのを防ぐための足切り
        $CUMULATIVE_MIN_GAMES_FOR_RATE = 10;

        // 段位算出用：経験リーグ一覧（id => [league, ...]）
        $leaguesReachedMap = [];
        if ($ids) {
            $placeholders3 = implode(',', array_fill(0, count($ids), '?'));
            $lrStmt = $pdo->prepare(
                "SELECT DISTINCT individual_id, league FROM standings
                 WHERE individual_id IN ($placeholders3) AND league IN ('A', 'B', 'C', 'D')"
            );
            $lrStmt->execute($ids);
            foreach ($lrStmt->fetchAll() as $lr) {
                $leaguesReachedMap[$lr['individual_id']][] = $lr['league'];
            }
        }

        $result = [];
        foreach ($ranked as $r) {
            $rec = $records[$r['id']] ?? ['total' => 0, 'win' => 0, 'loss' => 0, 'draw' => 0];
            $decisive = (int)$rec['win'] + (int)$rec['loss'];
            $leaguesReached = $leaguesReachedMap[$r['id']] ?? [];
            if (in_array($r['league'], ['A', 'B', 'C', 'D'], true)) $leaguesReached[] = $r['league'];
            $result[] = [
                'id' => $r['id'], 'display_name' => $r['display_name'], 'clan_root_id' => $r['clan_root_id'],
                'retired' => (int)$r['retired'],
                'elo' => round((float)$r['elo_rating'], 1), 'peak_elo' => round((float)$r['peak_elo'], 1),
                'age' => compute_age($r['initial_age'], $r['total_seasons']),
                'total' => (int)$rec['total'], 'win' => (int)$rec['win'], 'loss' => (int)$rec['loss'],
                'draw' => (int)($rec['draw'] ?? 0),
                'win_rate' => $decisive >= $CUMULATIVE_MIN_GAMES_FOR_RATE ? round($rec['win'] / $decisive, 4) : null,
                'title_count' => $titleCounts[$r['id']] ?? 0,
                'eternal_titles' => $eternalIds[$r['id']] ?? [],
                'dan' => calc_dan((int)$rec['win'], $leaguesReached, $titleCountsByTitle[$r['id']] ?? [], !empty($eternalIds[$r['id']])),
            ];
        }

        // 通算同一カード対戦ランキング（特定の2個体の組み合わせで、通算何回対戦したか＋勝敗数・引分数）
        $matchupStmt = $pdo->query(
            "SELECT LEAST(individual_a_id, individual_b_id) AS id_a,
                    GREATEST(individual_a_id, individual_b_id) AS id_b,
                    COUNT(*) AS cnt,
                    SUM(CASE
                          WHEN individual_a_id = LEAST(individual_a_id, individual_b_id) AND result = 'win' THEN 1
                          WHEN individual_a_id = GREATEST(individual_a_id, individual_b_id) AND result = 'loss' THEN 1
                          ELSE 0
                        END) AS a_wins,
                    SUM(CASE
                          WHEN individual_a_id = LEAST(individual_a_id, individual_b_id) AND result = 'loss' THEN 1
                          WHEN individual_a_id = GREATEST(individual_a_id, individual_b_id) AND result = 'win' THEN 1
                          ELSE 0
                        END) AS b_wins,
                    SUM(CASE WHEN result = 'draw' THEN 1 ELSE 0 END) AS draws
             FROM matches
             WHERE individual_a_id IS NOT NULL AND individual_b_id IS NOT NULL
             GROUP BY id_a, id_b
             ORDER BY cnt DESC
             LIMIT 50"
        );
        $matchups = $matchupStmt->fetchAll();
        $matchupIds = array_unique(array_merge(array_column($matchups, 'id_a'), array_column($matchups, 'id_b')));
        $matchupNameMap = [];
        if ($matchupIds) {
            $ph = implode(',', array_fill(0, count($matchupIds), '?'));
            $mnStmt = $pdo->prepare(
                "SELECT id, display_name, 0 AS retired FROM individuals WHERE id IN ($ph)
                 UNION ALL
                 SELECT id, display_name, 1 AS retired FROM retired_archive WHERE id IN ($ph)"
            );
            $mnStmt->execute(array_merge($matchupIds, $matchupIds));
            foreach ($mnStmt->fetchAll() as $row) { $matchupNameMap[$row['id']] = $row; }
        }
        $byMatchup = [];
        foreach ($matchups as $mu) {
            // 勝数が多い方を左側（表示上のA）に配置する。同数の場合は元の並び（LEAST側）のまま
            $idLeft = $mu['id_a']; $idRight = $mu['id_b'];
            $winsLeft = (int)$mu['a_wins']; $winsRight = (int)$mu['b_wins'];
            if ($winsRight > $winsLeft) {
                [$idLeft, $idRight] = [$idRight, $idLeft];
                [$winsLeft, $winsRight] = [$winsRight, $winsLeft];
            }
            $infoLeft = $matchupNameMap[$idLeft] ?? ['display_name' => $idLeft, 'retired' => 0];
            $infoRight = $matchupNameMap[$idRight] ?? ['display_name' => $idRight, 'retired' => 0];
            $byMatchup[] = [
                'id_a' => $idLeft, 'display_name_a' => $infoLeft['display_name'], 'retired_a' => (int)$infoLeft['retired'],
                'id_b' => $idRight, 'display_name_b' => $infoRight['display_name'], 'retired_b' => (int)$infoRight['retired'],
                'win_a' => $winsLeft, 'win_b' => $winsRight, 'draw' => (int)$mu['draws'],
                'count' => (int)$mu['cnt'],
            ];
        }

        json_out([
            'ranking' => $result, 'min_games_for_rate' => $CUMULATIVE_MIN_GAMES_FOR_RATE,
            'by_matchup' => $byMatchup,
        ]);
        break;

    // --------------------------------------------------------
    default:
        json_error('不明な action です: ' . htmlspecialchars($action));
}
