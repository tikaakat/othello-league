<?php
// 運営用の合言葉（掲示板の運営返信・削除、非公開のお問い合わせの閲覧に使う）。
// 合言葉そのものは保存せず、SHA-256のハッシュだけを持つ（リポジトリは公開のため）
const LEAGUE_ADMIN_KEY_SHA256 = 'f8a8d9661f2aaddb52dbf08a7181c87517f517ea828f3b1c70c534b29af85b2e';

function is_admin_key($key) {
    if (!is_string($key) || $key === '') return false;
    return hash_equals(LEAGUE_ADMIN_KEY_SHA256, hash('sha256', $key));
}
