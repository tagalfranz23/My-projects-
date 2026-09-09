-- Barangay Minante 1 Integrated Management System
-- Complete MariaDB schema for a new database named `minante_db`.
--
-- Import from a command prompt:
--   mariadb -u root -p < database/minante_db_schema.sql
--
-- This script intentionally creates no Administrator, Staff, or Resident users.
-- Use seed.py with INITIAL_ADMIN_* environment variables, or run the separate
-- migrate_permanent_admin.sql script to preserve the existing Administrator.

CREATE DATABASE IF NOT EXISTS `minante_db`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `minante_db`;

SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE IF NOT EXISTS `users` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(80) NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `role` VARCHAR(20) NOT NULL DEFAULT 'resident',
  `full_name` VARCHAR(150) NOT NULL,
  `contact_number` VARCHAR(20) NULL,
  `email` VARCHAR(150) NULL,
  `address` VARCHAR(255) NULL,
  `is_active` TINYINT(1) NOT NULL DEFAULT 1,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_username` (`username`),
  UNIQUE KEY `uq_users_email` (`email`),
  KEY `idx_users_role` (`role`),
  KEY `idx_users_active_role` (`is_active`, `role`),
  CONSTRAINT `chk_users_role`
    CHECK (`role` IN ('resident', 'staff', 'admin')),
  CONSTRAINT `chk_users_is_active`
    CHECK (`is_active` IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `permit_types` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(120) NOT NULL,
  `description` TEXT NULL,
  `requirements` TEXT NULL,
  `fee` DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  `validity_days` INT NOT NULL DEFAULT 365,
  `is_active` TINYINT(1) NOT NULL DEFAULT 1,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_permit_types_name` (`name`),
  KEY `idx_permit_types_active` (`is_active`),
  CONSTRAINT `chk_permit_types_fee` CHECK (`fee` >= 0),
  CONSTRAINT `chk_permit_types_validity` CHECK (`validity_days` > 0),
  CONSTRAINT `chk_permit_types_active` CHECK (`is_active` IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `event_categories` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(120) NOT NULL,
  `is_active` TINYINT(1) NOT NULL DEFAULT 1,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_event_categories_name` (`name`),
  KEY `idx_event_categories_active` (`is_active`),
  CONSTRAINT `chk_event_categories_active` CHECK (`is_active` IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `system_settings` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `key` VARCHAR(100) NOT NULL,
  `value` TEXT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_system_settings_key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `permit_applications` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `reference_no` VARCHAR(40) NOT NULL,
  `applicant_id` INT UNSIGNED NOT NULL,
  `permit_type_id` INT UNSIGNED NOT NULL,
  `purpose` TEXT NOT NULL,
  `status` VARCHAR(30) NOT NULL DEFAULT 'Pending',
  `application_date` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `appointment_date` DATETIME NULL,
  `decision_date` DATETIME NULL,
  `decided_by` INT UNSIGNED NULL,
  `reviewed_by` INT UNSIGNED NULL,
  `remarks` TEXT NULL,
  `permit_file_path` VARCHAR(255) NULL,
  `attachment_path` VARCHAR(255) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_permit_applications_reference` (`reference_no`),
  KEY `idx_permit_applications_applicant` (`applicant_id`),
  KEY `idx_permit_applications_permit_type` (`permit_type_id`),
  KEY `idx_permit_applications_status` (`status`),
  KEY `idx_permit_applications_application_date` (`application_date`),
  KEY `idx_permit_applications_decided_by` (`decided_by`),
  KEY `idx_permit_applications_reviewed_by` (`reviewed_by`),
  CONSTRAINT `fk_permits_applicant`
    FOREIGN KEY (`applicant_id`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT `fk_permits_type`
    FOREIGN KEY (`permit_type_id`) REFERENCES `permit_types` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT `fk_permits_decided_by`
    FOREIGN KEY (`decided_by`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT `fk_permits_reviewed_by`
    FOREIGN KEY (`reviewed_by`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `event_requests` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `reference_no` VARCHAR(40) NOT NULL,
  `requester_id` INT UNSIGNED NOT NULL,
  `event_name` VARCHAR(180) NOT NULL,
  `category_id` INT UNSIGNED NULL,
  `event_type` VARCHAR(120) NOT NULL,
  `description` TEXT NOT NULL,
  `proposed_date` DATE NOT NULL,
  `proposed_start_time` TIME NULL,
  `proposed_end_time` TIME NULL,
  `proposed_location` VARCHAR(255) NOT NULL,
  `expected_attendees` INT NOT NULL,
  `supporting_document_path` VARCHAR(255) NULL,
  `status` VARCHAR(30) NOT NULL DEFAULT 'Pending',
  `reviewed_by` INT UNSIGNED NULL,
  `decision_date` DATETIME NULL,
  `remarks` TEXT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_event_requests_reference` (`reference_no`),
  KEY `idx_event_requests_requester` (`requester_id`),
  KEY `idx_event_requests_category` (`category_id`),
  KEY `idx_event_requests_status` (`status`),
  KEY `idx_event_requests_proposed_date` (`proposed_date`),
  KEY `idx_event_requests_reviewed_by` (`reviewed_by`),
  CONSTRAINT `chk_event_requests_attendees` CHECK (`expected_attendees` > 0),
  CONSTRAINT `chk_event_requests_time_order`
    CHECK (`proposed_start_time` IS NULL OR `proposed_end_time` IS NULL OR `proposed_end_time` > `proposed_start_time`),
  CONSTRAINT `fk_events_requester`
    FOREIGN KEY (`requester_id`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT `fk_events_category`
    FOREIGN KEY (`category_id`) REFERENCES `event_categories` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT `fk_events_reviewed_by`
    FOREIGN KEY (`reviewed_by`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `blotter_cases` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `case_no` VARCHAR(40) NOT NULL,
  `complainant_id` INT UNSIGNED NOT NULL,
  `respondent_name` VARCHAR(150) NOT NULL,
  `respondent_address` VARCHAR(255) NULL,
  `incident_type` VARCHAR(120) NOT NULL,
  `incident_date` DATETIME NOT NULL,
  `incident_location` VARCHAR(255) NOT NULL,
  `narrative` TEXT NOT NULL,
  `supporting_document_path` VARCHAR(255) NULL,
  `status` VARCHAR(30) NOT NULL DEFAULT 'Filed',
  `filed_date` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `assigned_officer` VARCHAR(150) NULL,
  `resolution` TEXT NULL,
  `hearing_date` DATETIME NULL,
  `updated_by` INT UNSIGNED NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_blotter_cases_case_no` (`case_no`),
  KEY `idx_blotter_cases_complainant` (`complainant_id`),
  KEY `idx_blotter_cases_status` (`status`),
  KEY `idx_blotter_cases_incident_date` (`incident_date`),
  KEY `idx_blotter_cases_filed_date` (`filed_date`),
  KEY `idx_blotter_cases_updated_by` (`updated_by`),
  CONSTRAINT `fk_blotter_complainant`
    FOREIGN KEY (`complainant_id`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT `fk_blotter_updated_by`
    FOREIGN KEY (`updated_by`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `schedules` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `reference_no` VARCHAR(40) NOT NULL,
  `related_type` VARCHAR(20) NOT NULL,
  `related_id` INT UNSIGNED NOT NULL,
  `requested_by` INT UNSIGNED NOT NULL,
  `scheduled_datetime` DATETIME NOT NULL,
  `purpose` VARCHAR(255) NOT NULL,
  `note` TEXT NULL,
  `status` VARCHAR(30) NOT NULL DEFAULT 'Requested',
  `confirmed_by` INT UNSIGNED NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_schedules_reference` (`reference_no`),
  KEY `idx_schedules_related` (`related_type`, `related_id`),
  KEY `idx_schedules_requested_by` (`requested_by`),
  KEY `idx_schedules_datetime` (`scheduled_datetime`),
  KEY `idx_schedules_status` (`status`),
  KEY `idx_schedules_confirmed_by` (`confirmed_by`),
  CONSTRAINT `chk_schedules_related_type`
    CHECK (`related_type` IN ('permit', 'event', 'blotter')),
  CONSTRAINT `fk_schedules_requested_by`
    FOREIGN KEY (`requested_by`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT `fk_schedules_confirmed_by`
    FOREIGN KEY (`confirmed_by`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `notifications` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` INT UNSIGNED NULL,
  `recipient_user_id` INT UNSIGNED NULL,
  `recipient_contact` VARCHAR(150) NULL,
  `title` VARCHAR(180) NOT NULL DEFAULT 'System notification',
  `message` TEXT NOT NULL,
  `channel` VARCHAR(20) NOT NULL DEFAULT 'in_app',
  `status` VARCHAR(20) NOT NULL DEFAULT 'sent',
  `related_type` VARCHAR(30) NULL,
  `related_id` INT UNSIGNED NULL,
  `sent_at` DATETIME NULL,
  `read_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_notifications_user` (`user_id`),
  KEY `idx_notifications_recipient_user` (`recipient_user_id`),
  KEY `idx_notifications_channel` (`channel`),
  KEY `idx_notifications_status` (`status`),
  KEY `idx_notifications_unread` (`user_id`, `channel`, `read_at`),
  KEY `idx_notifications_related` (`related_type`, `related_id`),
  KEY `idx_notifications_created_at` (`created_at`),
  CONSTRAINT `chk_notifications_channel`
    CHECK (`channel` IN ('in_app', 'email', 'sms')),
  CONSTRAINT `fk_notifications_user`
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT `fk_notifications_recipient_user`
    FOREIGN KEY (`recipient_user_id`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `activity_logs` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` INT UNSIGNED NULL,
  `actor_role` VARCHAR(20) NULL,
  `action` VARCHAR(120) NOT NULL,
  `resource` VARCHAR(50) NOT NULL DEFAULT 'system',
  `target_reference` VARCHAR(80) NULL,
  `details` TEXT NULL,
  `ip_address` VARCHAR(50) NULL,
  `user_agent` VARCHAR(255) NULL,
  `timestamp` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_activity_logs_user` (`user_id`),
  KEY `idx_activity_logs_action` (`action`),
  KEY `idx_activity_logs_resource` (`resource`),
  KEY `idx_activity_logs_target` (`target_reference`),
  KEY `idx_activity_logs_timestamp` (`timestamp`),
  CONSTRAINT `fk_activity_logs_user`
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `reports` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `generated_by` INT UNSIGNED NOT NULL,
  `report_type` VARCHAR(50) NOT NULL,
  `format` VARCHAR(10) NOT NULL,
  `filters_json` TEXT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_reports_generated_by` (`generated_by`),
  KEY `idx_reports_type_created` (`report_type`, `created_at`),
  CONSTRAINT `chk_reports_format` CHECK (`format` IN ('csv', 'pdf')),
  CONSTRAINT `fk_reports_generated_by`
    FOREIGN KEY (`generated_by`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `password_reset_tokens` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` INT UNSIGNED NOT NULL,
  `token_hash` VARCHAR(64) NOT NULL,
  `expires_at` DATETIME NOT NULL,
  `used_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_password_reset_tokens_hash` (`token_hash`),
  KEY `idx_password_reset_tokens_user` (`user_id`),
  KEY `idx_password_reset_tokens_expiry` (`expires_at`),
  CONSTRAINT `fk_password_reset_tokens_user`
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `announcements` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `title` VARCHAR(180) NOT NULL,
  `body` TEXT NOT NULL,
  `is_published` TINYINT(1) NOT NULL DEFAULT 1,
  `published_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expires_at` DATETIME NULL,
  `created_by` INT UNSIGNED NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_announcements_published` (`is_published`, `published_at`),
  KEY `idx_announcements_expires_at` (`expires_at`),
  KEY `idx_announcements_created_by` (`created_by`),
  CONSTRAINT `chk_announcements_published` CHECK (`is_published` IN (0, 1)),
  CONSTRAINT `fk_announcements_created_by`
    FOREIGN KEY (`created_by`) REFERENCES `users` (`id`)
    ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;

-- Required non-user configuration data. These are catalogs and settings, not
-- fake dashboard/demo records. Existing values are retained on repeat imports.
INSERT IGNORE INTO `permit_types`
  (`name`, `description`, `requirements`, `fee`, `validity_days`, `is_active`)
VALUES
  ('Barangay Clearance', NULL, NULL, 0.00, 365, 1),
  ('Business Clearance', NULL, NULL, 0.00, 365, 1),
  ('Certificate of Residency', NULL, NULL, 0.00, 365, 1);

INSERT IGNORE INTO `event_categories` (`name`, `is_active`)
VALUES
  ('Community Event', 1),
  ('Sports Activity', 1),
  ('Public Assembly', 1);

INSERT IGNORE INTO `system_settings` (`key`, `value`)
VALUES
  ('receipt_processing_permit', '1-3 business days'),
  ('receipt_processing_event', '3-5 business days'),
  ('receipt_processing_blotter', '1-3 business days');

-- Optional least-privilege application account (run after replacing the
-- placeholder with a strong password; never commit the real password):
-- CREATE USER IF NOT EXISTS 'barangay_app'@'localhost'
--   IDENTIFIED BY 'REPLACE_WITH_A_STRONG_PASSWORD';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON `minante_db`.*
--   TO 'barangay_app'@'localhost';
-- FLUSH PRIVILEGES;

