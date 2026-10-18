"""Exercise the real bundled app-server against DeepSeek in an isolated temporary home."""
from pathlib import Path
import json, os, queue, subprocess, tempfile, threading, time

import argparse, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import patch

def main(directory, model='deepseek-flash'):
    patch.mac_only()
    directory, manifest = patch.load_build(Path(directory))
    import proxy_support as ps
    proxy = model != patch.MODEL
    patch.require(not proxy or model in manifest.get('proxy',{}).get('models',[]), 'Model is not enabled in this build')
    provider = ps.PROVIDER if proxy else patch.PROVIDER
    staged = directory/'patched.app'
    CLI = str(patch.cli_path(staged if manifest.get('status')=='prepared' and staged.is_dir() else Path(manifest['target'])))
    evidence={'model':model,'provider':provider,'checks':[], 'status':'failed'}
    with tempfile.TemporaryDirectory(prefix='codex-ds41f-smoke-') as tmp:
        home=Path(tmp)/'home';home.mkdir();cwd=Path(tmp)/'work';cwd.mkdir()
        (cwd/'probe.txt').write_text('DS41F_TOOL_OK\n')
        provider_text = ps.provider_config(manifest['proxy'], directory/'proxy_support.py') if proxy else patch.provider_config(manifest['keychain_account'], manifest['keychain_service'])
        (home/'config.toml').write_text('model_catalog_json = '+json.dumps(str(directory/'desktop-models.json'))+'\n'+provider_text)
        env={**os.environ,'CODEX_HOME':str(home)}
        suffix = '-'+model if proxy else ''
        log=open(directory/('smoke'+suffix+'-stderr.log'),'w')
        p=subprocess.Popen([CLI,'app-server','--listen','stdio://'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,env=env,cwd=cwd)
        events=queue.Queue()
        def reader():
            for line in p.stdout:
                try: events.put(json.loads(line))
                except json.JSONDecodeError: pass
        threading.Thread(target=reader,daemon=True).start()
        counter=0
        def send(method,params):
            nonlocal counter
            counter+=1;p.stdin.write(json.dumps({'id':counter,'method':method,'params':params})+'\n');p.stdin.flush()
            return counter
        def receive(ident,seconds=45):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                e=events.get(timeout=max(.1,end-time.monotonic()))
                if e.get('id')==ident:
                    if 'error' in e: raise RuntimeError(json.dumps(e['error']))
                    return e['result']
            raise TimeoutError('RPC '+str(ident))
        try:
            receive(send('initialize',{'clientInfo':{'name':'ds41f_patch_validation','version':'1.0'},'capabilities':{'experimentalApi':True}}))
            models=receive(send('model/list',{'includeHidden':False,'limit':100}))['data']
            flash=[m for m in models if m['model'].startswith('deepseek-')]
            assert len(flash)==1 and flash[0]['model']=='deepseek-flash'
            evidence['checks'].append({'catalog':flash[0]['displayName'],'gpt_models':[m['model'] for m in models if m['model'].startswith('gpt-')]})
            assert len([m for m in models if m['model']==model])==1, 'Requested model missing from catalog'
            thread=receive(send('thread/start',{'model':model,'modelProvider':provider,'cwd':str(cwd),'ephemeral':True,'approvalPolicy':'never','sandbox':'read-only','config':{'web_search':'disabled','features.memories':False},'baseInstructions':'You are testing the model connection. Follow the user request concisely.'}))
            assert thread['modelProvider']==provider
            tid=thread['thread']['id'];evidence['checks'].append({'thread_provider':thread['modelProvider'],'model':thread['model']})
            print('PASS:',model,'catalog and thread routing',flush=True)
            for prompt,expected in [('Reply exactly DS41F_OK. Do not call tools.','DS41F_OK'),('Use the shell tool to read probe.txt in the current directory, then reply with its exact content.','DS41F_TOOL_OK')]:
                receive(send('turn/start',{'threadId':tid,'input':[{'type':'text','text':prompt}],'model':model,'effort':'high' if proxy else 'low'}))
                deadline=time.monotonic()+180;messages=[];item_types=[];status=None
                while time.monotonic()<deadline:
                    e=events.get(timeout=max(.1,deadline-time.monotonic()));method=e.get('method');params=e.get('params',{})
                    if method=='item/completed':
                        item=params['item'];item_types.append(item['type'])
                        if item['type']=='agentMessage':messages.append(item.get('text',''))
                    if method=='turn/completed':
                        status=params['turn']['status']
                        if status!='completed':raise RuntimeError(json.dumps(params['turn'].get('error')))
                        break
                assert status=='completed',status
                assert expected in '\n'.join(messages),messages
                if expected=='DS41F_TOOL_OK': assert 'commandExecution' in item_types,item_types
                evidence['checks'].append({'expected':expected,'status':status,'item_types':item_types,'assistant':messages})
                print('PASS:',model,expected,'items:',','.join(item_types),flush=True)
            evidence['status']='passed'
        finally:
            p.terminate()
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill();p.wait()
            log.close()
            (directory/('smoke'+suffix+'-result.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Optional live DeepSeek API check. Sends two small test turns; may incur API usage charges.')
    parser.add_argument('directory', type=Path)
    parser.add_argument('--run', action='store_true', required=True, help='Explicitly run the live API test')
    parser.add_argument('--model', default=patch.MODEL, help='An explicitly enabled model ID; defaults to DeepSeek')
    args = parser.parse_args()
    main(args.directory, args.model)
