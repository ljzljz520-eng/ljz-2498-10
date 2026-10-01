// 数据库层：SQLite 建表与连接
import Database from 'better-sqlite3';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DB_PATH = process.env.PROOF_DB || path.join(__dirname, '..', 'data', 'proof.db');

let _db;
export function getDb() {
  if (_db) return _db;
  _db = new Database(DB_PATH);
  _db.pragma('journal_mode = WAL');
  _db.pragma('foreign_keys = ON');
  init(_db);
  return _db;
}

// 供测试使用内存库
export function createDb(raw = ':memory:') {
  const db = new Database(raw);
  db.pragma('foreign_keys = ON');
  init(db);
  return db;
}

export function resetDb() {
  if (_db) { _db.close(); _db = null; }
}

function init(db) {
  db.exec(`
  -- 稿件
  CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',   -- 导语
    body TEXT NOT NULL DEFAULT '',      -- 正文（段落以 \n\n 分隔）
    byline TEXT NOT NULL DEFAULT '',    -- 署名
    current_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'draft',  -- draft | proofread | published
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  -- 版本（每次保存一条，未校对的新版本不能继承旧版本的绿色状态）
  CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    title TEXT NOT NULL, summary TEXT NOT NULL, body TEXT NOT NULL, byline TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_by TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(document_id, version)
  );

  -- 段落节点（标题/导语/正文段落/署名），段落大改 -> 新 content_hash，旧豁免失效
  CREATE TABLE IF NOT EXISTS paragraphs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    ord INTEGER NOT NULL,            -- 段落顺序
    role TEXT NOT NULL,              -- title | summary | body | byline
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE(document_id, version, ord)
  );

  -- 规则版本（词典/相似阈值等；扫描结果绑定生效时的规则版本）
  CREATE TABLE IF NOT EXISTS rule_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL UNIQUE,
    definition TEXT NOT NULL,        -- JSON: {similarityThreshold, dictionary:[...], linkTargets:[...]}
    created_by TEXT,
    note TEXT,
    active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  -- 扫描批次：full=逐次全文扫描 / incremental=依赖节点增量扫描
  CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('full','incremental')),
    rule_version_id INTEGER NOT NULL REFERENCES rule_versions(id),
    affected_ords TEXT,              -- JSON 数组；full 为全部，incremental 为脏节点+跨界邻居
    stats TEXT,                      -- JSON: {nodes, elapsedMs, ...}
    scanned_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  -- 告警/发现
  CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    paragraph_id INTEGER REFERENCES paragraphs(id) ON DELETE SET NULL,
    ord INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN (
      'similarity','dead_link','abbr_meaning','abbr_undefined','abbr_use_before_def',
      'date_syntax','date_fact_check'
    )),
    severity TEXT NOT NULL CHECK(severity IN ('blocker','warning','info')),
    message TEXT NOT NULL,
    evidence TEXT,                   -- JSON: 相似片段/链接地址/缩写等
    -- 合并段落跨界重算时替换关系
    superseded_by INTEGER REFERENCES findings(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  -- 编辑决定（豁免必须绑定具体段落 + 具体相似依据 + 段落 hash；段落大改即失效）
  CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    paragraph_id INTEGER NOT NULL REFERENCES paragraphs(id),
    paragraph_hash TEXT NOT NULL,        -- 决定时的段落内容指纹
    basis_finding_id INTEGER REFERENCES findings(id), -- 豁免所针对的"相似依据"发现
    action TEXT NOT NULL CHECK(action IN ('fix','exempt','acknowledge')),
    reason TEXT NOT NULL,                -- 引用原话/固定声明/事实已核实 等
    decided_by TEXT NOT NULL,
    valid INTEGER NOT NULL DEFAULT 1,    -- 段落 hash 变化 / 依据消失 -> 0
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  -- 文内链接目标（目标失联告警）
  CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    paragraph_id INTEGER REFERENCES paragraphs(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    alive INTEGER NOT NULL DEFAULT 1
  );

  -- 校对软锁（两人并行校对）
  CREATE TABLE IF NOT EXISTS proof_locks (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    held_by TEXT NOT NULL,
    acquired_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
  );

  -- 署名权限（权限变化后，旧权限下的校对结论需重新核对）
  CREATE TABLE IF NOT EXISTS byline_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    byline TEXT NOT NULL UNIQUE,
    allowed INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,  -- 权限本身的版本号，变化即 +1
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  -- 审计日志
  CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    actor TEXT, action TEXT NOT NULL, detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );
  `);
}
