<?php
// ============================================================
// お問い合わせ機能：ファンサイトに設置した「問い合わせ」タブから送られてくる
// 自由記述の要望・意見を保存するだけの公開エンドポイント。
// 想定用途の一つが「NHK杯・JT杯のような、段位・タイトル保持・降級点免除に
// 一切影響しない単発の冠スポンサー杯を新設したい」という協賛ニーズの申し出。
// こうした新規棋戦は需要が実際にあるかどうか分からないため、恒久的な自動化は
// 見送り、ここに溜まった要望を見て手動で対応するかどうかを判断する運用とする。
// 乱用防止のため、同一IPからは1日5件まで。
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';

header('Content-Type: application/json; charset=utf-8');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'POSTのみ対応しています']);
    exit;
}

$body = json_decode(file_get_contents('php://input'), true) ?? [];
$category = trim((string)($body['category'] ?? 'その他'));
$message = trim((string)($body['message'] ?? ''));
// 返信先（任意・自由記述。メールアドレス/SNS等、こちらで形式を限定しない）
$contact = trim((string)($body['contact'] ?? ''));

$ALLOWED_CATEGORIES = ['新規棋戦の提案', 'バグ報告', 'ご意見・ご感想', 'その他'];
if (!in_array($category, $ALLOWED_CATEGORIES, true)) {
    $category = 'その他';
}

if ($message === '') {
    http_response_code(400);
    echo json_encode(['error' => '内容を入力してください']);
    exit;
}
if (mb_strlen($message) > 1000) {
    http_response_code(400);
    echo json_encode(['error' => '内容は1000文字以内で入力してください']);
    exit;
}
if (mb_strlen($contact) > 100) {
    http_response_code(400);
    echo json_encode(['error' => '返信先は100文字以内で入力してください']);
    exit;
}

$pdo = league_db_connect();

// IPアドレスは生のまま保存せず、ハッシュ化してレート制限の照合にのみ使う
$ipHash = hash('sha256', ($_SERVER['REMOTE_ADDR'] ?? 'unknown') . '|othello-league-inquiry');

$IP_DAILY_LIMIT = 5;
$rateStmt = $pdo->prepare(
    "SELECT COUNT(*) FROM inquiries WHERE ip_hash = :ip AND created_at >= (NOW() - INTERVAL 1 DAY)"
);
$rateStmt->execute(['ip' => $ipHash]);
if ((int)$rateStmt->fetchColumn() >= $IP_DAILY_LIMIT) {
    http_response_code(429);
    echo json_encode(['error' => '本日の送信上限に達しました。また明日お試しください。']);
    exit;
}

$insStmt = $pdo->prepare(
    "INSERT INTO inquiries (category, message, contact, ip_hash) VALUES (:category, :message, :contact, :ip)"
);
$insStmt->execute([
    'category' => $category,
    'message' => $message,
    'contact' => $contact !== '' ? $contact : null,
    'ip' => $ipHash,
]);

echo json_encode([
    'success' => true,
    'message' => 'お問い合わせを受け付けました。内容によっては返信できない場合があります。',
], JSON_UNESCAPED_UNICODE);
