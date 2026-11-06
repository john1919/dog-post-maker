"""Sequential foundation draft generation; never approve or render videos."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from dog_post_maker.foundation import sync_catalog,write_json
from dog_post_maker.workflow import make_options


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retry-failed',action='store_true',help='Keep ready dogs and retry only incomplete drafts.')
    args=parser.parse_args()
    details=ROOT/'output'/'details';details.mkdir(parents=True,exist_ok=True)
    path=details/'foundation_batch.json'
    inventory=sync_catalog(ROOT)
    previous={row['id']:row for row in json.loads(path.read_text())} if args.retry_failed and path.exists() else {}
    report=[]
    for index,dog in enumerate(inventory,1):
        if previous.get(dog['id'],{}).get('status')=='ready':
            report.append(previous[dog['id']]);continue
        row={**dog,'status':'no photos yet' if dog['photos']<4 else 'building'}
        report.append(row);write_json(path,report+[previous[d['id']] for d in inventory[index:] if d['id'] in previous])
        if dog['photos']<4:
            print(f"[{index}/{len(inventory)}] {dog['name']}: no photos yet ({dog['photos']})",flush=True);continue
        print(f"[{index}/{len(inventory)}] {dog['name']}: {dog['photos']} photos; making options...",flush=True)
        try:
            result=make_options(dog['id'])
            row.update(status='ready',overview=result['overview'])
            print(f"READY: {dog['name']}",flush=True)
        except Exception as error:
            row.update(status='needs attention',error=str(error))
            print(f"NEEDS ATTENTION: {dog['name']}: {error}",flush=True)
        write_json(path,report+[previous[d['id']] for d in inventory[index:] if d['id'] in previous])
    write_json(path,report)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
