<?php
// ============================================================
// 勝敗予想（データサイトの「あそぶ」→「勝敗予想」）：次の季の5つのタイトルが「防衛」か「奪取（保持者が替わる）」かを予想する。
// 当たれば1つにつき1ポイント。ランキングは通算ポイント順。
//
// 受付は「次に公開される季」だけ。季の結果がサイトに取り込まれた時点（毎日7:00・19:00ごろ）でその季は締め切られ、
// 次の季の受付が始まる（standingsの最大の季＝公開済みの最新の季、の次が受付中の季）。
// 利用者はブラウザが作るランダムなID（32桁の16進数）で区別する。ログインは無い。
//
// GET  ?action=status&user=ID   受付中の季、各タイトルの保持者、自分の予想、自分の成績
// GET  ?action=ranking&user=ID  ランキング（上位30名）と自分の順位、直近の季の答え合わせ
// POST {"action":"save","user","nickname","picks":{"青龍":"defend"|"change",...}}  受付中の季の予想を保存（上書き可）
//
// 荒らし対策：同じ接続元からの保存は1日60回まで。ニックネームはリンク・NGワード不可、20文字まで。
// 接続元のIPは生のまま保存せず、ハッシュにして回数の判定にだけ使う。
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

const PREDICT_TITLES = ['青龍', '麒麟', '朱雀', '白虎', '玄武'];
const PREDICT_NG_WORDS = ['死ね', '殺す', 'しね', 'ころす', '殺害', 'セックス', 'ちんこ', 'まんこ', 'カジノ', '出会い系', '副業', '稼げる'];
const PREDICT_LIMIT_DAY = 60;
const PREDICT_DEFAULT_NAME = '名無しの予想家';

function out($data, $code = 200) {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function valid_user($u) {
    return is_string($u) && preg_match('/^[0-9a-f]{32}$/', $u);
}

$pdo = league_db_connect();
$pdo->exec("CREATE TABLE IF NOT EXISTS predictions (
    user_id CHAR(32) NOT NULL,
    season INT NOT NULL,
    title VARCHAR(8) NOT NULL,
    pick VARCHAR(8) NOT NULL,
    nickname VARCHAR(20) NOT NULL,
    ip_hash CHAR(64) NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, season, title),
    INDEX (season),
    INDEX (ip_hash, updated_at)
) DEFAULT CHARSET=utf8mb4");

// 公開済みの最新の季（＝この季までの結果はサイトに出ている）
$released = (int)$pdo->query("SELECT COALESCE(MAX(season), 0) FROM standings")->fetchColumn();
$open = $released + 1;

// 季ごと・タイトルごとの保持者（季の終わり時点）。「季Sで防衛」＝ 季S-1と季Sの保持者が同じ
function holders_by_season($pdo) {
    $map = [];
    foreach ($pdo->query("SELECT season, title, holder_id, holder_name FROM title_history WHERE holder_id IS NOT NULL ORDER BY season")->fetchAll() as $r) {
        $map[(int)$r['season']][$r['title']] = ['id' => $r['holder_id'], 'name' => $r['holder_name']];
    }
    return $map;
}
// 季Sのタイトルtの結果（'defend'／'change'）。どちらとも言えない（前の季に保持者がいない等）ときはnull
function outcome($holders, $s, $t) {
    $cur = $holders[$s][$t] ?? null; $prev = null;
    for ($k = $s - 1; $k >= 1; $k--) if (isset($holders[$k][$t])) { $prev = $holders[$k][$t]; break; }
    if ($cur === null || $prev === null) return null;
    return $cur['id'] === $prev['id'] ? 'defend' : 'change';
}
// 公開済みの予想をすべて採点して、利用者ごとの成績を返す
function score_all($pdo, $holders, $released) {
    $st = $pdo->prepare("SELECT user_id, season, title, pick, nickname FROM predictions WHERE season <= :r ORDER BY season");
    $st->execute(['r' => $released]);
    $users = [];
    foreach ($st->fetchAll() as $p) {
        $o = outcome($holders, (int)$p['season'], $p['title']);
        if ($o === null) continue;
        $u = &$users[$p['user_id']];
        if (!$u) $u = ['points' => 0, 'answered' => 0, 'seasons' => [], 'nickname' => $p['nickname']];
        $u['answered']++;
        if ($o === $p['pick']) $u['points']++;
        $u['seasons'][(int)$p['season']] = true;
        $u['nickname'] = $p['nickname'];
        unset($u);
    }
    return $users;
}

$method = $_SERVER['REQUEST_METHOD'];

if ($method === 'GET') {
    $action = $_GET['action'] ?? 'status';
    $user = $_GET['user'] ?? '';
    $holders = holders_by_season($pdo);

    if ($action === 'status') {
        // 受付中の季の各タイトルの保持者（公開済みの最新の季の終わり時点）
        $now = [];
        foreach (PREDICT_TITLES as $t) {
            $h = null;
            for ($k = $released; $k >= 1; $k--) if (isset($holders[$k][$t])) { $h = $holders[$k][$t]; break; }
            $now[$t] = $h;
        }
        $mine = []; $nickname = null;
        if (valid_user($user)) {
            $st = $pdo->prepare("SELECT title, pick, nickname FROM predictions WHERE user_id = :u AND season = :s");
            $st->execute(['u' => $user, 's' => $open]);
            foreach ($st->fetchAll() as $r) { $mine[$r['title']] = $r['pick']; $nickname = $r['nickname']; }
        }
        $count = $pdo->prepare("SELECT COUNT(DISTINCT user_id) FROM predictions WHERE season = :s");
        $count->execute(['s' => $open]);
        // タイトルごとの予想の割合（受付中の季）
        $share = [];
        $sh = $pdo->prepare("SELECT title, pick, COUNT(*) AS n FROM predictions WHERE season = :s GROUP BY title, pick");
        $sh->execute(['s' => $open]);
        foreach ($sh->fetchAll() as $r) $share[$r['title']][$r['pick']] = (int)$r['n'];
        out(['open_season' => $open, 'released_season' => $released, 'titles' => PREDICT_TITLES, 'holders' => $now,
             'mine' => (object)$mine, 'nickname' => $nickname, 'participants' => (int)$count->fetchColumn(), 'share' => (object)$share]);
    }

    if ($action === 'ranking') {
        $users = score_all($pdo, $holders, $released);
        uasort($users, fn($a, $b) => [$b['points'], $a['answered']] <=> [$a['points'], $b['answered']]);
        $rank = 0; $prevPts = null; $i = 0; $top = []; $me = null;
        foreach ($users as $uid => $u) {
            $i++;
            if ($u['points'] !== $prevPts) { $rank = $i; $prevPts = $u['points']; }
            $row = ['rank' => $rank, 'nickname' => $u['nickname'], 'points' => $u['points'], 'answered' => $u['answered'], 'seasons' => count($u['seasons'])];
            if ($i <= 30) $top[] = $row;
            if ($uid === $user) $me = $row;
        }
        // 直近の公開済みの季の答え合わせ（自分の予想つき）
        $last = [];
        $minePrev = [];
        if (valid_user($user)) {
            $st = $pdo->prepare("SELECT title, pick FROM predictions WHERE user_id = :u AND season = :s");
            $st->execute(['u' => $user, 's' => $released]);
            foreach ($st->fetchAll() as $r) $minePrev[$r['title']] = $r['pick'];
        }
        foreach (PREDICT_TITLES as $t) {
            $last[] = ['title' => $t, 'outcome' => outcome($holders, $released, $t), 'holder' => $holders[$released][$t]['name'] ?? null, 'mine' => $minePrev[$t] ?? null];
        }
        out(['released_season' => $released, 'participants' => count($users), 'top' => $top, 'me' => $me, 'last' => $last]);
    }
    out(['error' => 'unknown action'], 400);
}

if ($method === 'POST') {
    $in = json_decode(file_get_contents('php://input'), true) ?: [];
    if (($in['action'] ?? '') !== 'save') out(['error' => 'unknown action'], 400);
    $user = $in['user'] ?? '';
    if (!valid_user($user)) out(['error' => '利用者IDが不正です'], 400);
    $nick = trim((string)($in['nickname'] ?? ''));
    $nickGiven = $nick !== '';
    if (!$nickGiven) {
        // 空欄なら、前に付けたニックネームをそのまま使う（無ければ既定の名前）
        $prev = $pdo->prepare("SELECT nickname FROM predictions WHERE user_id = :u ORDER BY updated_at DESC LIMIT 1");
        $prev->execute(['u' => $user]);
        $nick = $prev->fetchColumn() ?: PREDICT_DEFAULT_NAME;
    }
    if (mb_strlen($nick) > 20) out(['error' => 'ニックネームは20文字までです'], 400);
    if (preg_match('/https?:|www\./i', $nick)) out(['error' => 'ニックネームにリンクは使えません'], 400);
    foreach (PREDICT_NG_WORDS as $w) if (mb_strpos($nick, $w) !== false) out(['error' => 'そのニックネームは使えません'], 400);
    $picks = $in['picks'] ?? [];
    if (!is_array($picks) || !$picks) out(['error' => '予想がありません'], 400);
    $ipHash = hash('sha256', ($_SERVER['REMOTE_ADDR'] ?? 'unknown') . '|othello-league-predict');
    $cnt = $pdo->prepare("SELECT COUNT(*) FROM predictions WHERE ip_hash = :ip AND updated_at >= (NOW() - INTERVAL 1 DAY)");
    $cnt->execute(['ip' => $ipHash]);
    if ((int)$cnt->fetchColumn() >= PREDICT_LIMIT_DAY * 5) out(['error' => '今日はこれ以上保存できません。時間をおいてお試しください'], 429);
    $st = $pdo->prepare("INSERT INTO predictions (user_id, season, title, pick, nickname, ip_hash) VALUES (:u, :s, :t, :p, :n, :ip)
                         ON DUPLICATE KEY UPDATE pick = VALUES(pick), nickname = VALUES(nickname), ip_hash = VALUES(ip_hash)");
    $saved = 0;
    foreach ($picks as $t => $p) {
        if (!in_array($t, PREDICT_TITLES, true) || !in_array($p, ['defend', 'change'], true)) continue;
        $st->execute(['u' => $user, 's' => $open, 't' => $t, 'p' => $p, 'n' => $nick, 'ip' => $ipHash]);
        $saved++;
    }
    // ニックネームは過去の予想にも揃える（ランキングの表示名）
    if ($nickGiven) $pdo->prepare("UPDATE predictions SET nickname = :n WHERE user_id = :u")->execute(['n' => $nick, 'u' => $user]);
    out(['ok' => true, 'season' => $open, 'saved' => $saved]);
}

out(['error' => 'method not allowed'], 405);
