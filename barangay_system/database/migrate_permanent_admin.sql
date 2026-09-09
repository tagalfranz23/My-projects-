-- Optional one-time migration of the existing permanent Administrator.
-- Prerequisites:
--   1. minante_db_schema.sql has already been imported.
--   2. The old database is named barangay_minante_db on this MariaDB server.
--
-- This copies the existing password hash; it never exposes or resets the
-- Administrator password. It deliberately copies no Staff or Resident users.

START TRANSACTION;

INSERT INTO `minante_db`.`users` (
  `username`,
  `password_hash`,
  `role`,
  `full_name`,
  `contact_number`,
  `email`,
  `address`,
  `is_active`,
  `created_at`,
  `updated_at`
)
SELECT
  `username`,
  `password_hash`,
  'admin',
  `full_name`,
  `contact_number`,
  `email`,
  `address`,
  `is_active`,
  `created_at`,
  `updated_at`
FROM `barangay_minante_db`.`users`
WHERE `role` = 'admin'
ORDER BY `id`
LIMIT 1
ON DUPLICATE KEY UPDATE
  `password_hash` = VALUES(`password_hash`),
  `role` = 'admin',
  `full_name` = VALUES(`full_name`),
  `contact_number` = VALUES(`contact_number`),
  `email` = VALUES(`email`),
  `address` = VALUES(`address`),
  `is_active` = VALUES(`is_active`),
  `updated_at` = VALUES(`updated_at`);

COMMIT;

SELECT `id`, `username`, `email`, `role`, `is_active`
FROM `minante_db`.`users`
WHERE `role` = 'admin';

