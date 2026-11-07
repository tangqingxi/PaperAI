"""逐页缓存、原页证据、核查回执和最终验收。"""
import importlib.metadata
from pathlib import Path
import re
from urllib.parse import quote
import pymupdf
import pymupdf4llm
from .io import digest, lock, read, save, text_hash, write
from .scan import pages, scan, TAG, LINK, link_target

CHECKS = ['body', 'order', 'equations', 'inline_math', 'tables', 'figures', 'special', 'references']
VERSION = 'paperai-1'


def source_key(source):
    return digest(source)


def prepare(source, results):
    source, results = Path(source).resolve(), Path(results).resolve()
    key = source_key(source)
    stem = re.sub(r'[^\w.-]+', '_', source.stem).strip('._')[:80] or 'paper'
    folder = results / (stem + '-' + key[:12])
    folder.mkdir(parents=True, exist_ok=True)
    with lock(folder / '.paperai/lock'):
        return _prepare(source, folder, key)


def _prepare(source, folder, key):
    cache = folder / '.paperai'
    state_file = cache / 'state.json'
    versions = {k: importlib.metadata.version(k) for k in ['pymupdf', 'pymupdf4llm']}
    signature = {'source_sha256': key, 'pipeline': VERSION, 'versions': versions, 'dpi': 160}
    if state_file.exists():
        state = read(state_file)
        if state['signature'] != signature:
            raise ValueError('依赖或缓存签名变化：请使用新的 results 根目录，保留现有成果')
        state['source'] = str(source)
    else:
        if (folder / 'paper.md').exists():
            raise ValueError('结果目录含未关联的 paper.md，拒绝覆盖')
        state = {'signature': signature, 'source': str(source), 'pages': [], 'reviews': {}}
    with pymupdf.open(source) as doc:
        if doc.needs_pass:
            raise ValueError('PDF已加密，请提供可读取的本地版本')
        state['page_count'] = len(doc)
        for number, page in enumerate(doc, 1):
            raw = cache / f'p{number:04}.md'
            record_file = cache / f'p{number:04}.json'
            if record_file.exists():
                record = read(record_file)
                if not raw.exists() or digest(raw) != record['raw_sha256']:
                    raise ValueError(f'第{number}页缓存被更改，拒绝静默重建')
                if all((folder / x['path']).exists() and digest(folder / x['path']) == x['sha256'] for x in record['assets']):
                    continue
                raise ValueError(f'第{number}页资源缺失或变化，请恢复该页资源，不得覆盖已核查内容')
            chunk = pymupdf4llm.to_markdown(doc, pages=[number-1], page_chunks=True,
                                          write_images=False, embed_images=False, use_ocr=False, show_progress=False)[0]
            body = chunk['text']
            assets = []
            def render(rect, name):
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(page.get_pixmap(clip=rect, dpi=160).tobytes('png'))
                assets.append({'path': name, 'sha256': digest(target)})
                return quote(name, safe='/')
            render(page.rect, f'assets/pages/p{number:04}.png')
            regions = []
            for i, box in enumerate(chunk.get('page_boxes', [])):
                if box.get('class') in ('picture', 'formula'):
                    rect = pymupdf.Rect(box['bbox']) & page.rect
                    if not rect.is_empty:
                        name = render(rect, f'assets/p{number:04}-region-{i:03}.png')
                        regions.append(f'![原页区域 {number}-{i}（需核对图号/公式）]({name})')
            table_records = []
            for i, table in enumerate(page.find_tables().tables, 1):
                name = render(pymupdf.Rect(table.bbox), f'assets/p{number:04}-table-{i}.png')
                table_records.append({'bbox': list(table.bbox), 'cells': table.extract(), 'image': name})
            write(raw, f'<!-- PDF page: {number} -->\n\n'+body+'\n\n'+'\n\n'.join(regions))
            record = {'page': number, 'raw_sha256': digest(raw), 'assets': assets,
                      'tables': table_records, 'boxes': chunk.get('page_boxes', []), 'native_characters': len(page.get_text())}
            save(record_file, record)
        if digest(source) != key:
            raise ValueError('处理期间源PDF改变，结果未发布')
        state['pages'] = [read(cache / f'p{n:04}.json') for n in range(1, len(doc)+1)]
    if not (folder / 'paper.md').exists():
        write(folder / 'paper.md', '\n\n'.join((cache/f'p{n:04}.md').read_text(encoding='utf-8') for n in range(1,state['page_count']+1)))
    save(state_file, state)
    evaluate(folder, publish=False)
    return folder


def snapshot(folder, state, page, body):
    record = state['pages'][page-1]
    if digest(folder/f'.paperai/p{page:04}.md') != record['raw_sha256']:
        raise ValueError('原始提取缓存被更改')
    assets = {x['path']: digest(folder/x['path']) for x in record['assets']}
    if any(assets[x['path']] != x['sha256'] for x in record['assets']):
        raise ValueError('源页证据被更改')
    for uri in LINK.findall(body):
        target = link_target(folder, uri)
        if target is not None:
            assets[str(target.relative_to(folder))] = digest(target)
    return {'body': text_hash(body), 'assets': assets}


def review(folder, page, receipt):
    folder = Path(folder).resolve()
    with lock(folder / '.paperai/lock'):
        state = read(folder / '.paperai/state.json')
        if digest(state['source']) != state['signature']['source_sha256']:
            raise ValueError('源PDF与核查基线不一致')
        sections = pages((folder/'paper.md').read_text(encoding='utf-8'))
        if page not in sections or not 1 <= page <= state['page_count']:
            raise ValueError('无效的原始页码')
        if set(receipt.get('checks', {})) != set(CHECKS):
            raise ValueError('回执必须逐项覆盖全部核查维度')
        if any(x not in ('VERIFIED','REPAIRED','UNVERIFIED','FAILED') for x in receipt['checks'].values()):
            raise ValueError('不支持的核查状态')
        if not receipt.get('visual') or not receipt.get('notes', '').strip():
            raise ValueError('必须实际查看原页并填写具体依据，程序不能代替视觉核查')
        for field in ['equations','figures','references','tables','special']:
            if not isinstance(receipt.get(field), list):
                raise ValueError('回执缺少原页清单：'+field)
        decisions = receipt.get('decisions', {})
        for issue in scan(f'<!-- PDF page: {page} -->'+sections[page], folder):
            decision = decisions.get(issue['id'], {})
            if decision.get('status') not in ('FALSE_POSITIVE','SOURCE_ISSUE','UNVERIFIED','FAILED') or not decision.get('reason','').strip():
                raise ValueError('每个尚存扫描候选必须填写有依据的处置：'+issue['id'])
        receipt['snapshot'] = snapshot(folder, state, page, sections[page])
        state['reviews'][str(page)] = receipt
        save(folder / '.paperai/state.json', state)
        return evaluate(folder, publish=False)


def evaluate(folder, publish=False):
    folder = Path(folder).resolve()
    state_file = folder / '.paperai/state.json'
    state = read(state_file)
    paper = folder / 'paper.md'
    text = paper.read_text(encoding='utf-8')
    problems = scan(text, folder)
    reasons = []
    try:
        sections = pages(text)
    except ValueError as exc:
        sections = {}
        reasons.append(str(exc))
    if list(sections) != list(range(1,state['page_count']+1)):
        reasons.append('源页标记不连续或覆盖不完整')
    if not Path(state['source']).is_file() or digest(state['source']) != state['signature']['source_sha256']:
        reasons.append('源文件哈希失配')
    valid, unknown, inventory = [], [], {'equations': [], 'figures': [], 'references': []}
    for p, body in sections.items():
        receipt = state['reviews'].get(str(p))
        try:
            current = snapshot(folder, state, p, body)
        except (OSError, IndexError, ValueError):
            reasons.append(f'第{p}页证据资源失效')
            continue
        if not receipt or receipt.get('snapshot') != current:
            reasons.append(f'第{p}页未核查或核查后发生变化')
            continue
        valid.append(p)
        for name, status in receipt['checks'].items():
            if status == 'FAILED':reasons.append(f'第{p}页{name}失败')
            if status == 'UNVERIFIED':unknown.append(f'第{p}页{name}未核实')
        actual = TAG.findall(body)
        if actual != receipt['equations']:
            reasons.append(f'第{p}页公式数量、顺序或原位清单不一致')
        for name in inventory:inventory[name].extend(receipt[name])
        for label in receipt['figures']+receipt['tables']+receipt['special']:
            plain = body.replace('*','').replace('_','')
            if label not in plain:
                reasons.append(f'第{p}页缺少核查清单结构：{label}')
        for issue in [x for x in problems if x['page']==p]:
            decision = receipt.get('decisions', {}).get(issue['id'], {})
            if decision.get('status') == 'UNVERIFIED':unknown.append(issue['id'])
            elif decision.get('status') not in ('SOURCE_ISSUE','FALSE_POSITIVE'):
                reasons.append('未处置候选 '+issue['id'])
    for name, entries in inventory.items():
        if len(entries) != len(set(entries)):
            reasons.append(name+'的原页清单存在重复标识')
    actual_refs = re.findall(r'^[>\s*-]*\[(\d+)\]',text,re.M)
    if actual_refs != inventory['references']:
        reasons.append('参考文献编号与逐页原文清单不一致')
    # 程序硬性完整性错误不能通过人工误报处置绕过。
    for issue in problems:
        if issue['kind'] in ('resource_link','equation_duplicate','page_duplicate'):
            reasons.append(issue['kind']+': '+issue['snippet'])
    status = 'FAIL' if reasons else 'CONDITIONAL' if unknown else 'PASS'
    rows = '\n'.join(f'|{p}|'+('已核查' if p in valid else '未完成/已失效')+'|' for p in range(1,state['page_count']+1))
    notes = '\n\n'.join(f'### 第{p}页\n\n'+state['reviews'][str(p)]['notes'] for p in valid)
    decisions = '\n'.join(f'- p{p} {k}: {d["status"]}；{d["reason"]}' for p in valid for k,d in state['reviews'][str(p)].get('decisions',{}).items())
    report = (f'# PaperAI 核查报告\n\n状态：**{status}**\n\n源文件 SHA-256：`{state["signature"]["source_sha256"]}`\n\n'
              f'paper.md 实际字节 SHA-256：`{digest(paper)}`\n\n原页 {state["page_count"]}，有效回执 {len(valid)}。'
              '\n\n程序不能验证Agent是否确实看过原图；回执是执行Agent的明确声明。\n\n|原页|核查状态|\n|---|---|\n'+rows+
              '\n\n## 未完成项目\n\n'+'\n'.join('- '+x for x in reasons+unknown)+
              '\n\n## 原页核查依据\n\n'+notes+'\n\n## 候选处置与原文疑点\n\n'+decisions+'\n')
    write(folder/'QA_REPORT.md', report)
    save(folder/'.paperai/issues.json', problems)
    if publish:
        state['final'] = {'status': status, 'paper_sha256': digest(paper)}
        save(state_file, state)
    return {'status': status, 'pages': state['page_count'], 'reviewed': len(valid), 'issues': len(problems), 'reasons': reasons+unknown}


def finalize(folder):
    folder = Path(folder).resolve()
    with lock(folder/'.paperai/lock'):
        return evaluate(folder, publish=True)


def crop(folder, page, bbox):
    folder = Path(folder).resolve()
    with lock(folder/'.paperai/lock'):
        state = read(folder/'.paperai/state.json')
        if digest(state['source']) != state['signature']['source_sha256']:
            raise ValueError('源文件发生变化')
        with pymupdf.open(state['source']) as doc:
            rect = pymupdf.Rect(bbox)
            if not 1<=page<=len(doc) or rect.is_empty or not doc[page-1].rect.contains(rect):
                raise ValueError('非法裁剪范围')
            relative = f'assets/evidence-p{page:04}-'+text_hash(str(bbox))[:10]+'.png'
            doc[page-1].get_pixmap(clip=rect, dpi=260).save(folder/relative)
        record = state['pages'][page-1]
        record['assets'] = [x for x in record['assets'] if x['path']!=relative]+[{'path':relative,'sha256':digest(folder/relative)}]
        save(folder/'.paperai/state.json',state)
        save(folder/f'.paperai/p{page:04}.json',record)
        return relative
