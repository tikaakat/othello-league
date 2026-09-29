<?php
// ============================================================
// キャラクリエイト機能：サイト訪問者が「名前＋タイプ傾向」を指定して新規個体の
// 作成をリクエストするための公開エンドポイント。character_requestsに保存するだけで、
// 実際には翌朝の新人リーグ（newcomer_league.yml）で選抜され、その勝者だけが
// 次回のシーズン実行時にDリーグへ新規参入する。
// 乱用防止のため、同一IPからは24時間に1回まで。
// また、1日あたりの募集人数にはグローバル上限（NEWCOMER_SUBMISSION_CAP）を設け、
// 達した場合は「締め切りました」として新規受付を停止する
// （上限はPython側 run_newcomer_league.py の NEWCOMER_SUBMISSION_CAP と一致させること）。
// 新人リーグ実行時（毎朝6:00 JST）にpending分は取得・status='fetched'化されるため、
// このカウントは実質的に「その日の募集分」を表す。
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';

header('Content-Type: application/json; charset=utf-8');

// 募集状況（受付中/締め切り・待機メンバー一覧）の確認は api.php?action=character_creation_status 側で行う

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'POSTのみ対応しています']);
    exit;
}

$ALLOWED_TYPES = ['balanced', 'aggressive', 'defensive', 'corner'];
// Python側（othello_league/league.py）のPARAM_KEYS・CHARACTER_PARAM_MIN/MAX/BUDGETと一致させること
$PARAM_KEYS = [
    'corner_weight', 'danger_zone_weight', 'mobility_weight', 'edge_stability_weight',
    'frontier_weight', 'disc_weight', 'parity_weight', 'center_weight',
];
$PARAM_MIN = 0.1;
$PARAM_MAX = 10.0;
$PARAM_BUDGET = 40.0;
$SUBMISSION_CAP = 40;

$body = json_decode(file_get_contents('php://input'), true) ?? [];
$name = trim((string)($body['name'] ?? ''));
$type = (string)($body['type'] ?? 'balanced');
$customParams = $body['params'] ?? null;
// 投稿者名（任意）。表示専用のクレジットで、審査・集計には使わない
$submitterName = trim((string)($body['submitter_name'] ?? ''));
if (mb_strlen($submitterName) > 20) {
    $submitterName = mb_substr($submitterName, 0, 20);
}

if ($name === '') {
    http_response_code(400);
    echo json_encode(['error' => '名前を入力してください']);
    exit;
}
if (mb_strlen($name) > 20) {
    http_response_code(400);
    echo json_encode(['error' => '名前は20文字以内で入力してください']);
    exit;
}
if (!in_array($type, $ALLOWED_TYPES, true)) {
    http_response_code(400);
    echo json_encode(['error' => 'タイプ傾向の指定が不正です']);
    exit;
}

// 詳細設定（8パラメータの直接割り振り）が指定されていれば検証する。
// 範囲・合計予算をわずかに超える程度は許容し、Python側で比率を保ったまま予算内に丸める
// （合計予算はPARAM_BUDGETを参照。ここでは明らかに不正な値だけを弾く）
$paramsJson = null;
if (is_array($customParams)) {
    foreach ($PARAM_KEYS as $k) {
        if (!isset($customParams[$k]) || !is_numeric($customParams[$k])) {
            http_response_code(400);
            echo json_encode(['error' => "パラメータ「{$k}」が不正です"]);
            exit;
        }
        $v = (float)$customParams[$k];
        if ($v < 0 || $v > $PARAM_MAX * 1.5) {
            http_response_code(400);
            echo json_encode(['error' => "パラメータ「{$k}」の値が範囲外です"]);
            exit;
        }
    }
    $paramsJson = json_encode(
        array_combine($PARAM_KEYS, array_map(fn($k) => round((float)$customParams[$k], 3), $PARAM_KEYS)),
        JSON_UNESCAPED_UNICODE
    );
}

$pdo = league_db_connect();

// 本日の募集人数（グローバル上限）チェック。newcomer_league.yml実行（毎朝6:00 JST）で
// pending分がfetchedに更新されるため、実質的に「その日の募集分」の件数になる
$capCount = (int)$pdo->query("SELECT COUNT(*) FROM character_requests WHERE status = 'pending'")->fetchColumn();
if ($capCount >= $SUBMISSION_CAP) {
    http_response_code(429);
    echo json_encode([
        'error' => "本日の募集人数（{$SUBMISSION_CAP}名）に達したため、受付を締め切りました。また次回よろしくお願いします。",
        'closed' => true,
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

// IPアドレスは生のまま保存せず、ハッシュ化してレート制限の照合にのみ使う
$ipHash = hash('sha256', ($_SERVER['REMOTE_ADDR'] ?? 'unknown') . '|othello-league-charcreate');

// 同一IPからの上限。新人リーグが1日2回（6:00・17:00締切）実行されるのに合わせ、
// 「24時間」ではなく「直近の締切以降」で数える（＝各回ごとに上限がリセットされる）。
// created_atはDBサーバーのローカル時刻（xserverのためJST想定）で入るため、
// 締切境界の判定もMySQL側のNOW()を基準に行い、PHP側のタイムゾーン設定に依存しないようにする
$IP_CYCLE_LIMIT = 3;
$IP_LIMIT_ENABLED = true;
if ($IP_LIMIT_ENABLED) {
    $rateStmt = $pdo->prepare(
        "SELECT COUNT(*) FROM character_requests
         WHERE requester_ip_hash = :ip
           AND created_at >= (
             CASE
               WHEN TIME(NOW()) >= '17:00:00' THEN CONCAT(DATE(NOW()), ' 17:00:00')
               WHEN TIME(NOW()) >= '06:00:00' THEN CONCAT(DATE(NOW()), ' 06:00:00')
               ELSE CONCAT(DATE(NOW() - INTERVAL 1 DAY), ' 17:00:00')
             END
           )"
    );
    $rateStmt->execute(['ip' => $ipHash]);
    if ((int)$rateStmt->fetchColumn() >= $IP_CYCLE_LIMIT) {
        http_response_code(429);
        echo json_encode(['error' => "今回の受付枠（{$IP_CYCLE_LIMIT}体）に達しました。次回の締切（6:00/17:00）以降にまたお試しください。"]);
        exit;
    }
}

$insStmt = $pdo->prepare(
    "INSERT INTO character_requests (display_name, type_tendency, params_json, requester_ip_hash, submitter_name, status)
     VALUES (:name, :type, :params_json, :ip, :submitter_name, 'pending')"
);
$insStmt->execute([
    'name' => $name, 'type' => $type, 'params_json' => $paramsJson, 'ip' => $ipHash,
    'submitter_name' => $submitterName !== '' ? $submitterName : null,
]);

echo json_encode([
    'success' => true,
    'message' => "「{$name}」の作成をリクエストしました。明日6:00の新人リーグに出場し、上位者のみが次回のシーズン実行時にDリーグへ新規参入します。",
], JSON_UNESCAPED_UNICODE);
