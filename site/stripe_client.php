<?php
// ============================================================
// Stripe REST APIへの薄いラッパー。Stripe公式PHP SDK（stripe-php）は
// Composer前提でxserver（共有サーバー）に設置しづらいため使わず、
// cURLで直接REST APIを呼ぶ。秘密鍵はstripe_config.php（db_config.phpと
// 同様、Git管理外・サーバー上に直接設置するファイル）の
// stripe_secret_key()から取得する
// ============================================================

// $paramsはStripeのネスト配列パラメータ（line_items[0][price_data]...等）を
// そのままPHP配列で渡せる。http_build_query()がStripeの期待する
// ブラケット記法に自動変換してくれる
function stripe_api_request(string $method, string $path, array $params = []): array {
    $ch = curl_init('https://api.stripe.com' . $path);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 15);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
    curl_setopt($ch, CURLOPT_HTTPHEADER, ['Authorization: Bearer ' . stripe_secret_key()]);
    if ($method === 'POST' && $params) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query($params));
    }
    $body = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);

    if ($body === false || $err !== '') {
        error_log('Stripe API通信エラー: ' . $err);
        return [false, null];
    }
    $decoded = json_decode($body, true);
    if ($status < 200 || $status >= 300) {
        error_log('Stripe APIエラー(' . $status . '): ' . $body);
        return [false, $decoded];
    }
    return [true, $decoded];
}

// StripeのWebhook署名を検証する（公式SDK未使用のため自前実装）。
// ヘッダーは "t=<timestamp>,v1=<signature>" の形式。タイムスタンプと生のリクエスト
// ボディを"."で連結した文字列のHMAC-SHA256が、ヘッダー中のv1と一致するかを見る
function stripe_verify_webhook_signature(string $payload, string $sigHeader, string $secret): bool {
    if ($sigHeader === '' || $secret === '') return false;
    $parts = [];
    foreach (explode(',', $sigHeader) as $kv) {
        $pair = array_pad(explode('=', $kv, 2), 2, '');
        $parts[$pair[0]] = $pair[1];
    }
    $timestamp = $parts['t'] ?? '';
    $sig = $parts['v1'] ?? '';
    if ($timestamp === '' || $sig === '') return false;
    // 5分以上古いイベントはリプレイ攻撃とみなし拒否する
    if (abs(time() - (int)$timestamp) > 300) return false;
    $expected = hash_hmac('sha256', $timestamp . '.' . $payload, $secret);
    return hash_equals($expected, $sig);
}
