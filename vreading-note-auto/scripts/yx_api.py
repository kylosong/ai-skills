#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
印象笔记(印象笔记/yinxiang)API 封装 —— 微信读书笔记同步用。

已实测的接口与坑(2026-10-05):

1. 搜索笔记   POST .../ai-chat-note/grpc-api/search/searchNotesByFilter
   ⚠️ 不是 searchNote(返回 415)

2. 笔记详情   POST .../ai-chat-note/grpc-api/search/getNoteDetail
   ⚠️ 参数名是 guid,不是 noteGuid(用错返回 {"code":8400,"msg":"invalid guid"})
   ⚠️ 必须带 resultSpec:{"includeContent":true,...}

3. 列出笔记本 POST .../ai-chat-note/grpc-api/search/listNoteBooks

4. 列出标签   POST .../ai-chat-note/grpc-api/search/listTags

5. 创建笔记   POST .../third-party-note-service/restful/v1/createNoteFromMCP

6. 更新笔记   POST .../third-party-note-service/restful/v1/updateNoteFromMCP

⚠️ 回读返回的是纯文本(HTML 标签已剥离),不能靠标签数判断渲染结构,
   要用纯文本特征串(书名/ 第N章 / 我的想法)校验。
⚠️ API 不支持删除笔记,测试时创建的临时笔记需用户手动清理。
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error

BASE = 'https://app.yinxiang.com'
GRPC = BASE + '/third/ai-chat-note/grpc-api/search'
REST = BASE + '/third/third-party-note-service/restful/v1'


def _token():
    """读取 token:优先本地文件,回退环境变量。"""
    p = os.path.expanduser('~/.config/yinxiang-skill/token')
    if os.path.exists(p):
        t = open(p, encoding='utf-8').read().strip()
        if t:
            return t
    t = os.environ.get('YX_AUTH_TOKEN', '').strip()
    if t:
        return t
    raise RuntimeError('印象笔记未授权:请让用户说"授权印象笔记"')


def _post(url, payload):
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url, data=body,
        headers={'Content-Type': 'application/json; charset=utf-8', 'auth': _token()})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError('HTTP %s: %s' % (e.code, e.read().decode('utf-8', 'ignore')[:200]))


def list_notebooks():
    d = _post(GRPC + '/listNoteBooks', {'source': 'skill'})
    return d.get('data', {}).get('noteBookList', [])


def find_notebook(name):
    """按名称匹配笔记本,支持模糊(包含即匹配)。返回 dict 或 None。"""
    for nb in list_notebooks():
        if nb.get('name') == name:
            return nb
    for nb in list_notebooks():
        if name in (nb.get('name') or ''):
            return nb
    return None


def list_tags():
    d = _post(GRPC + '/listTags', {'source': 'skill'})
    dd = d.get('data', {})
    return dd.get('tagList', dd.get('tags', []))


def search(keyword=None, notebook_name=None, notebook_guid=None,
           title=None, tag_names=None, start_ms=None, end_ms=None, limit_note=100):
    p = {'source': 'skill',
         'resultSpec': {'includeContent': False, 'includeResources': False,
                        'includeTags': True, 'includeResourceContent': False}}
    if keyword:
        p['keyword'] = keyword
    if notebook_guid:
        p['notebookGuid'] = notebook_guid
    elif notebook_name:
        p['notebookName'] = notebook_name
    if title:
        p['title'] = title
    if tag_names:
        p['tagNames'] = tag_names
    if start_ms:
        p['startTime'] = start_ms
    if end_ms:
        p['endTime'] = end_ms
    d = _post(GRPC + '/searchNotesByFilter', p)
    dd = d.get('data', {})
    return {'total': dd.get('total', 0), 'notes': dd.get('noteDetailList', [])}


def get_note(guid):
    d = _post(GRPC + '/getNoteDetail',
              {'guid': guid, 'source': 'skill',
               'resultSpec': {'includeContent': True, 'includeResources': False,
                              'includeTags': True, 'includeResourceContent': False}})
    return d.get('data', {}).get('dataDetail', {})


def create_note(title, content, notebook_guid=None, tag_names=None):
    p = {'title': title, 'content': content, 'source': 'skill'}
    if notebook_guid:
        p['notebookGuid'] = notebook_guid
    if tag_names:
        p['tagNames'] = tag_names
    return _post(REST + '/createNoteFromMCP', p)


def update_note(guid, title=None, content=None, notebook_guid=None, tag_names=None, clear_tags=False):
    p = {'noteGuid': guid, 'source': 'skill'}
    if title is not None:
        p['title'] = title
    if content is not None:
        p['content'] = content
    if notebook_guid is not None:
        p['notebookGuid'] = notebook_guid
    if clear_tags:
        p['clearTags'] = True
    elif tag_names is not None:
        p['tagNames'] = tag_names
    return _post(REST + '/updateNoteFromMCP', p)


def normalize_content(text):
    """归一化正文用于比较。

    印象笔记回读返回的是纯文本:HTML 标签被剥离、Markdown 标记(## ** -)消失、
    单换行变空格。所以拿回读内容和本地 md 直接比字符串必然不相等。
    这里只保留「可见文字」再做比较。
    """
    if not text:
        return ''
    out = []
    skip = False
    for ch in text:
        if skip:
            #跳过被剥离的 HTML 标签
            if ch == '>':
                skip = False
            continue
        if ch == '<':
            skip = True
            continue
        if ch in '#*`>~|-=\n\r\t ':
            continue
        out.append(ch)
    return ''.join(out)


def content_signature(text):
    """提取正文结构指纹。

    比逐字符 diff 可靠:印象笔记 HTML 转换会零星吞字符(实测丢 2 处「姓名」,
    因为「姓名」被当成 name 标签),字符级比较必然抖动。
    但**结构不会丢**:章节序列、想法组数、阅读分析块都稳定。
    """
    n = normalize_content(text)
    return {
        'chapters': len(re.findall(r'第\d+章', n)),
        'titles': re.findall(r'第\d+章[^第]{0,20}', n),
        'reviews': n.count('我的想法'),
        'analysis': n.count('阅读分析'),
        'length': len(n),
        # 首尾片段:同长度替换(如改书名)不会改变任何计数,只有片段能区分
        'bullets': n.count('（💭我的想法）') + n.count('💭我的想法'),
        # 段落标记数:书里的段落边界会变成空格,统计不可靠;
        # 用「连续可见文字段」的粗略个数代替,能捕捉整段增删
        'segments': len([x for x in re.split(r'[，。；！？]', n) if len(x) > 6]),
        'head': n[:120],
        'tail': n[-120:],
    }


def contents_equal(a, b, tolerance=0.02):
    """判断两段正文是否实质相同。

    策略:**先比结构指纹,结构一致即视为相同**;结构变了再退到字符 diff 兜底。

    为什么不用纯字符 diff:
      转换吞字使 `==` 恒为 False,相似度也区分不开
      (真实丢失 0.9999 vs 加 9 字 0.99990,同一量级),
      任何字符阈值都会在「容忍丢失」与「识别真实改动」之间反复误判。

    为什么结构指纹可靠:
      转换丢的是零星字符,不会丢整章、整条划线或整个想法组。
    """
    na, nb = normalize_content(a), normalize_content(b)
    if not na or not nb:
        return na == nb
    if na == nb:
        return True
    sa, sb = content_signature(a), content_signature(b)
    if (sa['chapters'] == sb['chapters']
            and sa['titles'] == sb['titles']
            and sa['reviews'] == sb['reviews']
            and sa['analysis'] == sb['analysis']
            and sa['bullets'] == sb['bullets']
            and sa['segments'] == sb['segments']
            and sa['head'] == sb['head']
            and sa['tail'] == sb['tail']):
        if abs(sa['length'] - sb['length']) <= max(30, int(sa['length'] * tolerance)):
            return True
    return _diff_only_delete(nb, na, 40)


def _diff_only_delete(na, nb, max_lost):
    """判断 na→nb 的差异是否只是丢字。出现 insert / replace 一律 False。"""
    import difflib
    lost = 0
    # autojunk=False 必须显式关闭:长文本高频字会被当噪声丢弃,diff 不稳定。
    sm = difflib.SequenceMatcher(None, na, nb, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'delete':
            lost += i2 - i1
            if lost > max_lost:
                return False
        elif tag in ('insert', 'replace'):
            return False
    return True


def _keyword_candidates(keyword):
    """生成搜索关键词的降级序列。

    印象笔记搜索**不支持全角括号等标点**:实测关键词「汉朝407（二）」命中 0 篇,
    而「汉朝407」能命中。所以按「去标点 → 逐步截短」的顺序回退。
    """
    kw = (keyword or '').strip()
    out = [kw]
    # 去掉书名号与各类括号
    cleaned = kw
    for ch in '《》（）()〔〕【】[]':
        cleaned = cleaned.replace(ch, '')
    cleaned = cleaned.strip()
    if cleaned and cleaned not in out:
        out.append(cleaned)
    # 取前半部分(如「趣说汉朝407」→「趣说汉朝」)
    for n in (3, 2):
        if len(cleaned) > n:
            part = cleaned[:n]
            if part not in out:
                out.append(part)
    return [x for x in out if x]


def find_conflict_copies(notebook_guid=None, title=None, limit=20):
    """找出印象笔记自动生成的「冲突的修改」副本。

    印象笔记有乐观锁:若笔记在两次写入之间被客户端/服务端改动过,
    第二次写入会被另存为「冲突的修改 《书名》(时间)」而不是覆盖。
    """
    kw = title or '冲突的修改'
    r = search(keyword=kw, notebook_guid=notebook_guid)
    out = []
    for n in r['notes']:
        t = n.get('noteTitle') or ''
        # 排除目标笔记自身:否则每篇新建/更新的笔记都会被算成 1 份冲突副本
        if title and t.strip() == title.strip():
            continue
        if '冲突' in t or (title and title.strip('《》') in t):
            out.append({'guid': n.get('noteGuid'), 'title': t})
        if len(out) >= limit:
            break
    return out


def upsert_note(title, content, notebook_guid=None, tag_names=None,
                match_keyword=None, force=False, dry_run=False):
    """按标题匹配已有笔记,四态处理。

    返回 dict:
      {'status': 'created'|'updated'|'unchanged'|'conflict'|'drift',
       'guid', 'message', 'candidates'}

    ⚠️ **避免产生「冲突的修改」副本** —— 印象笔记的乐观锁:
    同一篇笔记短时间内被反复写入(尤其 force=True 连续覆盖),
    或写入期间客户端/App 也在改这篇,服务端会把新内容另存为
    「冲突的修改 《书名》(时间)」,原笔记不变,笔记列表就出现多份。

    规避要点:
      1. **默认不 force**。内容有差异时先 dry_run 报告,用户确认再写。
      2. **一次成型**:写入前把格式、分析段落确认好,别反复重写。
      3. 更新后**校验**:写入没生效就立即报告,不要重试 —— 重试只会产生更多副本。
      4. 冲突副本**不要尝试自动删除**(API 不支持),必须告知用户手动清理。
    """
    existing = []
    if match_keyword:
        for kw in _keyword_candidates(match_keyword):
            r = search(keyword=kw, notebook_guid=notebook_guid)
            for n in r['notes']:
                if (n.get('noteTitle') or '').strip() == title:
                    existing.append(n)
            if existing:
                break

    if not existing:
        if dry_run:
            return {'status': 'would_create', 'guid': None,
                    'message': '笔记本中未找到同名笔记,将新建'}
        res = create_note(title, content, notebook_guid, tag_names)
        if res.get('code') == 0:
            return _verify_written('created', res.get('noteGuid'), content, title)
        raise RuntimeError('创建失败:%s' % res.get('message'))

    if len(existing) > 1:
        cands = [{'guid': n.get('noteGuid'), 'title': n.get('noteTitle'),
                  'len': len(n.get('content') or '')} for n in existing]
        return {'status': 'conflict', 'guid': None,
                'message': '找到 %d 篇同名笔记,请指定要更新哪一篇' % len(existing),
                'candidates': cands}

    n = existing[0]
    guid = n.get('noteGuid')
    old = get_note(guid).get('content', '')

    if not force and contents_equal(old, content):
        return {'status': 'unchanged', 'guid': guid,
                'message': '笔记已存在且内容一致,未重复录入'}

    if dry_run:
        return {'status': 'would_update', 'guid': guid,
                'message': '笔记已存在但内容有差异,确认后才会更新',
                'candidates': [{'guid': guid, 'title': title,
                                'len': len(old), 'new_len': len(content)}]}

    res = update_note(guid, content=content, tag_names=tag_names)
    if res.get('code') == 0:
        return _verify_written('updated', guid, content, title)
    raise RuntimeError('更新失败:%s' % res.get('message'))


def _verify_written(status, guid, target_content, title):
    """写入后校验:内容是否真的生效。

    若未生效,多半是撞上了印象笔记的乐观锁(被存成了冲突副本)。
    此时**不要重试**,直接报告 —— 重试只会产生更多副本。
    """
    actual = get_note(guid).get('content', '')
    out = {'guid': guid, 'title': title, 'len': len(actual)}
    if contents_equal(actual, target_content):
        out['status'] = status
        out['message'] = ('已新建笔记' if status == 'created'
                          else '已更新已有笔记(%d 字)' % len(actual))
        return out
    conflicts = find_conflict_copies(title=title)
    out['status'] = 'drift'
    out['message'] = ('写入未生效,可能被存成了冲突副本(检测到 %d 份)。'
                      '印象笔记 API 不支持删除,请在客户端手动清理后再重试。'
                      % len(conflicts))
    out['conflicts'] = conflicts
    return out


# ---------- 微信读书 ----------

WR_GATEWAY = 'https://i.weread.qq.com/api/agent/gateway'


def _wr_key():
    """取 WEREAD_API_KEY。

    用户级环境变量写入后,**已启动的进程读不到**(Windows 需重启才继承新环境变量)。
    所以进程环境变量拿不到时,回退去读注册表 HKCU\\Environment,
    这样在同一个会话里也能用,不必让用户重启客户端。
    """
    k = os.environ.get('WEREAD_API_KEY', '').strip()
    if k:
        return k
    # 回退:读 Windows 用户级环境变量注册表
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                v, _ = winreg.QueryValueEx(key, 'WEREAD_API_KEY')
            if v and v.strip():
                return v.strip()
        except (FileNotFoundError, OSError):
            pass
    # 回退:本地文件
    p = os.path.expanduser('~/.config/weread-skill/token')
    if os.path.exists(p):
        t = open(p, encoding='utf-8').read().strip()
        if t:
            return t
    raise RuntimeError(
        '未找到 WEREAD_API_KEY。请设置环境变量后重试'
        '(Windows: setx WEREAD_API_KEY "wrk-xxxxxxxx"; '
        '或放到 ~/.config/weread-skill/token)')


def weread(api_name, skill_version='1.0.5', **params):
    p = {'api_name': api_name, 'skill_version': skill_version}
    p.update(params)  # ⚠️ 业务参数必须平铺在顶层,不能包在 params 里
    body = json.dumps(p, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        WR_GATEWAY, data=body,
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Bearer %s' % _wr_key()})
    r = urllib.request.urlopen(req, timeout=60)
    return json.loads(r.read().decode('utf-8'))


def find_book(book_id=None, title=None, timeout=20):
    """在书架里定位书籍,返回 (bookId, shelf_entry)。"""
    d = weread('/shelf/sync')
    books = d.get('books', [])
    for b in books:
        if book_id and b.get('bookId') == book_id:
            return b.get('bookId'), b
        if title and title in (b.get('title') or ''):
            return b.get('bookId'), b
    raise RuntimeError('书架里没找到该书')


def fetch_notes(book_id):
    """拉取单本书的划线 + 想法,自动翻页。返回 (marks_data, reviews_data)。"""
    marks = weread('/book/bookmarklist', bookId=book_id)
    reviews = {'reviews': []}
    synckey = 0
    while True:
        d = weread('/review/list/mine', bookid=book_id, count=100, synckey=synckey)
        reviews['reviews'].extend(d.get('reviews', []))
        if not d.get('hasMore'):
            break
        sk = d.get('synckey')
        if not sk or sk == synckey:
            break
        synckey = sk
    return marks, reviews
