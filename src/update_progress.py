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
from windows_support import background_options, native_ui_options

ROOT = Path(__file__).resolve().parent
TEXT = {
    'current': ('补丁已是最新', '当前补丁已就绪，无需更新。'),
    'available': ('发现补丁新版本', '此次更新模型补丁。可以继续使用 Codex；点“开始更新”后，在检查完成后关闭 Codex 并安装补丁。'),
    'checking': ('正在检查补丁', '补丁已下载，正在检查来源和兼容性。可以继续使用 Codex。'),
    'downloading': ('正在下载补丁', '正在下载模型补丁。可以继续使用 Codex。'),
    'building': ('正在准备补丁', '正在准备模型目录和本地适配服务。可以继续使用 Codex。'),
    'verifying': ('正在验证补丁', '正在进行本地兼容性测试；检查通过后再安装补丁。'),
    'waiting_for_exit': ('补丁已准备好', '可以继续使用 Codex；点“开始更新”将关闭 Codex 及其后台进程，随后安装补丁。'),
    'closing': ('正在关闭 Codex', '正在关闭 Codex 及其后台进程，安装期间暂勿启动 Codex。'),
    'installing': ('正在安装补丁', '正在安装模型补丁，暂勿启动 Codex。关窗不会中断更新。'),
    'activating': ('正在启用补丁', '正在启用补丁后台组件，暂勿启动 Codex。'),
    'completed': ('补丁更新完成', '模型补丁已就绪。点“启动 Codex”打开原版应用。'),
    'failed': ('补丁更新暂未完成', '补丁更新遇到问题，已保留本地记录。可继续使用当前版本，稍后重试。'),
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


def macos_ui_command(script, *arguments):
    """Match the Python host's architecture despite LaunchServices preferences.

    Finder can pass an Intel preference through a script-based .app launcher.
    Rosetta's JXA/libffi callback allocation can then hang before showing UI.
    """
    machine = __import__('platform').machine()
    prefix = ['/usr/bin/arch', '-' + machine] if machine in ('arm64', 'x86_64') else []
    return prefix + ['/usr/bin/osascript', '-l', 'JavaScript', str(script), *map(str, arguments)]


def _spawn(command, directory, *, view=False):
    return subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **((native_ui_options() if view else background_options()) if os.name == 'nt' else {'start_new_session': True}))


def _python(settings):
    value = Path(settings.get('python_path') or sys.executable)
    if sys.platform == 'win32' and value.name.lower() == 'python.exe':
        hidden = value.with_name('pythonw.exe')
        if hidden.is_file():
            value = hidden
    return str(value.resolve())


class CheckStatus:
    """Read-only discovery feedback, separate from an install/action identity."""
    def __init__(self, directory, edition):
        self.directory, self.edition = Path(directory), edition
        self.identity = uuid.uuid4().hex

    @optional_view
    def publish(self, phase, completed=None, total=None, present=False):
        title, detail = {
            'checking': ('正在检查补丁更新', '正在读取本版本的更新来源。'),
            'downloading': ('正在下载候选补丁', '正在下载并校验文件，可继续使用 Codex。'),
            'verifying': ('正在验证候选补丁', '正在隔离环境中验证兼容性，可继续使用 Codex。'),
            'current': TEXT['current'],
            'failed': ('更新检查未完成', '暂时无法取得有效更新，请稍后重试；现有补丁保持不变。'),
        }[phase]
        atomic_json(self.directory/'update.check.json', dict(id=self.identity, edition=self.edition,
            phase=phase,title=title,detail=detail,completed=completed,total=total,
            phase_started_at=time.time(),can_start=False,can_launch=False,
            active=phase not in ('current','failed')))

    @optional_view
    def finish(self):
        value=load_json(self.directory/'update.check.json', {})
        if value.get('id')==self.identity:
            value['active']=False
            atomic_json(self.directory/'update.check.json', value)


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
                'completed': None, 'total': None, 'can_start': True, 'can_launch': False,
                'phase_started_at': time.time()}
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
            if value.get('phase') != phase or not value.get('phase_started_at'):
                value['phase_started_at'] = time.time()
            if self.requested() and phase in ('checking', 'downloading', 'building', 'verifying'):
                detail += ' 检查通过后会自动请求退出 Codex，无需再次点击。'
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
            # During the first upgrade an already-open legacy progress viewer
            # follows this same progress file. Let it finish without a second UI.
            if not ui.get('view') and ui.get('pid') and _alive(ui['pid']):
                if category not in ui['presented']:ui['presented'].append(category)
                atomic_json(self.directory/'progress.ui.json',ui)
                return
            # One on-demand whale host owns progress, settings and notifications.
            # A background notice reveals only the badge, never a settings card.
            host=load_json(self.directory/'settings-ui/host.json',{})
            from management import HOST_VIEW,queue_ui_entry
            host_is_whale=host.get('edition')==value['edition'] and host.get('view')==HOST_VIEW
            pid=host.get('pid') if host_is_whale else None
            if not pid and ui.get('view')=='whale':pid=ui.get('pid')
            if pid and _alive(pid):
                if not host_is_whale:
                    queue_ui_entry(self.directory,page='updates' if force else None,notify=not force)
                elif force:
                    atomic_json(self.directory/'settings-ui/raise.json',{'time':uuid.uuid4().hex,'page':'updates'})
                elif category not in ui['presented']:
                    atomic_json(self.directory/'settings-ui/raise.json',{'time':uuid.uuid4().hex,'notify':True})
                if category not in ui['presented']:ui['presented'].append(category)
                ui.update(pid=pid,view='whale')
                atomic_json(self.directory/'progress.ui.json',ui)
                return
            if sys.platform not in ('darwin','win32'):return
            command=[_python(settings),str(self.root/'management.py'),'show','--state-dir',str(self.directory)]
            command+=['--page','updates'] if force else ['--notify-update']
            try:
                child = _spawn(command, self.directory, view=True)
            except OSError:
                return  # A missing view never blocks a safe update.
            ui.update(pid=child.pid,view='whale')
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
def for_settings(settings, root=None, version=None, force=False, *, runtime=None, app_hash=None):
    directory = settings.get('state_dir')
    if not directory or settings.get('edition') not in ('ds', 'proxy'):
        return None
    import patch as p
    import updater
    root = Path(root or p.ROOT)
    identity = runtime or updater.runtime_digest(root)
    baseline = load_json(Path(settings.get('baseline') or directory) / 'manifest.json', {})
    if not force and baseline.get('update', {}).get('runtime_digest') == identity:
        return None
    if app_hash is None:
        try:
            app_hash = p.file_sha(p.app_layout(Path(settings['app']))['asar'])
        except (OSError, ValueError, p.PatchError):
            app_hash = str(settings.get('app', ''))
    progress = Progress(directory, root)
    progress.begin(settings['edition'], version or updater.edition(root)['version'], identity + ':' + app_hash)
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
    if value.get('edition') != manifest.get('edition'):
        return None
    if value.get('id') != identity and not _same_release(progress, manifest):
        return None
    return progress


def _same_release(progress, manifest):
    update = manifest.get('update', {})
    value = progress.read()
    meta = load_json(progress.directory / 'progress.context.json', {})
    return (bool(update.get('runtime_digest'))
            and meta.get('key', '').startswith(update['runtime_digest'] + ':')
            and update.get('release', {}).get('version') == value.get('version'))


def _build(progress):
    meta = load_json(progress.directory / 'progress.context.json', {})
    value = progress.read()
    state = load_json(progress.directory / 'state.json', {})
    candidates = [meta.get('build'), state.get('pending', {}).get('build', {}).get('directory')]
    if value.get('phase') == 'completed':
        candidates.append(state.get('installed', {}).get('build', {}).get('directory'))
    for candidate in candidates:
        if not candidate:
            continue
        directory = Path(candidate).resolve()
        manifest = load_json(directory / 'manifest.json', {})
        update = manifest.get('update', {})
        if manifest.get('edition') != value.get('edition'):
            continue
        # An explicit recheck can create a new UI request ID for an already
        # prepared/installed release. Bind to that exact runtime and version,
        # rather than losing the build when the pending state is retired.
        if update.get('progress_id') == value.get('id') or _same_release(progress, manifest):
            return directory, manifest
    return None


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
    try:
        graceful_close(sorted(targets))
    except (OSError, ValueError, subprocess.SubprocessError):
        return close_incomplete(progress)
    # Windows waits for exact verified handles, including headless backends.
    # Reopened processes still block installation and are never included in that set.
    for attempt in range(2 if sys.platform == 'win32' else 40):
        try:
            for target in targets:
                p.require_stopped(Path(target), read_only_source=sys.platform == 'win32')
            return True
        except (ValueError, p.PatchError):
            time.sleep(.25)
    return close_incomplete(progress)


def close_incomplete(progress):
    progress.publish('waiting_for_exit')
    with control_lock(progress.directory):
        value=progress.read()
        value.update(detail='Codex 尚未完全退出，补丁已准备好。可手动退出，或点击重试关闭。',can_start=True)
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
        from windows_support import close_app_handles
        close_app_handles(targets)
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
            value.update(can_start=False, detail='已收到更新请求。检查通过后会自动退出 Codex 并安装补丁。')
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
        ready=validated_close(progress)
        settings = load_json(progress.directory / 'settings.json', {})
        from monitor import service_command
        command=service_command(progress.directory, _python(settings))
        if ready:command.append('--install-ready')
        _spawn(command, progress.directory)
    else:
        try:
            record = _build(progress)
            if not record:
                raise ValueError('The installed update record is unavailable')
            launch_official(*record)
        except Exception:
            # A failed launch must not permanently consume the user's button.
            with control_lock(progress.directory):
                value = progress.read()
                claim = load_json(progress.directory / 'progress.launch.json', {})
                if claim.get('id') == identity and value.get('id') == identity:
                    (progress.directory / 'progress.launch.json').unlink(missing_ok=True)
                    value.update(can_launch=True, detail='启动未完成，请再次点击“启动 Codex”。补丁已安装。')
                    atomic_json(progress.directory / 'progress.json', value)
            raise
    return {'action': command}


@optional_view
def worker_progress(action_name, job, job_path):
    """New candidate workers also bridge first upgrades from old monitors."""
    if job.get('quiet_progress') or job.get('update',{}).get('quiet_model_sync') is True:
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
