-- データサイトの結果ページを書き出すときに、GitHub Actions の中の一時的なMySQLに作る表（Xserverの本番DBと同じ作り）
-- cloudflare/build/build.sh から使う。利用者が書き込むデータ（予想・掲示板など）は Cloudflare D1 側にあり、ここでは空のまま

CREATE TABLE `article_likes` (
  `article_id` varchar(64) NOT NULL,
  `ip_hash` char(64) NOT NULL,
  `liked_date` date NOT NULL,
  PRIMARY KEY (`article_id`,`ip_hash`,`liked_date`)
);

CREATE TABLE `board_posts` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `thread_id` int(11) NOT NULL,
  `author` varchar(20) NOT NULL,
  `body` varchar(1000) NOT NULL,
  `is_admin` tinyint(4) NOT NULL DEFAULT 0,
  `ip_hash` char(64) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `deleted` tinyint(4) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `thread_id` (`thread_id`,`id`),
  KEY `ip_hash` (`ip_hash`,`created_at`)
);

CREATE TABLE `board_threads` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `category` varchar(16) NOT NULL,
  `title` varchar(60) NOT NULL,
  `author` varchar(20) NOT NULL,
  `is_admin` tinyint(4) NOT NULL DEFAULT 0,
  `ip_hash` char(64) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `last_post_at` datetime NOT NULL DEFAULT current_timestamp(),
  `post_count` int(11) NOT NULL DEFAULT 0,
  `deleted` tinyint(4) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `deleted` (`deleted`,`last_post_at`)
);

CREATE TABLE `character_requests` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `display_name` varchar(50) NOT NULL,
  `submitter_name` varchar(20) DEFAULT NULL,
  `type_tendency` varchar(32) NOT NULL DEFAULT 'balanced',
  `params_json` text DEFAULT NULL,
  `requester_ip_hash` varchar(64) NOT NULL,
  `status` varchar(16) NOT NULL DEFAULT 'pending',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `fetched_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_status` (`status`),
  KEY `idx_ip_created` (`requester_ip_hash`,`created_at`)
);

CREATE TABLE `cheer_log` (
  `individual_id` varchar(32) NOT NULL,
  `ip_hash` char(64) NOT NULL,
  `cheered_date` date NOT NULL,
  `message` varchar(140) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`individual_id`,`ip_hash`,`cheered_date`)
);

CREATE TABLE `donations` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `stripe_session_id` varchar(255) NOT NULL,
  `amount_jpy` int(11) NOT NULL,
  `display_name` varchar(30) DEFAULT NULL,
  `show_name` tinyint(4) NOT NULL DEFAULT 0,
  `message` varchar(100) DEFAULT NULL,
  `naming_family` varchar(10) DEFAULT NULL,
  `naming_given` varchar(10) DEFAULT NULL,
  `naming_gender` varchar(8) DEFAULT NULL,
  `naming_status` varchar(12) DEFAULT NULL,
  `named_season` int(11) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `stripe_session_id` (`stripe_session_id`)
);

CREATE TABLE `individuals` (
  `id` varchar(64) NOT NULL,
  `display_name` varchar(32) DEFAULT NULL,
  `league` varchar(4) NOT NULL,
  `dojo` varchar(16) DEFAULT NULL,
  `buff_multiplier` float DEFAULT NULL,
  `params_json` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL CHECK (json_valid(`params_json`)),
  `elo_rating` float NOT NULL DEFAULT 1500,
  `generation` int(11) NOT NULL DEFAULT 0,
  `parent_a_id` varchar(64) DEFAULT NULL,
  `parent_b_id` varchar(64) DEFAULT NULL,
  `seasons_in_league` int(11) NOT NULL DEFAULT 0,
  `total_seasons` int(11) NOT NULL DEFAULT 0,
  `retired` tinyint(1) NOT NULL DEFAULT 0,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `awakened_param` varchar(32) DEFAULT NULL,
  `peak_elo` float NOT NULL DEFAULT 1500,
  `initial_age` int(11) DEFAULT NULL,
  `clan_root_id` varchar(64) DEFAULT NULL,
  `cheer_count` int(11) NOT NULL DEFAULT 0,
  `demotion_points` int(11) NOT NULL DEFAULT 0,
  `gender` varchar(10) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_league` (`league`),
  KEY `idx_dojo` (`dojo`),
  KEY `idx_retired` (`retired`),
  KEY `idx_elo` (`elo_rating`)
);

CREATE TABLE `inquiries` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `category` varchar(32) NOT NULL,
  `message` varchar(1000) NOT NULL,
  `contact` varchar(100) DEFAULT NULL,
  `ip_hash` char(64) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`)
);

CREATE TABLE `matches` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `match_uid` char(32) NOT NULL,
  `season` int(11) NOT NULL,
  `league` varchar(16) NOT NULL,
  `stage` varchar(16) DEFAULT NULL,
  `block_no` int(11) DEFAULT NULL,
  `individual_a_id` varchar(64) NOT NULL,
  `individual_b_id` varchar(64) DEFAULT NULL,
  `result` varchar(8) NOT NULL,
  `games_json` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL CHECK (json_valid(`games_json`)),
  `played_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_match_uid` (`match_uid`),
  KEY `idx_season` (`season`),
  KEY `idx_individual_a` (`individual_a_id`),
  KEY `idx_individual_b` (`individual_b_id`)
);

CREATE TABLE `newcomer_league_meta` (
  `for_season` int(11) NOT NULL,
  `slots_needed` int(11) NOT NULL DEFAULT 0,
  `submission_count` int(11) NOT NULL DEFAULT 0,
  `imported_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`for_season`)
);

CREATE TABLE `newcomer_league_standings` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `for_season` int(11) NOT NULL,
  `rank` int(11) DEFAULT NULL,
  `individual_id` varchar(64) NOT NULL,
  `display_name` varchar(64) DEFAULT NULL,
  `win` int(11) NOT NULL DEFAULT 0,
  `loss` int(11) NOT NULL DEFAULT 0,
  `draw` int(11) NOT NULL DEFAULT 0,
  `auto_generated` tinyint(1) NOT NULL DEFAULT 0,
  `promoted` tinyint(1) NOT NULL DEFAULT 0,
  `master_id` varchar(32) DEFAULT NULL,
  `master_name` varchar(30) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_season_individual` (`for_season`,`individual_id`),
  KEY `idx_for_season` (`for_season`)
);

CREATE TABLE `permanent_sponsors` (
  `individual_id` varchar(32) NOT NULL,
  `display_name` varchar(30) NOT NULL,
  `message` varchar(100) DEFAULT NULL,
  `amount_jpy` int(11) NOT NULL,
  `stripe_session_id` varchar(255) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`individual_id`),
  UNIQUE KEY `uniq_stripe_session` (`stripe_session_id`)
);

CREATE TABLE `predictions` (
  `user_id` char(32) NOT NULL,
  `season` int(11) NOT NULL,
  `title` varchar(8) NOT NULL,
  `pick` varchar(8) NOT NULL,
  `nickname` varchar(20) NOT NULL,
  `ip_hash` char(64) NOT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`user_id`,`season`,`title`),
  KEY `season` (`season`),
  KEY `ip_hash` (`ip_hash`,`updated_at`)
);

CREATE TABLE `retired_archive` (
  `id` varchar(64) NOT NULL,
  `display_name` varchar(32) DEFAULT NULL,
  `league` varchar(4) NOT NULL,
  `dojo` varchar(16) DEFAULT NULL,
  `params_json` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL CHECK (json_valid(`params_json`)),
  `elo_rating` float NOT NULL,
  `total_seasons` int(11) NOT NULL,
  `retired_season` int(11) NOT NULL,
  `retired_at` datetime NOT NULL DEFAULT current_timestamp(),
  `peak_elo` float NOT NULL DEFAULT 1500,
  `initial_age` int(11) DEFAULT NULL,
  `clan_root_id` varchar(64) DEFAULT NULL,
  `parent_a_id` varchar(64) DEFAULT NULL,
  `cheer_count` int(11) NOT NULL DEFAULT 0,
  `demotion_points` int(11) NOT NULL DEFAULT 0,
  `retirement_reason` varchar(64) DEFAULT NULL,
  `gender` varchar(10) DEFAULT NULL,
  PRIMARY KEY (`id`)
);

CREATE TABLE `season_state` (
  `id` int(11) NOT NULL DEFAULT 1,
  `current_season` int(11) NOT NULL DEFAULT 0,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `suzaku_league_json` text DEFAULT NULL,
  PRIMARY KEY (`id`)
);

CREATE TABLE `sponsors` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `individual_id` varchar(32) NOT NULL,
  `display_name` varchar(30) NOT NULL,
  `message` varchar(100) DEFAULT NULL,
  `amount_jpy` int(11) NOT NULL,
  `currency` varchar(8) NOT NULL DEFAULT 'jpy',
  `stripe_session_id` varchar(255) NOT NULL,
  `season` int(11) DEFAULT NULL,
  `logo_url` varchar(255) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_stripe_session` (`stripe_session_id`),
  KEY `idx_individual` (`individual_id`)
);

CREATE TABLE `standings` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `season` int(11) NOT NULL,
  `league` varchar(4) NOT NULL,
  `rank` int(11) DEFAULT NULL,
  `individual_id` varchar(64) NOT NULL,
  `display_name` varchar(32) DEFAULT NULL,
  `dojo` varchar(16) DEFAULT NULL,
  `elo` float NOT NULL,
  `movement` varchar(16) NOT NULL,
  `win` int(11) NOT NULL DEFAULT 0,
  `loss` int(11) NOT NULL DEFAULT 0,
  `draw` int(11) NOT NULL DEFAULT 0,
  `no_roundrobin` tinyint(1) NOT NULL DEFAULT 0,
  `demotion_point_event` varchar(16) DEFAULT NULL,
  `demotion_points` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_season_individual` (`season`,`individual_id`),
  KEY `idx_season_league` (`season`,`league`)
);

CREATE TABLE `supporters` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `display_name` varchar(30) NOT NULL,
  `message` varchar(100) DEFAULT NULL,
  `ip_hash` char(64) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`)
);

CREATE TABLE `suzaku_group_standings` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `season` int(11) NOT NULL,
  `league` varchar(16) NOT NULL,
  `rank` int(11) DEFAULT NULL,
  `individual_id` varchar(64) NOT NULL,
  `display_name` varchar(64) DEFAULT NULL,
  `elo` decimal(10,2) DEFAULT NULL,
  `win` int(11) NOT NULL DEFAULT 0,
  `loss` int(11) NOT NULL DEFAULT 0,
  `draw` int(11) NOT NULL DEFAULT 0,
  `no_roundrobin` tinyint(1) NOT NULL DEFAULT 0,
  `movement` varchar(32) NOT NULL DEFAULT 'stay',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_season_individual` (`season`,`individual_id`),
  KEY `idx_season_league` (`season`,`league`)
);

CREATE TABLE `title_history` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `season` int(11) NOT NULL,
  `title` varchar(8) NOT NULL,
  `event_type` varchar(16) NOT NULL,
  `holder_name` varchar(32) NOT NULL,
  `holder_id` varchar(64) DEFAULT NULL,
  `challenger_wins` int(11) DEFAULT NULL,
  `titleholder_wins` int(11) DEFAULT NULL,
  `achieved_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_season_title` (`season`,`title`)
);
