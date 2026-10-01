# -*- coding: utf-8 -*-
"""SQLite 数据层：规则版本、扫描、告警、编辑决定、豁免、署名权限。"""
import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  lead TEXT NOT NULL DEFAULT '',
  byline TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft',        -- draft | in_review | published
  version INTEGER NOT NULL DEFAULT 1,          -- 任何内容修改都会 +1
  green_version INTEGER NOT NULL DEFAULT -1,   -- 最近一次"零未决告警"的扫描对应的版本
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS paragraphs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id),
  seq INTEGER NOT NULL,                        -- 正文段落序号（导语单独存于 documents.lead）
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_sets (          -- 规则版本化保存
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version TEXT NOT NULL,
  rules_json TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id),
  doc_version INTEGER NOT NULL,
  rule_set_id INTEGER NOT NULL REFERENCES rule_sets(id),
  strategy TEXT NOT NULL,                      -- full | incremental
  nodes_total INTEGER NOT NULL DEFAULT 0,      -- 依赖节点总数
  nodes_recomputed INTEGER NOT NULL DEFAULT 0, -- 本次实际重算节点数
  detail_json TEXT NOT NULL DEFAULT '{}',      -- 增量扫描的重算节点清单等
  paragraph_snapshot_json TEXT NOT NULL DEFAULT '{}', -- 扫描时段落哈希快照（增量 diff 基准）
  duration_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id),
  doc_version INTEGER NOT NULL,
  scan_id INTEGER NOT NULL REFERENCES scans(id),
  node_key TEXT NOT NULL,                      -- 依赖节点标识，如 TIME:3 / ABBR / SIM:2:5
  rule_code TEXT NOT NULL,
  category TEXT NOT NULL,                      -- syntax | fact_hint | abbreviation | link | similarity
  severity TEXT NOT NULL,                      -- error | warning | hint
  para_seq INTEGER,                            -- NULL 表示导语/文档级
  message TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  fingerprint TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',         -- open | exempted | acked | fixed | superseded
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(doc_id, doc_version, node_key, fingerprint)   -- 幂等：并行/重复扫描不产生重复告警
);

CREATE TABLE IF NOT EXISTS decisions (          -- 人工编辑决定（审计日志，含冲突失败记录）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id INTEGER NOT NULL REFERENCES alerts(id),
  username TEXT NOT NULL,
  action TEXT NOT NULL,                        -- exempt | ack | fix
  reason TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT 'applied',      -- applied | conflicted
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exemptions (         -- 人工豁免：绑定具体段落 + 相似依据（双方快照）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id),
  rule_code TEXT NOT NULL,
  para_seq INTEGER,                            -- 被豁免段落（NULL=导语/文档级）
  para_snapshot TEXT NOT NULL DEFAULT '',      -- 豁免时段落原文快照
  para_hash TEXT NOT NULL DEFAULT '',
  counter_seq INTEGER,                         -- 相似依据：对方段落序号（语料库则为 NULL）
  counter_snapshot TEXT,
  counter_hash TEXT,
  basis_json TEXT NOT NULL DEFAULT '{}',       -- 相似依据详情（对方内容、得分、指纹）
  reason TEXT NOT NULL,
  username TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dictionary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  term TEXT NOT NULL,
  kind TEXT NOT NULL,                          -- abbreviation | fixed_statement
  expansion TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  UNIQUE(term, kind, expansion)
);

CREATE TABLE IF NOT EXISTS byline_permissions ( -- 署名权限（追加式历史，最新一条生效）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id),
  username TEXT NOT NULL,
  can_sign INTEGER NOT NULL,
  granted_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS link_mocks (         -- 离线演示用链接解析表（生产环境替换为真实 HTTP 探测）
  url TEXT PRIMARY KEY,
  status INTEGER NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id),
  doc_version INTEGER NOT NULL,
  published_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS corpus (             -- 语料库：已发布稿件段落，供跨稿相似比对
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  UNIQUE(source, content_hash)
);
"""

def db_path():
    return os.environ.get('NEWSDESK_DB', os.path.join(os.path.dirname(__file__), 'newsdesk.db'))

def connect():
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def init_db():
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _seed(conn)
        conn.commit()
    finally:
        conn.close()

def _seed(conn):
    import rules  # 延迟导入避免循环
    if not conn.execute('SELECT 1 FROM rule_sets').fetchone():
        conn.execute(
            'INSERT INTO rule_sets(version, rules_json, active) VALUES (?,?,1)',
            (rules.RULE_SET_VERSION, json.dumps(rules.describe_rules(), ensure_ascii=False)))
    abbr_seeds = [
        ('ATP', '三磷酸腺苷'), ('ATP', '国际职业网球联合会'),
        ('AI', '人工智能'), ('NBA', '美国职业篮球联赛'),
        ('GDP', '国内生产总值'), ('WHO', '世界卫生组织'),
    ]
    for term, exp in abbr_seeds:
        conn.execute(
            "INSERT OR IGNORE INTO dictionary(term, kind, expansion) VALUES (?,'abbreviation',?)",
            (term, exp))
    fixed_seeds = ['本活动最终解释权归主办方所有', '未经授权不得转载']
    for term in fixed_seeds:
        conn.execute(
            "INSERT OR IGNORE INTO dictionary(term, kind, expansion) VALUES (?,'fixed_statement','')",
            (term,))
    corpus_seeds = [
        ('历史稿件#1024', '本活动最终解释权归主办方所有。'),
        ('历史稿件#1024', '据悉，本次活动吸引了来自全国各地的数百名参与者，现场气氛热烈，秩序良好。'),
    ]
    from similarity import content_hash
    for source, content in corpus_seeds:
        conn.execute(
            'INSERT OR IGNORE INTO corpus(source, content, content_hash) VALUES (?,?,?)',
            (source, content, content_hash(content)))

def reset_for_tests(path):
    """测试用：指向全新数据库文件并初始化。"""
    os.environ['NEWSDESK_DB'] = path
    if os.path.exists(path):
        os.remove(path)
    init_db()
