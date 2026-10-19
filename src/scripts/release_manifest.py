"""Publish pinned portable updates while keeping the development tree organized."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
ROOT = SOURCE.parent if SOURCE.name == "src" else SOURCE


def git(*args, **kwargs):
    return subprocess.check_output(["git", *args], cwd=ROOT, **kwargs)


def distribution(commit):
    """Build a source-only tree at historical paths for installed old updaters.

    Uses a temporary Git index; never checks out, resets or changes user files.
    The returned commit must be pushed with its release tag before feed use.
    """
    names=git("ls-tree","-r","--name-only",commit,text=True).splitlines()
    if "src/edition.json" not in names:
        return commit
    entries=[];seen=set()
    for name in names:
        if name in ("patch.py","monitor.py","update.json"):
            continue
        target=name.removeprefix("src/")
        if target in seen:
            raise ValueError("Portable release path collision: "+target)
        seen.add(target)
        object_id=git("rev-parse",commit+":"+name,text=True).strip()
        entries.append("100644 "+object_id+"\t"+target+"\n")
    with tempfile.TemporaryDirectory(prefix="codex-release-index-") as temp:
        env={**os.environ,"GIT_INDEX_FILE":str(Path(temp)/"index")}
        git("read-tree","--empty",env=env)
        git("update-index","--index-info",env=env,input="".join(entries).encode())
        tree=git("write-tree",env=env,text=True).strip()
    return git("commit-tree",tree,"-p",commit,"-m","Package portable source for existing update clients",text=True).strip()


def create(commit,sequence):
    if not re.fullmatch(r"[0-9a-f]{40}",commit) or sequence<1:
        raise ValueError("Use a full committed source SHA and a positive release sequence")
    def read(name):return git("show",commit+":"+name)
    channel=json.loads(read("edition.json"))
    names=git("ls-tree","-r","--name-only",commit,text=True).splitlines()
    files={name:hashlib.sha256(read(name)).hexdigest() for name in names if name!="update.json"}
    return {"format":1,**channel,"sequence":sequence,"commit":commit,"files":files}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit",required=True)
    parser.add_argument("--sequence",required=True,type=int)
    parser.add_argument("--distribution",action="store_true",help="Build a portable source commit from src/")
    parser.add_argument("--output",type=Path,default=ROOT/"update.json")
    args=parser.parse_args()
    commit=distribution(args.commit) if args.distribution else args.commit
    value=create(commit,args.sequence)
    if commit!=args.commit:value["source_commit"]=args.commit
    args.output.write_text(json.dumps(value,indent=2)+"\n")
    print(value["edition"],value["version"],"sequence",value["sequence"],"commit",value["commit"])
