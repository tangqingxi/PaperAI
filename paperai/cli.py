"""命令行入口；失败保留局部缓存与待核查状态。"""
import argparse
import json
from pathlib import Path
from .core import prepare, finalize, review, crop
from .io import read
from .scan import scan


def main():
    parser=argparse.ArgumentParser(prog='paperai')
    sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('prepare');p.add_argument('input',nargs='?',default='papers');p.add_argument('--results',default='results')
    for name in ['status','finalize','scan']:
        p=sub.add_parser(name);p.add_argument('folder',type=Path)
    p=sub.add_parser('review');p.add_argument('folder',type=Path);p.add_argument('--page',type=int,required=True);p.add_argument('--receipt',type=Path,required=True)
    p=sub.add_parser('crop');p.add_argument('folder',type=Path);p.add_argument('--page',type=int,required=True);p.add_argument('--bbox',type=float,nargs=4,required=True)
    args=parser.parse_args()
    try:
        if args.action=='prepare':
            source=Path(args.input)
            files=[source] if source.is_file() else sorted(x for x in source.rglob('*') if x.suffix.lower()=='.pdf')
            if not files:raise ValueError('没有发现PDF')
            output=[];failed=False
            for path in files:
                try:output.append({'source':str(path),'folder':str(prepare(path,args.results))})
                except Exception as exc:failed=True;output.append({'source':str(path),'error':str(exc)})
            print(json.dumps(output,ensure_ascii=False,indent=2));return int(failed)
        if args.action=='scan':result=scan((args.folder/'paper.md').read_text(encoding='utf-8'),args.folder)
        elif args.action=='review':result=review(args.folder,args.page,read(args.receipt))
        elif args.action=='crop':result=crop(args.folder,args.page,args.bbox)
        elif args.action=='finalize':result=finalize(args.folder)
        else:result=finalize(args.folder)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return int(isinstance(result,dict) and result.get('status')=='FAIL')
    except Exception as exc:
        print(json.dumps({'status':'FAIL','error':str(exc)},ensure_ascii=False));return 1


if __name__=='__main__':
    raise SystemExit(main())
