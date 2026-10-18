"""Stable isolated process interface for staged patch code and model updates."""
from pathlib import Path
import argparse
import json
import sys

import patch as p
import backend


def execute(action, job):
    if action=='identity':
        import updater
        return {**updater.edition(),'runtime_digest':updater.runtime_digest(p.ROOT),'files':list(p.SERVICE_RUNTIME_FILES)}
    if action=='sync-models':
        import model_sync
        value=model_sync.synchronize(job['settings'],job['directory'],job['baseline'])
        return {'digest':value['digest'],'warnings':value['warnings']}
    if action=='inspect':
        value=backend.inspect_source(Path(job['app']))
        return {'compatible':True,'assertions':value['probe']['assertions']}
    if action=='build':
        settings=job['settings'];output=Path(job['output'])
        args=argparse.Namespace(app=Path(job['app']),codex_home=Path(settings['codex_home']),output=output,
             compatible=True,baseline=Path(job['baseline']),keychain_account=settings['keychain_account'],
             keychain_service=settings['keychain_service'],node_path=settings.get('node_path'),
             install_target=Path(job['install_target']) if job.get('install_target') else None,
             model_snapshot=job.get('models'),update_metadata=job.get('update'))
        backend.build_compatible(args)
        from scripts.test_offline import main as offline
        from scripts.test_boot import main as boot
        offline(output)
        manifest=json.loads((output/'manifest.json').read_text())
        if manifest.get('proxy') or manifest.get('model_routes'):
            from scripts.test_context import main as context
            context(output)
        boot(output)
        return {'directory':str(output),'status':'prepared'}
    if action=='install':
        directory,manifest=p.load_build(Path(job['directory']))
        backend.install_compatible(directory,manifest)
        return {'directory':str(directory),'status':'installed'}
    raise ValueError('Unknown update worker action')


if __name__=='__main__':
    action,path=sys.argv[1],Path(sys.argv[2])
    try:
        value=execute(action,json.loads(path.read_text()))
        p.save_json(path.with_suffix('.result.json'),value)
    except Exception as error:
        from monitor import safe_error
        print(safe_error(error),file=sys.stderr)
        raise SystemExit(1)
