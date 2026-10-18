"""Create an edition-specific update feed pinned to an already committed source."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def create(commit,sequence):
    if not re.fullmatch(r'[0-9a-f]{40}',commit) or sequence<1:
        raise ValueError('Use a full committed source SHA and a positive release sequence')
    def read(name):return subprocess.check_output(['git','show',commit+':'+name],cwd=ROOT)
    channel=json.loads(read('edition.json'))
    names=subprocess.check_output(['git','ls-tree','-r','--name-only',commit],cwd=ROOT,text=True).splitlines()
    files={name:hashlib.sha256(read(name)).hexdigest() for name in names if name!='update.json'}
    return {'format':1,**channel,'sequence':sequence,'commit':commit,'files':files}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--sequence',required=True,type=int)
    parser.add_argument('--output',type=Path,default=ROOT/'update.json')
    args=parser.parse_args()
    value=create(args.commit,args.sequence)
    args.output.write_text(json.dumps(value,indent=2)+'\n')
    print(value['edition'],value['version'],'sequence',value['sequence'],'commit',value['commit'])
