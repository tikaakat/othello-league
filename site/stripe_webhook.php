<?php
// ============================================================
// Stripe Webhook受信エンドポイント。checkout.session.completed を受けて
// sponsors（常に）・permanent_sponsors（500円以上かつ空席の場合のみ）に
// 1行ずつ記録する。stripe_session_idのUNIQUE制約により、Webhookの重複配信
// （Stripeは同一イベントを複数回送ることがある）があっても二重計上しない。
// permanent_sponsorsはindividual_id自体がPRIMARY KEYなので、既に先着者が
// いる個体への2件目の書き込みも自然に失敗し、先着1名が保証される。
//
// 署名検証はstripe_client.phpのstripe_verify_webhook_signature()で行う
// （Stripe公式PHP SDKは使わず自前実装。詳細はそちらのコメント参照）
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';
require_once __DIR__ . '/stripe_config.php';
require_once __DIR__ . '/stripe_client.php';

header('Content-Type: application/json; charset=utf-8');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    exit;
}

$rawBody = file_get_contents('php://input');
$sigHeader = $_SERVER['HTTP_STRIPE_SIGNATURE'] ?? '';

if (!stripe_verify_webhook_signature($rawBody, $sigHeader, stripe_webhook_secret())) {
    http_response_code(400);
    echo json_encode(['error' => 'signature verification failed']);
    exit;
}

$event = json_decode($rawBody, true);
if (!$event || ($event['type'] ?? '') !== 'checkout.session.completed') {
    http_response_code(200);
    echo json_encode(['received' => true]);
    exit;
}

$session = $event['data']['object'] ?? [];
if (($session['payment_status'] ?? '') !== 'paid') {
    http_response_code(200);
    echo json_encode(['received' => true]);
    exit;
}

$sessionId = (string)($session['id'] ?? '');
$metadata = $session['metadata'] ?? [];
$individualId = trim((string)($metadata['individual_id'] ?? ''));
$displayName = trim((string)($metadata['display_name'] ?? '')) ?: '名無しの支援者';
$message = trim((string)($metadata['message'] ?? ''));
// JPYはゼロ小数通貨（セント相当の概念が無い）ため、amount_totalはそのまま円単位
$amountJpy = (int)($session['amount_total'] ?? 0);

if ($sessionId === '' || $individualId === '' || $amountJpy <= 0) {
    http_response_code(200);
    echo json_encode(['received' => true, 'skipped' => true]);
    exit;
}

$pdo = league_db_connect();

try {
    $ins = $pdo->prepare(
        "INSERT INTO sponsors (individual_id, display_name, message, amount_jpy, currency, stripe_session_id)
         VALUES (:id, :name, :message, :amount, 'jpy', :sid)"
    );
    $ins->execute([
        'id' => $individualId, 'name' => $displayName,
        'message' => $message !== '' ? $message : null,
        'amount' => $amountJpy, 'sid' => $sessionId,
    ]);
} catch (\PDOException $e) {
    // 23000=一意制約違反（Webhookの重複配信）。それ以外は非2xxを返してStripeに再送させる
    if ($e->getCode() !== '23000') { throw $e; }
}

if ($amountJpy >= 500) {
    try {
        $permIns = $pdo->prepare(
            "INSERT INTO permanent_sponsors (individual_id, display_name, message, amount_jpy, stripe_session_id)
             VALUES (:id, :name, :message, :amount, :sid)"
        );
        $permIns->execute([
            'id' => $individualId, 'name' => $displayName,
            'message' => $message !== '' ? $message : null,
            'amount' => $amountJpy, 'sid' => $sessionId,
        ]);
    } catch (\PDOException $e) {
        // 23000＝既に別の支援者が先着しているか、Webhookの重複配信。どちらも無視してよい
        if ($e->getCode() !== '23000') { throw $e; }
    }
}

echo json_encode(['received' => true]);
