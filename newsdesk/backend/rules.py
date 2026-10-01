# -*- coding: utf-8 -*-
"""校对规则集（版本化）。

设计要点：
- 时间检查拆成两层：TIME_FORMAT 是语法校验（格式非法=error）；
  DATE_*_FACT 是事实提示（格式合法 ≠ 事实正确，一律 hint，需人工核实）。
- 缩写按文内作用域检查：定义（全称（ABBR））自出现处起生效，直至同名不同义的重定义；
  同名缩写不同含义时，词典多义会触发人工确认提示。
- 相似段落只产出"候选"hint，绝不自动判违规。
"""
import re
from datetime import date

RULE_SET_VERSION = '1.0.0'

CATEGORY_SYNTAX = 'syntax'
CATEGORY_FACT = 'fact_hint'
CATEGORY_ABBR = 'abbreviation'
CATEGORY_LINK = 'link'
CATEGORY_SIM = 'similarity'

SEV_ERROR = 'error'
SEV_WARNING = 'warning'
SEV_HINT = 'hint'

def describe_rules():
    return [
        {'code': 'TIME_FORMAT', 'category': CATEGORY_SYNTAX, 'severity': SEV_ERROR,
         'desc': '日期语法校验：月份/日期越界等格式非法'},
        {'code': 'DATE_WEEKDAY_FACT', 'category': CATEGORY_FACT, 'severity': SEV_HINT,
         'desc': '星期标注与日期不符：格式合法，事实需人工核实'},
        {'code': 'DATE_FUTURE_FACT', 'category': CATEGORY_FACT, 'severity': SEV_HINT,
         'desc': '未来日期：格式合法，事实需人工核实'},
        {'code': 'DATE_NOYEAR_FACT', 'category': CATEGORY_FACT, 'severity': SEV_HINT,
         'desc': '缺少年份的日期：归属年份需人工核实'},
        {'code': 'ABBR_UNDEFINED', 'category': CATEGORY_ABBR, 'severity': SEV_ERROR,
         'desc': '缩写在使用前未于文内作用域解释'},
        {'code': 'ABBR_MULTI_MEANING', 'category': CATEGORY_ABBR, 'severity': SEV_WARNING,
         'desc': '同名缩写存在多个已知含义，当前作用域解释需人工确认'},
        {'code': 'ABBR_REDEFINED', 'category': CATEGORY_ABBR, 'severity': SEV_HINT,
         'desc': '缩写被重新定义为不同含义，新作用域自此生效'},
        {'code': 'LINK_DEAD', 'category': CATEGORY_LINK, 'severity': SEV_ERROR,
         'desc': '链接目标失联'},
        {'code': 'SIMILAR_PARA', 'category': CATEGORY_SIM, 'severity': SEV_HINT,
         'desc': '稿内相似段落候选（引用/固定声明可能合法，需人工裁定）'},
        {'code': 'SIMILAR_CORPUS', 'category': CATEGORY_SIM, 'severity': SEV_HINT,
         'desc': '与语料库相似的段落候选（需人工裁定）'},
    ]

# ---------------- 时间：语法校验 vs 事实提示 ----------------

WEEKDAY_MAP = {'周一': 0, '周二': 1, '周三': 2, '周四': 3, '周五': 4, '周六': 5, '周日': 6, '周天': 6}
WEEKDAY_CN = '一二三四五六日'

LOOSE_DATE_RE = re.compile(
    r'(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日(?:\s*[（(](周[一二三四五六日天])[)）])?')

def check_time(text, today=None):
    """返回告警列表。语法非法 -> error；语法合法但需人工核实事实 -> hint。"""
    today = today or date.today()
    alerts = []
    for m in LOOSE_DATE_RE.finditer(text or ''):
        y_s, mo, d, wd = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        literal = m.group(0).strip()
        if not literal:
            continue
        # --- 语法校验 ---
        try:
            if y_s:
                real = date(int(y_s), mo, d)
            else:
                real = date(2024, mo, d)  # 无年份：用闰年仅校验月/日合法性
        except ValueError:
            alerts.append({
                'rule_code': 'TIME_FORMAT', 'category': CATEGORY_SYNTAX, 'severity': SEV_ERROR,
                'message': f'日期格式非法：「{literal}」（月份或日期超出合法范围）',
                'detail': {'literal': literal}})
            continue
        # --- 语法合法，以下为事实提示（不证明事实正确，需人工核实） ---
        if y_s:
            if wd and WEEKDAY_MAP[wd] != real.weekday():
                alerts.append({
                    'rule_code': 'DATE_WEEKDAY_FACT', 'category': CATEGORY_FACT, 'severity': SEV_HINT,
                    'message': (f'「{literal}」星期标注与日期不符：{y_s}年{mo}月{d}日'
                                f'实为周{WEEKDAY_CN[real.weekday()]}。格式合法，事实日期需人工核实'),
                    'detail': {'literal': literal, 'actual_weekday': real.weekday()}})
            if real > today:
                alerts.append({
                    'rule_code': 'DATE_FUTURE_FACT', 'category': CATEGORY_FACT, 'severity': SEV_HINT,
                    'message': f'「{literal}」是未来日期。格式合法，事实准确性需人工核实',
                    'detail': {'literal': literal}})
        else:
            alerts.append({
                'rule_code': 'DATE_NOYEAR_FACT', 'category': CATEGORY_FACT, 'severity': SEV_HINT,
                'message': f'「{literal}」缺少年份。格式合法，归属年份需人工核实',
                'detail': {'literal': literal}})
    return alerts

# ---------------- 缩写：文内作用域 ----------------

ABBR_DEF_RE = re.compile(r'([一-鿿A-Za-z][一-鿿A-Za-z0-9·]{1,15})（([A-Z][A-Za-z0-9]{1,9})）')
ABBR_USE_RE = re.compile(r'(?<![A-Za-z0-9])([A-Z][A-Za-z0-9]{1,9})(?![A-Za-z0-9])')

def _refine_expansion(matched, abbr, dict_abbrs):
    """定义捕获可能带入前文汉字，优先对齐词典已知全称。"""
    for known in dict_abbrs.get(abbr, []):
        if matched.endswith(known):
            return known
    return matched

def check_abbreviations(segments, dict_abbrs):
    """segments: [(seg_label, text)] 按阅读顺序。dict_abbrs: {abbr: [expansion,...]}

    作用域语义：定义「全称（ABBR）」自出现位置起生效；同名不同义的重定义开启新作用域；
    使用点必须落在某个已定义作用域内。
    """
    events = []
    for si, (label, text) in enumerate(segments):
        for m in ABBR_DEF_RE.finditer(text or ''):
            events.append((si, m.start(), 'def', m.group(2), m.group(1), label))
        for m in ABBR_USE_RE.finditer(text or ''):
            events.append((si, m.start(), 'use', m.group(1), None, label))
    events.sort(key=lambda e: (e[0], e[1]))

    alerts = []
    active = {}            # abbr -> (expansion, label)
    multi_noted = set()    # 每个缩写每次扫描只提示一次多义确认
    for si, pos, kind, abbr, expansion, label in events:
        if kind == 'def':
            expansion = _refine_expansion(expansion, abbr, dict_abbrs)
            if abbr in active and active[abbr][0] != expansion:
                alerts.append({
                    'rule_code': 'ABBR_REDEFINED', 'category': CATEGORY_ABBR, 'severity': SEV_HINT,
                    'para': label,
                    'message': (f'缩写 {abbr} 在{label}被重新定义为「{expansion}」'
                                f'（此前含义「{active[abbr][0]}」），同名缩写不同含义，新作用域自此生效'),
                    'detail': {'abbr': abbr, 'expansion': expansion, 'previous': active[abbr][0]}})
            active[abbr] = (expansion, label)
        else:
            if abbr not in active:
                known = dict_abbrs.get(abbr, [])
                hint = f'（词典已知含义：{"/".join(known)}）' if known else ''
                alerts.append({
                    'rule_code': 'ABBR_UNDEFINED', 'category': CATEGORY_ABBR, 'severity': SEV_ERROR,
                    'para': label,
                    'message': f'缩写 {abbr} 在{label}使用前未于文内解释{hint}',
                    'detail': {'abbr': abbr, 'known_expansions': known}})
            else:
                cur_exp = active[abbr][0]
                known = dict_abbrs.get(abbr, [])
                others = [e for e in known if e != cur_exp]
                if others and abbr not in multi_noted:
                    alerts.append({
                        'rule_code': 'ABBR_MULTI_MEANING', 'category': CATEGORY_ABBR, 'severity': SEV_WARNING,
                        'para': label,
                        'message': (f'缩写 {abbr} 有多个已知含义（{"/".join(known)}），'
                                    f'当前作用域解释为「{cur_exp}」，请人工确认{label}此处含义'),
                        'detail': {'abbr': abbr, 'active': cur_exp, 'known': known}})
                    multi_noted.add(abbr)
    return alerts

# ---------------- 链接 ----------------

URL_RE = re.compile(r'https?://[^\s）"\'<>）)，。；]+')

def check_links(text, resolver):
    """resolver(url) -> (ok, status)。目标失联 -> error。"""
    alerts = []
    for m in URL_RE.finditer(text or ''):
        url = m.group(0)
        ok, status = resolver(url)
        if not ok:
            alerts.append({
                'rule_code': 'LINK_DEAD', 'category': CATEGORY_LINK, 'severity': SEV_ERROR,
                'message': f'链接目标失联（HTTP {status}）：{url}',
                'detail': {'url': url, 'status': status}})
    return alerts
