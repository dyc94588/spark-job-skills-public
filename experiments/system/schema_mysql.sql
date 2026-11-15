-- MySQL 8 建表语句（与 init_db.py 的 SQLite 模型等价）
-- 实测环境：MySQL Community Server 8.0.46 便携版，Windows 11，127.0.0.1:3306，
--           utf8mb4 / utf8mb4_unicode_ci / InnoDB；由 init_mysql_db.py 自动执行并在服务端实测通过。
-- 用法：mysql -u root -p < schema_mysql.sql
--      或 python experiments/system/init_mysql_db.py（同时导入真实结果数据）
CREATE DATABASE IF NOT EXISTS thesis_skill DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE thesis_skill;

CREATE TABLE users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  email VARCHAR(128),
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('user','admin') NOT NULL DEFAULT 'user',
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL,
  last_login DATETIME
) ENGINE=InnoDB;

CREATE TABLE op_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64), action VARCHAR(64), detail VARCHAR(255),
  ip VARCHAR(64), created_at DATETIME
) ENGINE=InnoDB;

CREATE TABLE skill_month (
  month CHAR(7) NOT NULL, skill VARCHAR(64) NOT NULL, popularity DOUBLE,
  PRIMARY KEY (month, skill), INDEX ix_month (month), INDEX ix_skill (skill)
) ENGINE=InnoDB;

CREATE TABLE skill_category (
  category VARCHAR(64) PRIMARY KEY, jobs INT, ratio DOUBLE
) ENGINE=InnoDB;

CREATE TABLE extraction (
  method VARCHAR(64) PRIMARY KEY, `precision` DOUBLE, recall DOUBLE, f1 DOUBLE,
  ms_per_doc DOUBLE, tp INT, fp INT, fn INT
) ENGINE=InnoDB;

CREATE TABLE rules (
  id INT AUTO_INCREMENT PRIMARY KEY,
  antecedent VARCHAR(64), consequent VARCHAR(64), co_jobs INT,
  support DOUBLE, conf_ab DOUBLE, conf_ba DOUBLE, lift DOUBLE,
  INDEX ix_lift (lift DESC)
) ENGINE=InnoDB;

CREATE TABLE clusters (
  cluster_id INT PRIMARY KEY, top_skills TEXT, skill_cnt INT,
  job_cnt INT, avg_salary DOUBLE
) ENGINE=InnoDB;

CREATE TABLE model_compare (
  id INT AUTO_INCREMENT PRIMARY KEY,
  model VARCHAR(64), horizon VARCHAR(16), wape DOUBLE, mape DOUBLE,
  rmse DOUBLE, samples INT
) ENGINE=InnoDB;

CREATE TABLE predictions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  h INT, origin VARCHAR(32), month CHAR(7), skill VARCHAR(64),
  y DOUBLE, naive DOUBLE, ma DOUBLE, trend DOUBLE, tree DOUBLE,
  INDEX ix_pm (month, skill)
) ENGINE=InnoDB;

CREATE TABLE trend_monthly (
  month CHAR(7) PRIMARY KEY, jobs INT, avg_jd_len DOUBLE,
  metro_ratio DOUBLE, skill_jobs INT, skill_coverage DOUBLE
) ENGINE=InnoDB;

CREATE TABLE scalability (
  rows_n BIGINT PRIMARY KEY, pandas_s DOUBLE, spark_s DOUBLE,
  pandas_mem DOUBLE, spark_mem DOUBLE, speedup DOUBLE,
  groups_pd INT, groups_sp INT
) ENGINE=InnoDB;

CREATE TABLE source_info (
  item VARCHAR(64) PRIMARY KEY, source_a TEXT, source_b TEXT
) ENGINE=InnoDB;

-- 初始管理员账号不在此写入：密码哈希由应用层 init_db.py 用
-- werkzeug.security.generate_password_hash 生成后 INSERT，避免明文口令入库。
