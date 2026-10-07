<?php
// ============================================================
// 個体への有料支援（スポンサー）機能。
// このファイルは現時点ではGET（一覧取得）のみを提供する。
// 実際の決済（Stripe Checkout）・Webhook受信によるsponsors/permanent_sponsors
// テーブルへの書き込みは別途追加する（見た目を先行実装している段階のため、
// 書き込みエンドポイントはまだ無い）。
// DBマイグレーションはdeploy_site.ymlのrun_migration=add_sponsor_feature・
// add_permanent_sponsor_featureで適用済みが前提。
//
// GET：個体への支援者一覧（sponsors）と、永久スポンサー枠（permanent_sponsors、
// 1個体につき先着1名のみ。埋まっていなければpermanentはnull）を取得する。
// 金額そのものは返さず、松（500円以上）・竹（300円以上）・梅（それ未満）の
// 3段階の支援ランクのみを返す（表示側で松竹梅として区分けするため）
// ============================================================

function sponsor_tier($amountJpy) {
    if ($amountJpy >= 500) return '松';
    if ($amountJpy >= 300) return '竹';
    return '梅';
}

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    http_response_code(405);
    echo json_encode(['error' => 'GETのみ対応しています']);
    exit;
}

$individualId = trim((string)($_GET['individual_id'] ?? ''));
if ($individualId === '' || mb_strlen($individualId) > 32) {
    http_response_code(400);
    echo json_encode(['error' => 'individual_id が不正です']);
    exit;
}

$pdo = league_db_connect();
$limit = min(50, max(1, (int)($_GET['limit'] ?? 20)));

$stmt = $pdo->prepare(
    "SELECT display_name, message, amount_jpy FROM sponsors
     WHERE individual_id = :id ORDER BY created_at DESC LIMIT :limit"
);
$stmt->bindValue(':id', $individualId);
$stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
$stmt->execute();
$sponsors = array_map(function ($row) {
    return [
        'display_name' => $row['display_name'], 'message' => $row['message'],
        'tier' => sponsor_tier((int)$row['amount_jpy']),
    ];
}, $stmt->fetchAll());

$permStmt = $pdo->prepare("SELECT display_name, message FROM permanent_sponsors WHERE individual_id = :id");
$permStmt->execute(['id' => $individualId]);
$permanent = $permStmt->fetch() ?: null;

echo json_encode(['sponsors' => $sponsors, 'permanent' => $permanent], JSON_UNESCAPED_UNICODE);
