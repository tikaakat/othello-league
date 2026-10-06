<?php
// ============================================================
// 応援ページ（無料・名前リスト版）：実際の金銭のやり取りは一切発生しない。
// 訪問者が名前（任意のニックネーム）と一言コメント（任意）を登録し、
// 応援者一覧に掲載される。create_character.phpと同様にIPハッシュで乱用を防止する。
// GET：一覧取得。POST：新規登録。
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

$pdo = league_db_connect();

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    $limit = min(200, max(1, (int)($_GET['limit'] ?? 100)));
    $stmt = $pdo->prepare(
        "SELECT display_name, message, created_at FROM supporters ORDER BY created_at DESC LIMIT :limit"
    );
    $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
    $stmt->execute();
    echo json_encode(['supporters' => $stmt->fetchAll()], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'GETまたはPOSTのみ対応しています']);
    exit;
}

$body = json_decode(file_get_contents('php://input'), true) ?? [];
$name = trim((string)($body['display_name'] ?? ''));
$message = trim((string)($body['message'] ?? ''));

if ($name === '') {
    http_response_code(400);
    echo json_encode(['error' => '名前を入力してください']);
    exit;
}
if (mb_strlen($name) > 30) {
    http_response_code(400);
    echo json_encode(['error' => '名前は30文字以内で入力してください']);
    exit;
}
if (mb_strlen($message) > 100) {
    http_response_code(400);
    echo json_encode(['error' => 'コメントは100文字以内で入力してください']);
    exit;
}

$ipHash = hash('sha256', ($_SERVER['REMOTE_ADDR'] ?? 'unknown') . '|othello-league-supporter');

// 同一IPからの登録は1日3件まで（乱用防止。応援は何度でもできるが、荒らし的な連投は防ぐ）
$rateStmt = $pdo->prepare(
    "SELECT COUNT(*) FROM supporters WHERE ip_hash = :ip AND created_at >= (NOW() - INTERVAL 1 DAY)"
);
$rateStmt->execute(['ip' => $ipHash]);
if ((int)$rateStmt->fetchColumn() >= 3) {
    http_response_code(429);
    echo json_encode(['error' => '本日の投稿上限（3件）に達しました。また明日お試しください。']);
    exit;
}

$insStmt = $pdo->prepare(
    "INSERT INTO supporters (display_name, message, ip_hash) VALUES (:name, :message, :ip)"
);
$insStmt->execute([
    'name' => $name, 'message' => $message !== '' ? $message : null, 'ip' => $ipHash,
]);

echo json_encode(['success' => true], JSON_UNESCAPED_UNICODE);
