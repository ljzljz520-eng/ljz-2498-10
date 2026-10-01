# -*- coding: utf-8 -*-
"""文本相似度：字符 3-gram shingle + Jaccard。
用于相似段落候选检测与豁免存活判定（大改判定）。
"""
import hashlib
import re

def normalize(text):
    """去空白，用于指纹与 shingle。"""
    return re.sub(r'\s+', '', text or '')

def content_hash(text):
    return hashlib.sha1(normalize(text).encode('utf-8')).hexdigest()[:16]

def shingles(text, k=3):
    t = normalize(text)
    if not t:
        return set()
    if len(t) <= k:
        return {t}
    return {t[i:i + k] for i in range(len(t) - k + 1)}

def jaccard(a, b):
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a) + len(b) - inter)

def pair_fingerprint(hash_a, hash_b):
    """相似对的稳定指纹：与段落顺序无关。"""
    return ':'.join(sorted([hash_a, hash_b]))

# 豁免存活阈值：当前段落与豁免时快照的相似度低于该值，视为"大改"，旧豁免失效
EXEMPTION_SURVIVAL = 0.8
