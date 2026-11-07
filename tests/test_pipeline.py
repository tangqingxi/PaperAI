"""合成PDF、恢复机制及核查门禁的行为测试，不冒充Agent视觉验收。"""
import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pymupdf
from paperai.core import prepare, review, finalize, crop, CHECKS
from paperai.io import digest, read, write
from paperai.scan import scan, pages


class ScannerTests(unittest.TestCase):
    def test_real_meng_failures_and_valid_latex(self):
        broken='<!-- PDF page: 5 -->\n+ _̄_ $\\mathcal Q$\n<sup>forall1</sup>\n\ue257\n$\\hat{}x$\n$\\frac{a}{b$\n|0  .01|\x13|'
        kinds={x['kind'] for x in scan(broken)}
        self.assertTrue({'combining_modifier','html_math_scope','private_math_glyph','incomplete_math','math_braces','control_character','table_numeric_layout'}<=kinds)
        self.assertEqual(scan('<!-- PDF page: 5 -->\n$\\bar{\\mathcal Q}$\n$$\\dot{x}=\\frac{a}{b}\\tag{1}$$'),[])

    def test_duplicate_and_escape(self):
        s='<!-- PDF page: 1 -->\n$$x\\tag{1}$$\n$$y\\tag{1}$$\n![x](../secret)'
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual({x['kind'] for x in scan(s,Path(root))},{'equation_duplicate','resource_link'})

    def test_table_and_matrix_risks(self):
        s='<!-- PDF page: 1 -->\n|a|b|\n|1|\n|1 e -3|2|\n$$\\begin{matrix}1&2\\end{cases}$$\n$unfinished'
        self.assertTrue({'table_columns','table_exponent','math_environment','math_delimiter'} <= {x['kind'] for x in scan(s)})
        self.assertEqual(scan('<!-- PDF page: 1 -->\n$$\\begin{matrix}1\\end{matrix}+\\begin{cases}x\\end{cases}$$'), [])
        self.assertEqual(scan('<!-- PDF page: 1 -->\n# _A. Results_\n'), [])
        self.assertIn('incomplete_math', {x['kind'] for x in scan('<!-- PDF page: 1 -->\n$x_$')})


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.pdf=self.root/'中文 路径.pdf'
        doc=pymupdf.open()
        for n in range(2):
            p=doc.new_page();p.insert_text((40,45),f'Page {n+1} scientific example')
            p.insert_textbox((40,90,280,180),'Left column: x = a/b. Matrix [1 0; 0 1]. Piecewise x if t > 0.')
            p.insert_textbox((315,90,550,180),'Right column: theorem, proof and references. Continue equation on next page.')
            p.draw_rect((40,220,250,290));p.insert_text((50,245),'Figure 1. Synthetic control diagram')
            for y in [320,345,370]:p.draw_line((40,y),(260,y))
            for x in [40,150,260]:p.draw_line((x,320),(x,370))
            p.insert_text((50,337),'Parameter');p.insert_text((160,337),'Value')
            p.insert_text((50,362),'Gain');p.insert_text((160,362),'0.01')
        doc.save(self.pdf);doc.close()
        self.hash=digest(self.pdf)

    def test_prepare_cache_review_edit_and_evidence(self):
        folder=prepare(self.pdf,self.root/'out')
        self.assertEqual(finalize(folder)['status'],'FAIL')
        original=(folder/'paper.md').read_bytes()
        record=folder/'.paperai/p0001.md';stamp=record.stat().st_mtime_ns
        prepare(self.pdf,self.root/'out')
        self.assertEqual((folder/'paper.md').read_bytes(),original)
        self.assertEqual(record.stat().st_mtime_ns,stamp)
        # 这里是门禁测试的已知合成答案，不是对真实论文的视觉核查声明。
        write(folder/'paper.md','<!-- PDF page: 1 -->\n$$x=a/b\\tag{1}$$\n\n<!-- PDF page: 2 -->\n$$y=x\\tag{2}$$\n')
        for p in [1,2]:
            receipt={'visual':True,'notes':'合成夹具，测试回执绑定及状态迁移。','checks':dict.fromkeys(CHECKS,'VERIFIED'),
                     'equations':[str(p)],'figures':[],'references':[],'tables':[],'special':[],'decisions':{}}
            candidates=scan(f'<!-- PDF page: {p} -->'+pages((folder/'paper.md').read_text(encoding='utf-8'))[p],folder)
            receipt['decisions']={x['id']:{'status':'FALSE_POSITIVE','reason':'已知双栏合成夹具，测试中的正文顺序由固定答案明确给出。'} for x in candidates if x['kind']=='layout_columns'}
            review(folder,p,receipt)
        self.assertEqual(finalize(folder)['status'],'PASS')
        image=folder/'assets/pages/p0001.png';backup=image.read_bytes();image.write_bytes(b'corrupt')
        self.assertEqual(finalize(folder)['status'],'FAIL');image.write_bytes(backup)
        text=(folder/'paper.md').read_text(encoding='utf-8').replace('x=a/b','x=a/c')
        write(folder/'paper.md',text)
        result=finalize(folder);self.assertEqual(result['reviewed'],1)
        prepare(self.pdf,self.root/'out');self.assertEqual((folder/'paper.md').read_text(encoding='utf-8'),text)
        self.assertEqual(digest(self.pdf),self.hash)

    def test_interruption_resumes_only_remaining_pages(self):
        import paperai.core as core
        real=core.pymupdf4llm.to_markdown
        def fail_second(doc,**kwargs):
            if kwargs['pages']==[1]:raise RuntimeError('模拟中断')
            return real(doc,**kwargs)
        with patch.object(core.pymupdf4llm,'to_markdown',side_effect=fail_second):
            with self.assertRaises(RuntimeError):prepare(self.pdf,self.root/'out')
        with patch.object(core.pymupdf4llm,'to_markdown',wraps=real) as call:
            folder=prepare(self.pdf,self.root/'out');self.assertEqual(call.call_count,1)
        crop(folder,1,[40,220,250,290]);prepare(self.pdf,self.root/'out')
        self.assertTrue(any('evidence-' in a['path'] for a in read(folder/'.paperai/state.json')['pages'][0]['assets']))

    def test_source_changed_creates_new_bundle(self):
        old=prepare(self.pdf,self.root/'out')
        with pymupdf.open(self.pdf) as d:
            d[0].insert_text((50,600),'New source');d.save(self.root/'changed.pdf')
        self.pdf.write_bytes((self.root/'changed.pdf').read_bytes())
        new=prepare(self.pdf,self.root/'out')
        self.assertNotEqual(old,new);self.assertTrue((old/'paper.md').exists())

    def test_new_assets_and_raw_cache_invalidate_review(self):
        folder=prepare(self.pdf,self.root/'out')
        extra=folder/'assets/agent-note.png';extra.write_bytes(b'known-fixture')
        write(folder/'paper.md','<!-- PDF page: 1 -->\n![fixture](assets/agent-note.png)\n\n<!-- PDF page: 2 -->\nNo equations.\n')
        for p,b in pages((folder/'paper.md').read_text(encoding='utf-8')).items():
            receipt={'visual':True,'notes':'已知合成夹具，只测试新增图片与缓存变化使回执失效。','checks':dict.fromkeys(CHECKS,'VERIFIED'),
                     'equations':[],'figures':[],'tables':[],'special':[],'references':[],
                     'decisions':{x['id']:{'status':'FALSE_POSITIVE','reason':'双栏夹具，已知固定答案。'} for x in scan(f'<!-- PDF page: {p} -->'+b,folder)}}
            review(folder,p,receipt)
        self.assertEqual(finalize(folder)['status'],'PASS')
        extra.write_bytes(b'changed-fixture')
        self.assertEqual(finalize(folder)['reviewed'],1)
        write(folder/'.paperai/p0002.md','changed raw cache')
        self.assertEqual(finalize(folder)['reviewed'],0)

    def test_cli_discovers_nested_pdfs_and_continues_after_failure(self):
        from paperai.cli import main
        inputs=self.root/'papers/中文 子目录';inputs.mkdir(parents=True)
        (inputs/'正常 论文.PDF').write_bytes(self.pdf.read_bytes())
        (inputs/'坏文件.pdf').write_bytes(b'not a pdf')
        output=io.StringIO()
        with patch('sys.argv',['paperai','prepare',str(self.root/'papers'),'--results',str(self.root/'output')]),contextlib.redirect_stdout(output):
            self.assertEqual(main(),1)
        records=json.loads(output.getvalue())
        self.assertEqual(len(records),2)
        self.assertEqual(sum('folder' in r for r in records),1)
        self.assertEqual(sum('error' in r for r in records),1)


if __name__=='__main__':unittest.main()
