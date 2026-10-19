"""Private update state/controller; native views render data and fixed actions only."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from monitor import atomic_json, load_json, state_lock
from windows_support import background_options

ROOT = Path(__file__).resolve().parent
TEXT = {
    'available': ('发现新版本', '可以继续使用 Codex；点“开始更新”后，将在检查完成后关闭 Codex 并安装。'),
    'checking': ('正在检查更新', '新版本已下载，正在检查来源和兼容性。可以继续使用 Codex。'),
    'downloading': ('正在下载更新', '正在下载经过校验的更新文件。可以继续使用 Codex。'),
    'building': ('正在准备更新', '正在准备模型目录和本地适配服务。可以继续使用 Codex。'),
    'verifying': ('正在验证更新', '正在进行本地兼容性测试；检查通过后再安装。'),
    'waiting_for_exit': ('更新已准备好', '可以继续使用 Codex；点“开始更新”将正常关闭 Codex，随后安装。'),
    'closing': ('正在关闭 Codex', '已请求正常关闭 Codex。请保存工作，安装期间暂勿启动 Codex。'),
    'installing': ('正在安装更新', '正在安装本地适配服务和配置，暂勿启动 Codex。关窗不会中断更新。'),
    'activating': ('正在完成更新', '正在启用新的后台更新组件，暂勿启动 Codex。'),
    'completed': ('更新完成', '现在可以启动 Codex。点“启动 Codex”打开原版应用。'),
    'failed': ('更新暂未完成', '更新遇到问题，已保留本地记录。可继续使用当前版本，稍后重试。'),
}
START_PHASES = {'available', 'checking', 'downloading', 'building', 'verifying', 'waiting_for_exit'}


def optional_view(function):
    """View state is optional; invalid actions still use strict reads below."""
    from functools import wraps
    @wraps(function)
    def call(*args, **kwargs):
        try:return function(*args, **kwargs)
        except Exception:return None
    return call


@contextmanager
def control_lock(directory):
    # Independent of the monitor lock: a click remains responsive during a build.
    for attempt in range(40):
        with state_lock(Path(directory) / 'progress-control') as acquired:
            if acquired:
                yield
                return
        time.sleep(.025)
    raise ValueError('Update progress is busy')


def _spawn(command, directory):
    return subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **(background_options() if os.name == 'nt' else {'start_new_session': True}))


def _python(settings):
    value = Path(settings.get('python_path') or sys.executable)
    if sys.platform == 'win32' and value.name.lower() == 'python.exe':
        hidden = value.with_name('pythonw.exe')
        if hidden.is_file():
            value = hidden
    return str(value.resolve())


class Progress:
    def __init__(self, directory, root=None):
        self.directory = Path(directory).resolve()
        self.root = Path(root or ROOT).resolve()

    def read(self):
        return load_json(self.directory / 'progress.json', {})

    @optional_view
    def begin(self, edition, version, key, restart=False):
        if edition not in ('ds', 'proxy') or not re.fullmatch(r'[\w.+-]{1,79}', version):
            raise ValueError('Invalid update identity')
        with control_lock(self.directory):
            meta = load_json(self.directory / 'progress.context.json', {})
            value = self.read()
            if meta.get('key') == key and value.get('edition') == edition and value.get('version') == version and not (restart and value.get('phase') in ('completed','failed')):
                return value['id']
            identity = uuid.uuid4().hex
            atomic_json(self.directory / 'progress.context.json', {'format': 1, 'id': identity,
                'key': key, 'root': str(self.root)})
            value = {'format': 1, 'id': identity, 'edition': edition, 'version': version,
                'phase': 'available', 'title': TEXT['available'][0], 'detail': TEXT['available'][1],
                'completed': None, 'total': None, 'can_start': True, 'can_launch': False}
            atomic_json(self.directory / 'progress.json', value)
            return identity

    @optional_view
    def requested(self):
        request = load_json(self.directory / 'progress.request.json', {})
        return request.get('id') == self.read().get('id') and request.get('action') == 'start'

    @optional_view
    def publish(self, phase, completed=None, total=None, build=None, present=False):
        if phase not in TEXT:
            raise ValueError('Unknown update phase')
        if (completed is not None or total is not None) and not (
                type(completed) is int and type(total) is int and 0 <= completed <= total and total > 0):
            raise ValueError('Progress counts must be actual completed/total units')
        with control_lock(self.directory):
            value = self.read()
            if not value:
                return
            title, detail = TEXT[phase]
            value.update(phase=phase, title=title, detail=detail, completed=completed, total=total,
                can_start=phase in START_PHASES and not self.requested(), can_launch=phase == 'completed' and load_json(self.directory/'progress.launch.json',{}).get('id')!=value['id'])
            atomic_json(self.directory / 'progress.json', value)
            if build is not None:
                meta = load_json(self.directory / 'progress.context.json', {})
                meta['build'] = str(Path(build).resolve())
                atomic_json(self.directory / 'progress.context.json', meta)
        if present:
            self.present()

    @optional_view
    def present(self, force=False):
        # Only installed monitor settings can display a view. Preflight/unit jobs
        # without a configured app/home/Python remain completely headless.
        settings = load_json(self.directory / 'settings.json', {})
        value = self.read()
        if not value or not all(settings.get(k) for k in ('app', 'codex_home', 'python_path', 'edition')):
            return
        if settings['edition'] != value['edition'] or settings.get('progress_ui') is False and not force:
            return
        with control_lock(self.directory):
            ui = load_json(self.directory / 'progress.ui.json', {})
            if ui.get('id') != value['id']:
                ui = {'id': value['id'], 'presented': []}
            category = 'completed' if value['phase'] == 'completed' else 'available'
            if category in ui['presented'] and not force:
                return
            # A live panel already follows the state into completion.
            if ui.get('pid') and _alive(ui['pid']):
                if category not in ui['presented']:ui['presented'].append(category)
                atomic_json(self.directory / 'progress.ui.json', ui)
                return
            python, controller = _python(settings), str(self.root / 'update_progress.py')
            if sys.platform == 'darwin':
                command = ['/usr/bin/osascript', '-l', 'JavaScript', str(self.root / 'update_progress_macos.js'),
                           str(self.directory), python, controller]
            elif sys.platform == 'win32':
                from windows_support import native_ui_command
                command = native_ui_command(self.root/'update_progress.ps1',{'StateDirectory':str(self.directory),
                    'PythonExecutable':python,'ControllerPath':controller})
            else:
                return
            try:
                child = _spawn(command, self.directory)
            except OSError:
                return  # A missing view never blocks a safe update.
            ui['pid'] = child.pid
            if category not in ui['presented']:ui['presented'].append(category)
            atomic_json(self.directory / 'progress.ui.json', ui)

    def metadata(self):
        try:return {'progress_state_dir': str(self.directory), 'progress_id': self.read().get('id')}
        except Exception:return {}


def _alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.restype = ctypes.c_void_p
        api.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@optional_view
def for_settings(settings, root=None, version=None, force=False):
    directory = settings.get('state_dir')
    if not directory or settings.get('edition') not in ('ds', 'proxy'):
        return None
    import patch as p
    import updater
    root = Path(root or p.ROOT)
    identity = updater.runtime_digest(root)
    baseline = load_json(Path(settings.get('baseline') or directory) / 'manifest.json', {})
    if not force and baseline.get('update', {}).get('runtime_digest') == identity:
        return None
    try:
        app_hash = p.file_sha(p.app_layout(Path(settings['app']))['asar'])
    except (OSError, ValueError, p.PatchError):
        app_hash = str(settings.get('app', ''))
    progress = Progress(directory, root)
    progress.begin(settings['edition'], version or updater.edition(root)['version'], identity + ':' + app_hash, restart=force)
    return progress


@optional_view
def from_manifest(manifest):
    update = manifest.get('update', {})
    if update.get('quiet_model_sync') is True:
        return None
    directory, identity = update.get('progress_state_dir'), update.get('progress_id')
    if not directory or not identity:
        return None
    progress = Progress(directory)
    value = progress.read()
    if value.get('id') != identity or value.get('edition') != manifest.get('edition'):
        return None
    return progress


def _build(progress):
    meta = load_json(progress.directory / 'progress.context.json', {})
    candidate = meta.get('build')
    if not candidate:
        state = load_json(progress.directory / 'state.json', {})
        candidate = state.get('pending', {}).get('build', {}).get('directory')
    if not candidate:
        return None
    directory = Path(candidate).resolve()
    manifest = load_json(directory / 'manifest.json', {})
    update = manifest.get('update', {})
    if update.get('progress_id') != progress.read().get('id') or manifest.get('edition') != progress.read().get('edition'):
        return None
    return directory, manifest


@optional_view
def validated_close(progress):
    """Consume explicit intent only after rechecking a prepared native build."""
    if not progress.requested() or progress.read().get('phase') != 'waiting_for_exit':
        return False
    record = _build(progress)
    if not record:
        return False
    directory, manifest = record
    if manifest.get('architecture') != 'official-client-v1' or manifest.get('status') != 'prepared':
        return False
    from official_client_install import validate_build, _guard_staged_config
    import patch as p
    validate_build(directory, manifest)
    _guard_staged_config(p.read_config(Path(manifest['codex_home']) / 'config.toml'),
                         (directory / 'config-before.toml').read_text(encoding='utf-8'))
    progress.publish('closing')
    targets = {str(Path(manifest['target']).resolve())}
    previous = load_json(Path(manifest.get('legacy_baseline') or directory) / 'manifest.json', {})
    if previous.get('deployment_target'):
        targets.add(str(Path(previous['deployment_target']).resolve()))
    graceful_close(sorted(targets))
    # Give a normal quit time to finish; there is deliberately no force-kill.
    for attempt in range(40):
        try:
            for target in targets:
                p.require_stopped(Path(target), read_only_source=sys.platform == 'win32')
            return True
        except (ValueError, p.PatchError):
            time.sleep(.25)
    progress.publish('waiting_for_exit')
    with control_lock(progress.directory):
        value=progress.read()
        value.update(detail='Codex 仍在运行。请手动退出，或再次点“开始更新”请求正常关闭。',can_start=True)
        atomic_json(progress.directory/'progress.json',value)
    return False


def graceful_close(targets):
    if sys.platform == 'darwin':
        script = ("ObjC.import('AppKit'); function run(a){const paths=JSON.parse(a[0]); "
            "const apps=$.NSRunningApplication.runningApplicationsWithBundleIdentifier('com.openai.codex');"
            "for(let i=0;i<apps.count;i++){const app=apps.objectAtIndex(i);"
            "if(paths.indexOf(ObjC.unwrap(app.bundleURL.path))>=0)app.terminate;}}")
        subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', script, json.dumps(targets)],
            check=True, capture_output=True, timeout=15, **background_options())
    elif sys.platform == 'win32':
        # Paths are a JSON argument encoded as data; PowerShell never evaluates them.
        import base64
        data = base64.b64encode(json.dumps(targets).encode('utf-8')).decode('ascii')
        script = ("$roots=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + data + "')));"
            "Get-Process | ForEach-Object { try {$p=$_.Path; if($p){foreach($r in $roots){"
            "if($p.StartsWith($r.TrimEnd('\\')+'\\',[StringComparison]::OrdinalIgnoreCase)){"
            "$null=$_.CloseMainWindow();break}}}}catch{}}")
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            check=True, capture_output=True, timeout=15, **background_options())
    else:
        raise ValueError('Unsupported desktop platform')


def launch_official(directory, manifest):
    if manifest.get('status') != 'installed':
        raise ValueError('Update installation is not complete')
    app = Path(manifest['target']).resolve()
    from official_client_update import verify_official
    verify_official(app)
    if sys.platform == 'darwin':
        _spawn(['/usr/bin/open', '-a', str(app)], directory)
    elif sys.platform == 'win32':
        from official_client_shortcut import official_entry
        entry=official_entry(app)
        aumid=entry.get('app_user_model_id','')
        if not re.fullmatch(r'OpenAI\.Codex_[\w.-]+![\w.-]+',aumid):
            raise ValueError('The official registered Store application could not be identified')
        _spawn([entry['target'],'shell:AppsFolder\\'+aumid],directory)
    else:
        raise ValueError('Unsupported desktop platform')


def action(directory, identity, command, phase=None):
    if command not in ('start', 'launch', 'dismiss'):
        raise ValueError('Unknown update action')
    progress = Progress(directory)
    with control_lock(progress.directory):
        value = progress.read()
        if not identity or value.get('id') != identity:
            raise ValueError('This update window is no longer current')
        if command == 'dismiss':
            if phase not in TEXT:
                raise ValueError('Unknown dismissed update phase')
            ui = load_json(progress.directory / 'progress.ui.json', {'id': identity, 'presented': []})
            if ui.get('id') == identity:
                ui.pop('pid', None)
                ui['dismissed_phase'] = phase
                category = 'completed' if phase == 'completed' else 'available'
                if category not in ui['presented']:ui['presented'].append(category)
                if phase!='completed' and value['phase']=='completed':
                    ui['presented']=[v for v in ui['presented'] if v!='completed']
                atomic_json(progress.directory / 'progress.ui.json', ui)
        if command == 'start':
            if value['phase'] not in START_PHASES or not value.get('can_start'):
                raise ValueError('This update cannot be started now')
            atomic_json(progress.directory / 'progress.request.json', {'format': 1, 'id': identity, 'action': 'start'})
            value['can_start'] = False
            atomic_json(progress.directory / 'progress.json', value)
        elif command=='launch':
            claim=load_json(progress.directory/'progress.launch.json',{})
            if not value.get('can_launch') or value['phase']!='completed' or claim.get('id')==identity:
                raise ValueError('This update is not ready to launch')
            atomic_json(progress.directory/'progress.launch.json',{'id':identity})
            value['can_launch']=False;atomic_json(progress.directory/'progress.json',value)
    if command=='dismiss':
        if phase!='completed' and value['phase']=='completed':progress.present()
        return {'action':'dismissed'}
    if command == 'start':
        # Works for old monitors too: a ready build can quit immediately; an
        # in-flight new worker consumes this same durable request after verify.
        validated_close(progress)
        settings = load_json(progress.directory / 'settings.json', {})
        from monitor import service_command
        _spawn(service_command(progress.directory, _python(settings)), progress.directory)
    else:
        record = _build(progress)
        if not record:
            raise ValueError('The installed update record is unavailable')
        launch_official(*record)
    return {'action': command}


@optional_view
def worker_progress(action_name, job, job_path):
    """New candidate workers also bridge first upgrades from old monitors."""
    if job.get('update',{}).get('quiet_model_sync') is True:
        return None
    if action_name == 'install':
        return from_manifest(load_json(Path(job['directory']) / 'manifest.json', {}))
    settings = job.get('settings')
    if settings is None and action_name == 'identity':
        path = Path(job_path).resolve()
        if path.name != 'worker-identity.json' or path.parent.name != 'identity':
            return None
        directory = path.parent.parent
        settings = load_json(directory / 'settings.json', {})
        if settings.get('state_dir') and Path(settings['state_dir']).resolve() != directory:
            return None
        settings = {**settings, 'state_dir': str(directory)}
    if not settings:
        return None
    import patch as p
    if settings.get('edition') != p.EDITION:
        return None
    if action_name in ('identity','build') and settings.get('state_dir'):
        current=Progress(settings['state_dir'])
        value=current.read()
        if value.get('edition')==p.EDITION and value.get('version')==__import__('updater').edition()['version'] and value.get('phase') not in ('completed','failed'):
            return current
    return for_settings(settings, force=action_name == 'build')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--action', choices=('start', 'launch', 'dismiss'), required=True)
    parser.add_argument('--id', required=True)
    parser.add_argument('--phase', choices=tuple(TEXT))
    args = parser.parse_args(argv)
    try:
        action(args.state_dir, args.id, args.action, args.phase)
        return 0
    except Exception:
        # UI actions never print paths, endpoint tokens, or raw error details.
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
