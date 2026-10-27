#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从微信读书笔记数据生成印象笔记 Markdown 正文。

用法:
    python build_note.py <marks.json> <reviews.json> <输出路径> [--book-id XXX]

输入:
  marks.json   —— /book/bookmarklist 的返回
  reviews.json —— /review/list/mine 的返回

输出:
  一个 .md 文件,内容为可直接写入印象笔记的正文

核心逻辑(2026-10-05 实测验证):
  1. 想法与划线用 (chapterUid, range) 配对去重,配对成功的划线不再单独输出
  2. markText 内的 \\n 是书里的段落边界,必须保留并拆行
  3. 多段落首段必须带 "- ",后续段落用两空格缩进 —— 否则 Markdown 不识别为列表项,圆点不显示
"""
import json
import sys
import collections
import argparse


def build(marks_data, reviews_data, title='', author='', book_title=None,
          book_author=None, analysis_lines=None):
    """生成印象笔记正文 Markdown。

    title/author 为推荐写法;book_title/book_author 为兼容别名。
    返回 (markdown, stat)。
    """
    title = title or book_title or ''
    author = author or book_author or ''
    marks = marks_data.get('updated', [])
    revs_raw = reviews_data.get('reviews', [])
    revs = [it.get('review', {}) for it in revs_raw]
    chap_of = {c['chapterUid']: c['title'] for c in marks_data.get('chapters', [])}
    # ⚠️ chapterIdx 不可靠:实测《趣说汉朝407年(一)》出现 171 排在 169 之前。
    # chapterUid 单调递增,用它排序才能得到书中真实顺序。
    order = {c['chapterUid']: c['chapterUid'] for c in marks_data.get('chapters', [])}

    # ---- 配对: 找出与想法绑定的划线,这些划线不再单独输出 ----
    paired = set()
    for r in revs:
        if (r.get('content') or '').strip() and (r.get('abstract') or '').strip():
            paired.add((r.get('chapterUid'), r.get('range')))

    entries = []  # (chapterUid, ts, kind, text, extra)
    for b in marks:
        if (b.get('chapterUid'), b.get('range')) in paired:
            continue
        t = (b.get('markText') or '').strip()
        if t:
            entries.append((b.get('chapterUid'), b.get('createTime', 0), 'mark', t, None))

    n_orphan = 0
    for r in revs:
        c = (r.get('content') or '').strip()
        if not c:
            continue
        a = (r.get('abstract') or '').strip()
        if a:
            entries.append((r.get('chapterUid'), r.get('createTime', 0), 'pair', a, c))
        else:
            n_orphan += 1
            entries.append((r.get('chapterUid'), r.get('createTime', 0), 'review', c, None))

    by_ch = collections.defaultdict(list)
    for ch, ts, kind, txt, extra in entries:
        by_ch[ch].append((ts, kind, txt, extra))
    chapters = sorted(by_ch.items(), key=lambda kv: (order.get(kv[0], 999), kv[0]))

    # ---- 阅读轨迹(按原始数据统计,不要用去重后的 entries,否则条数偏少) ----
    day_ch = collections.defaultdict(set)
    day_cnt = collections.Counter()
    for b in marks:
        dt = _fmt_day(b.get('createTime', 0))
        day_ch[dt].add(chap_of.get(b.get('chapterUid'), '未知'))
        day_cnt[dt] += 1
    for r in revs:
        dt = _fmt_day(r.get('createTime', 0))
        day_ch[dt].add(chap_of.get(r.get('chapterUid'), '未知'))
        day_cnt[dt] += 1

    n_mark = sum(1 for e in entries if e[2] == 'mark')
    n_pair = sum(1 for e in entries if e[2] == 'pair')
    n_rev = sum(1 for e in entries if e[2] == 'review')
    total_raw = len(marks) + len(revs)

    L = []
    L.append('## 📖 阅读分析')
    L.append('')
    L.append('**《%s》** — %s' % (title, author))
    L.append('')
    L.append('> **笔记统计** ｜ 全书 %d 条 = 划线 %d + 想法 %d（去重后正文 %d 条）'
             % (total_raw, len(marks), len(revs), len(entries)))
    L.append('>')
    days = sorted(day_cnt)
    L.append('> **阅读时间** ｜ %s，实际有笔记的 %d 天：%s'
             % (_fmt_range(days), len(days), ' / '.join(days)))
    L.append('')
    L.append('**阅读轨迹**')
    L.append('')
    for dt in days:
        names = sorted(day_ch[dt])
        L.append('- **%s**｜%d 条 · %s' % (dt, day_cnt[dt], '、'.join(names[:4]) + ('等' if len(names) > 4 else '')))
    L.append('')

    if analysis_lines:
        for line in analysis_lines:
            L.append(line)
        L.append('')

    # ---- 正文 ----
    for idx, (ch, items) in enumerate(chapters, 1):
        # 章节标题用书里的原文(如「序 回到起点,遥望汉人来时路」「1 刘邦赢了,心里愁了」),
        # 不自己加「第N 章」前缀
        L.append('## %s' % chap_of.get(ch, '未知章节'))
        L.append('')
        items.sort(key=lambda x: x[0])
        for ts, kind, txt, extra in items:
            if kind == 'mark':
                paras = _split(txt)
                if len(paras) > 1:
                    # 首段必须带 "- " 才是列表项,后续段落缩进作续行
                    L.append('- %s' % paras[0])
                    L.append('')
                    for p in paras[1:]:
                        L.append('  %s' % p)
                        L.append('')
                else:
                    L.append('- %s' % txt)
                    L.append('')
            elif kind == 'pair':
                paras = _split(txt)
                mine = extra.replace('\n', ' ').strip()
                if len(paras) > 1:
                    L.append('- %s' % paras[0])
                    L.append('')
                    for p in paras[1:-1]:
                        L.append('  %s' % p)
                        L.append('')
                    L.append('  %s（💭 **我的想法**：%s）' % (paras[-1], mine))
                    L.append('')
                else:
                    L.append('- %s（💭 **我的想法**：%s）' % (txt, mine))
                    L.append('')
            else:
                L.append('- （💭 **我的想法**：%s）' % txt.replace('\n', ' '))
                L.append('')

    return '\n'.join(L), {
        'chapters': len(chapters),
        'entries': len(entries),
        'raw': total_raw,
        'marks': len(marks),
        'reviews': len(revs),
        'paired': n_pair,
        'orphan_reviews': n_orphan,
        'multi_para_marks': sum(1 for b in marks if '\n' in (b.get('markText') or '')),
    }


def _split(text):
    t = text.replace('\r\n', '\n').replace('\r', '\n')
    return [x.strip() for x in t.split('\n') if x.strip()]


def _fmt_day(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime('%m-%d')


def _fmt_range(days):
    """把 ['09-30','10-02','10-03'] 渲染成 '09-30 → 10-03'(同一年内省略重复年份)。"""
    if not days:
        return '未知'
    a, b = days[0], days[-1]
    return a if a == b else '%s → %s' % (a, b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('marks')
    ap.add_argument('reviews')
    ap.add_argument('out')
    ap.add_argument('--title', default='')
    ap.add_argument('--author', default='')
    a = ap.parse_args()

    m = json.load(open(a.marks, encoding='utf-8'))
    d = json.load(open(a.reviews, encoding='utf-8'))
    md, stat = build(m, d, a.title, a.author)
    open(a.out, 'w', encoding='utf-8').write(md)
    print('written:', a.out)
    print(json.dumps(stat, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
