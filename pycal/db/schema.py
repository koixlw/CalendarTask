"""PyCal 独立 SQLite schema。

设计要点:
  - 表名不与 DesktopCal 重名 (calendar_items vs item_table), 可并存
  - 用 ISO 日期字符串 'YYYY-MM-DD' (DesktopCal 用 'YYYYMMDD')
  - 用 UTC 时间戳
  - 主条目表 + 三类子表 (repeat_rules / reminders / countdowns) 解耦
  - FTS5 镜像 calendar_items 提供快速搜索
"""

SCHEMA_SQL = """
-- 单条主条目 (格子/重复实例/提醒/倒数日 都视作 Item 的 type 区别)
CREATE TABLE IF NOT EXISTS calendar_items (
    item_id    TEXT PRIMARY KEY,
    date       TEXT,                  -- 'YYYY-MM-DD', 事件可空
    type       INTEGER NOT NULL DEFAULT 0, -- 0=格子 1=重复 2=提醒 3=倒数日 4=课程表
    title      TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    color_ref  INTEGER NOT NULL DEFAULT 0,
    is_completed INTEGER NOT NULL DEFAULT 0,
    completed_dates TEXT,             -- 重复任务的完成日期列表 (逗号分隔 ISO)
    priority  INTEGER NOT NULL DEFAULT 0,
    group_id  TEXT,                   -- 'thirdcal' = 第三方日历
    subaccount_id INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_calendar_items_date ON calendar_items(date);
CREATE INDEX IF NOT EXISTS idx_calendar_items_type ON calendar_items(type);

-- 重复规则
CREATE TABLE IF NOT EXISTS repeat_rules (
    rule_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id    TEXT NOT NULL UNIQUE REFERENCES calendar_items(item_id) ON DELETE CASCADE,
    freq       TEXT NOT NULL,                  -- 'daily' | 'weekly' | 'monthly' | 'yearly'
    interval_n INTEGER NOT NULL DEFAULT 1,
    byweekday  TEXT,                           -- 'MO,WE,FR'
    until_date TEXT                            -- 'YYYY-MM-DD' 截止
);

-- 提醒
CREATE TABLE IF NOT EXISTS reminders (
    reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL REFERENCES calendar_items(item_id) ON DELETE CASCADE,
    remind_at   TIMESTAMP NOT NULL,
    fired       INTEGER NOT NULL DEFAULT 0,
    channel     TEXT NOT NULL DEFAULT 'system' -- 'system' | 'email' | 'sound'
);

CREATE INDEX IF NOT EXISTS idx_reminders_pending
    ON reminders(remind_at) WHERE fired = 0;

-- 倒数日
CREATE TABLE IF NOT EXISTS countdowns (
    countdown_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       TEXT NOT NULL UNIQUE REFERENCES calendar_items(item_id) ON DELETE CASCADE,
    target_date   TEXT NOT NULL,
    repeat_yearly INTEGER NOT NULL DEFAULT 0
);

-- 便签
CREATE TABLE IF NOT EXISTS notes (
    note_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    color_ref  INTEGER NOT NULL DEFAULT 0,
    pinned     INTEGER NOT NULL DEFAULT 0,
    pos_x      INTEGER NOT NULL DEFAULT 100,
    pos_y      INTEGER NOT NULL DEFAULT 100,
    width      INTEGER NOT NULL DEFAULT 320,
    height     INTEGER NOT NULL DEFAULT 240,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notes_alive ON notes(deleted_at) WHERE deleted_at IS NULL;

-- FTS5 镜像 (搜索用, 对应桌面版 historysearch.Search)
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    item_id UNINDEXED, date UNINDEXED, type UNINDEXED,
    title, content,
    content='calendar_items', content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON calendar_items BEGIN
    INSERT INTO items_fts(rowid, item_id, date, type, title, content)
    VALUES (new.rowid, new.item_id, new.date, new.type, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON calendar_items BEGIN
    INSERT INTO items_fts(items_fts, rowid, item_id, date, type, title, content)
    VALUES ('delete', old.rowid, old.item_id, old.date, old.type, old.title, old.content);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON calendar_items BEGIN
    INSERT INTO items_fts(items_fts, rowid, item_id, date, type, title, content)
    VALUES ('delete', old.rowid, old.item_id, old.date, old.type, old.title, old.content);
    INSERT INTO items_fts(rowid, item_id, date, type, title, content)
    VALUES (new.rowid, new.item_id, new.date, new.type, new.title, new.content);
END;

-- 全局配置
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

INSERT OR IGNORE INTO settings(key, value) VALUES
    ('schema_version', '1'),
    ('first_run', datetime('now'));

-- 「桌面挂件」功能已经下线 —— 把它留下的配置项清掉。
-- 这段每次开库都会跑, 但第一次之后就没有行可删, 是空操作。
DELETE FROM settings WHERE key IN
    ('desktop_widget', 'widget_scale', 'widget_scheme', 'widget_topmost');
"""
