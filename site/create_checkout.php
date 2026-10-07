<?php
// ============================================================
// サポーター支援の決済（Stripe Checkout）開始エンドポイント。
// ファンサイト（news-site）のサポーター登録モーダルから呼ばれる想定。
// individual_id・金額（100/300/500円の3段階のみ）・表示名・一言を受け取り、
// Stripe Checkout Sessionを作成してその遷移先URLを返す。
// ここでは一切DBへ書き込まない。支払いが実際に確定した時点で
// stripe_webhook.php（Webhook経由）側がsponsors/permanent_sponsorsへ記録する
// （Checkoutページを開いただけで未決済のまま離脱した分は記録されない）。
//
// Stripeの秘密鍵はstripe_config.php（db_config.phpと同様、Git管理外・
// サーバー上に直接設置する）のstripe_secret_key()から取得する
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';
require_once __DIR__ . '/stripe_config.php';
require_once __DIR__ . '/stripe_client.php';

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'POSTのみ対応しています']);
    exit;
}

$body = json_decode(file_get_contents('php://input'), true) ?? [];

$individualId = trim((string)($body['individual_id'] ?? ''));
if ($individualId === '' || mb_strlen($individualId) > 32) {
    http_response_code(400);
    echo json_encode(['error' => 'individual_id が不正です']);
    exit;
}

$ALLOWED_AMOUNTS = [100, 300, 500];
$amount = (int)($body['amount'] ?? 0);
if (!in_array($amount, $ALLOWED_AMOUNTS, true)) {
    http_response_code(400);
    echo json_encode(['error' => '金額は100円・300円・500円のいずれかのみ対応しています']);
    exit;
}

$displayName = trim((string)($body['display_name'] ?? ''));
if ($displayName === '') $displayName = '名無しの支援者';
if (mb_strlen($displayName) > 30) $displayName = mb_substr($displayName, 0, 30);

// 100円（梅）は名前のみ掲載のため一言は受け取らない。300円以上は一言必須
$message = trim((string)($body['message'] ?? ''));
if (mb_strlen($message) > 100) $message = mb_substr($message, 0, 100);
if ($amount === 100) {
    $message = '';
} elseif ($message === '') {
    http_response_code(400);
    echo json_encode(['error' => '300円以上は一言メッセージが必須です']);
    exit;
}

$pdo = league_db_connect();
$checkStmt = $pdo->prepare(
    "SELECT 1 FROM individuals WHERE id = :id
     UNION ALL SELECT 1 FROM retired_archive WHERE id = :id2 LIMIT 1"
);
$checkStmt->execute(['id' => $individualId, 'id2' => $individualId]);
if (!$checkStmt->fetchColumn()) {
    http_response_code(404);
    echo json_encode(['error' => '個体が見つかりません']);
    exit;
}

// 500円＝スペシャルサポーターは1個体につき先着1名限定（永久スポンサー枠そのもの）。
// 既に埋まっている個体には、決済ページの作成自体を許可しない（二重販売防止）。
// 最終的な確定判定はstripe_webhook.php側のpermanent_sponsorsへのINSERT（一意制約）
// で行うため、ここでのチェックはあくまで無駄な決済ページ作成を防ぐための事前チェック
if ($amount >= 500) {
    $permCheckStmt = $pdo->prepare("SELECT 1 FROM permanent_sponsors WHERE individual_id = :id");
    $permCheckStmt->execute(['id' => $individualId]);
    if ($permCheckStmt->fetchColumn()) {
        http_response_code(409);
        echo json_encode(['error' => 'スペシャルサポーター枠は既に埋まっています']);
        exit;
    }
}

// ファンサイト（news-site）側の個体ページへ、決済結果（成功／キャンセル）を
// クエリパラメータで付けて戻す。ハッシュルーティングと干渉しないよう、
// クエリは#より前に置く
$newsBase = 'https://lab.spia-net.com/othello-news/';
$successUrl = $newsBase . '?sponsor=success#individual/' . rawurlencode($individualId);
$cancelUrl = $newsBase . '?sponsor=cancel#individual/' . rawurlencode($individualId);

$params = [
    'mode' => 'payment',
    'submit_type' => 'donate',
    'locale' => 'ja',
    'success_url' => $successUrl,
    'cancel_url' => $cancelUrl,
    'line_items' => [[
        'quantity' => 1,
        'price_data' => [
            'currency' => 'jpy',
            'unit_amount' => $amount,
            'product_data' => ['name' => mb_substr($displayName, 0, 30) . 'さんのサポーター支援'],
        ],
    ]],
    'metadata' => [
        'individual_id' => $individualId,
        'display_name' => $displayName,
        'message' => $message,
    ],
];

[$ok, $result] = stripe_api_request('POST', '/v1/checkout/sessions', $params);
if (!$ok || empty($result['url'])) {
    http_response_code(502);
    echo json_encode(['error' => '決済ページの作成に失敗しました']);
    exit;
}

echo json_encode(['url' => $result['url']], JSON_UNESCAPED_UNICODE);
