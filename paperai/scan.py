"""只定位风险，不根据领域常识自动改写论文。"""
import re
import unicodedata
from urllib.parse import unquote, urlsplit

PAGE = re.compile(r'<!-- PDF page: (\d+) -->')
DISPLAY = re.compile(r'\$\$(.*?)\$\$', re.S)
INLINE = re.compile(r'(?<!\$)\$([^$\n]*)\$(?!\$)')
TAG = re.compile(r'\\tag\{([^}]+)\}')
LINK = re.compile(r'!?\[[^\]]*\]\(([^)]+)\)')


def pages(text):
    bits = PAGE.split(text)
    numbers = bits[1::2]
    if len(numbers) != len(set(numbers)):
        raise ValueError('源页标记重复')
    return {int(bits[i]): bits[i+1] for i in range(1, len(bits), 2)}


def link_target(root, uri):
    parsed = urlsplit(uri)
    if parsed.scheme in ('https', 'http', 'mailto') or uri.startswith('#'):
        return None
    if parsed.scheme or parsed.netloc:
        raise ValueError('不支持的资源地址')
    path = (root / unquote(parsed.path)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('资源引用越出结果目录')
    return path


def scan(text, root=None):
    issues = []
    try:
        sections = pages(text)
    except ValueError:
        sections = {0: text}
        issues.append({'page': 0, 'line': 1, 'kind': 'page_duplicate', 'risk': 'critical', 'snippet': ''})
    for p, body in sections.items():
        if root and (root / f'.paperai/p{p:04}.json').is_file():
            from .io import read
            record = read(root / f'.paperai/p{p:04}.json')
            if record.get('native_characters', 0) < 30:
                issues.append({'page': p, 'line': 0, 'kind': 'low_native_text', 'risk': 'high', 'snippet': '原生文字少于30字符，须核对扫描件/空白页/整页图像'})
            boxes = [b['bbox'] for b in record.get('boxes', []) if b.get('class') == 'text']
            if any(a[2] < b[0] and min(a[3], b[3])-max(a[1], b[1]) > 20 for a in boxes for b in boxes):
                issues.append({'page': p, 'line': 0, 'kind': 'layout_columns', 'risk': 'medium', 'snippet': '存在横向分离且纵向重叠的正文块，须核对双栏语义阅读顺序'})
        outside = DISPLAY.sub(lambda m: ' ' * len(m[0]), body)
        outside = INLINE.sub(lambda m: ' ' * len(m[0]), outside)
        for offset, line in enumerate(body.splitlines(), 1):
            kinds = []
            if any(unicodedata.category(c).startswith('M') for c in line):
                kinds.append(('combining_modifier', 'high'))
            if any(unicodedata.category(c) == 'Co' for c in line):
                kinds.append(('private_math_glyph', 'high'))
            if re.search(r'</?(?:sup|sub|u)>', line):
                kinds.append(('html_math_scope', 'high'))
            if re.search(r'\\(?:bar|hat|dot|ddot)\{\}', line):
                kinds.append(('incomplete_math', 'high'))
            if any(ord(c) < 32 and c not in '\t' for c in line):
                kinds.append(('control_character', 'high'))
            if re.search(r'[A-Za-z]{28,}', line):
                kinds.append(('joined_words', 'medium'))
            if re.search(r'https?\s*:\s*//[^\n]*\s+(?:doi\.|org/|\d{4}[-./])', line):
                kinds.append(('broken_url', 'medium'))
            if line.startswith('|') and re.search(r'\d\s+\.\s*\d|\d+\\n\.', line):
                kinds.append(('table_numeric_layout', 'high'))
            if line.startswith('|') and re.search(r'\d\s*[eE]\s+[+−-]?\s*\d|10\s*\^\s*(?:\||$)', line):
                kinds.append(('table_exponent', 'high'))
            for kind, risk in kinds:
                issues.append({'page': p, 'line': offset, 'kind': kind, 'risk': risk, 'snippet': line[:240]})
        for m in re.finditer(r'\\(?:hat|bar|dot|ddot|frac)\b', outside):
            issues.append({'page': p, 'line': body.count('\n', 0, m.start())+1,
                           'kind': 'math_outside_delimiters', 'risk': 'high', 'snippet': body[m.start():m.start()+120]})
        for math in [m[1] for m in DISPLAY.finditer(body)] + [m[1] for m in INLINE.finditer(body)]:
            if re.search(r'[\^_]\s*$', math):
                issues.append({'page': p, 'line': 0, 'kind': 'incomplete_math', 'risk': 'high', 'snippet': math[:240]})
            if math.count('{') != math.count('}'):
                issues.append({'page': p, 'line': 0, 'kind': 'math_braces', 'risk': 'high', 'snippet': math[:240]})
            stack, invalid = [], False
            for action, name in re.findall(r'\\(begin|end)\{([^}]+)\}', math):
                if action == 'begin':
                    stack.append(name)
                elif not stack or stack.pop() != name:
                    invalid = True
            if stack or invalid:
                issues.append({'page': p, 'line': 0, 'kind': 'math_environment', 'risk': 'high', 'snippet': math[:240]})
        # 不推测表格正确列数，只报告同一连续表格中行宽变化。
        width = None
        for offset, line in enumerate(body.splitlines(), 1):
            if not line.strip().startswith('|'):
                width = None
                continue
            cells = re.split(r'(?<!\\)\|', line.strip().strip('|'))
            if width is not None and len(cells) != width:
                issues.append({'page': p, 'line': offset, 'kind': 'table_columns', 'risk': 'high', 'snippet': line[:240]})
            width = len(cells)
        if len(re.findall(r'(?<!\\)\$', body)) % 2:
            issues.append({'page': p, 'line': 0, 'kind': 'math_delimiter', 'risk': 'high', 'snippet': '美元符号/数学分隔符数量为奇数，需查看原页'})
        for tag in TAG.findall(body):
            if TAG.findall(text).count(tag) > 1:
                issues.append({'page': p, 'line': 0, 'kind': 'equation_duplicate', 'risk': 'critical', 'snippet': tag})
        if root:
            for uri in LINK.findall(body):
                try:
                    path = link_target(root, uri)
                    if path is not None and not path.is_file():
                        raise ValueError('资源缺失')
                except ValueError as exc:
                    issues.append({'page': p, 'line': 0, 'kind': 'resource_link', 'risk': 'critical', 'snippet': str(exc)+': '+uri})
    from .io import text_hash
    for issue in issues:
        issue['id'] = text_hash(str(issue))[:16]
        issue['evidence'] = f'assets/pages/p{issue["page"]:04}.png'
    return issues
