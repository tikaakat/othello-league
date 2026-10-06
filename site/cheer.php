<?php
// ============================================================
// 応援機能：サイト訪問者が個体を「応援する」ための公開エンドポイント。
// 実際の金銭のやり取りは一切発生しない、閲覧専用カウンタ（いいね的な機能）。
// individuals/retired_archiveのcheer_countを+1し、乱用防止のため
// 同一IP・同一個体では1日1回までに制限する（cheer_logテーブルで判定）。
// 応援と同時に一言コメント（任意・匿名）を残せる（message列）。
// DBマイグレーションはdeploy_site.ymlのrun_migration=add_cheer_feature・
// add_cheer_message（message/created_at列の追加）で適用済みが前提。
//
// GET（action=comments）：個体への応援コメント一覧を取得（書き込み無し）
// POST：応援を1件記録する（messageがあれば一緒に保存）
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

$pdo = league_db_connect();

if ($_SERVER['REQUEST_METHOD'] === 'GET' && ($_GET['action'] ?? '') === 'comments') {
    $individualId = trim((string)($_GET['individual_id'] ?? ''));
    if ($individualId === '' || mb_strlen($individualId) > 32) {
        http_response_code(400);
        echo json_encode(['error' => 'individual_id が不正です']);
        exit;
    }
    $limit = min(50, max(1, (int)($_GET['limit'] ?? 20)));
    $stmt = $pdo->prepare(
        "SELECT message, created_at FROM cheer_log
         WHERE individual_id = :id AND message IS NOT NULL AND message != ''
         ORDER BY created_at DESC LIMIT :limit"
    );
    $stmt->bindValue(':id', $individualId);
    $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
    $stmt->execute();
    echo json_encode(['comments' => $stmt->fetchAll()], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'GET(action=comments)またはPOSTのみ対応しています']);
    exit;
}

$body = json_decode(file_get_contents('php://input'), true) ?? [];
$individualId = trim((string)($body['individual_id'] ?? ''));
if ($individualId === '' || mb_strlen($individualId) > 32) {
    http_response_code(400);
    echo json_encode(['error' => 'individual_id が不正です']);
    exit;
}
$message = trim((string)($body['message'] ?? ''));
if (mb_strlen($message) > 140) {
    $message = mb_substr($message, 0, 140);
}

// IPアドレスは生のまま保存せず、ハッシュ化してレート制限の照合にのみ使う
// （キャラクリエイトのrequester_ip_hashと同じ考え方だが、用途が違うのでソルトは別にする）
$ipHash = hash('sha256', ($_SERVER['REMOTE_ADDR'] ?? 'unknown') . '|othello-league-cheer');

try {
    $insStmt = $pdo->prepare(
        "INSERT INTO cheer_log (individual_id, ip_hash, cheered_date, message) VALUES (:id, :ip, CURDATE(), :message)"
    );
    $insStmt->execute(['id' => $individualId, 'ip' => $ipHash, 'message' => $message !== '' ? $message : null]);
} catch (\PDOException $e) {
    // 一意制約違反＝本日すでに応援済み。新しくカウントは増やさず、現在値を返す
    // （同日の2回目以降はコメントの追加・更新もしない。1日1件に統一するため）
    if ($e->getCode() === '23000') {
        echo json_encode([
            'success' => true, 'already_cheered' => true,
            'cheer_count' => fetch_cheer_count($pdo, $individualId),
        ], JSON_UNESCAPED_UNICODE);
        exit;
    }
    throw $e;
}

$updated = $pdo->prepare("UPDATE individuals SET cheer_count = cheer_count + 1 WHERE id = :id");
$updated->execute(['id' => $individualId]);
if ($updated->rowCount() === 0) {
    $updated = $pdo->prepare("UPDATE retired_archive SET cheer_count = cheer_count + 1 WHERE id = :id");
    $updated->execute(['id' => $individualId]);
}
if ($updated->rowCount() === 0) {
    // 個体が存在しない：先に入れたcheer_logの行を取り消す
    $delStmt = $pdo->prepare("DELETE FROM cheer_log WHERE individual_id = :id AND ip_hash = :ip AND cheered_date = CURDATE()");
    $delStmt->execute(['id' => $individualId, 'ip' => $ipHash]);
    http_response_code(404);
    echo json_encode(['error' => '個体が見つかりません']);
    exit;
}

echo json_encode([
    'success' => true, 'already_cheered' => false,
    'cheer_count' => fetch_cheer_count($pdo, $individualId),
], JSON_UNESCAPED_UNICODE);

function fetch_cheer_count($pdo, $individualId) {
    $stmt = $pdo->prepare(
        "SELECT cheer_count FROM individuals WHERE id = :id
         UNION ALL
         SELECT cheer_count FROM retired_archive WHERE id = :id2
         LIMIT 1"
    );
    $stmt->execute(['id' => $individualId, 'id2' => $individualId]);
    $count = $stmt->fetchColumn();
    return $count !== false ? (int)$count : 0;
}
