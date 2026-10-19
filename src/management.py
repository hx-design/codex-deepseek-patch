"""On-demand native settings and redacted diagnostics; no HTTP server or daemon."""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import plistlib
import shlex
import subprocess
import sys
import time
import tempfile
from types import SimpleNamespace
import urllib.error
import uuid

import monitor
import patch as p

ROOT = Path(__file__).resolve().parent
PHASES = {'installed': '已生效', 'idle': '等待检查', 'stabilizing': '正在确认更新',
          'building': '正在准备', 'waiting_for_exit': '准备完成，退出 Codex 后应用',
          'installing': '正在安装', 'failed': '更新暂未完成', 'incompatible': '等待兼容适配',
          'unavailable': '暂时无法检查', 'busy': '后台任务进行中'}
READ_ACTIONS = {'snapshot', 'diagnose', 'probe_connection', 'cleanup_preview', 'compare_models'}
ACTIONS = READ_ACTIONS | {'save_connection', 'remove_connection', 'save_model', 'save_options',
                         'save_proxy', 'save_deepseek', 'save_images', 'check_updates', 'retry_update',
                         'show_progress', 'cleanup', 'recover', 'restore', 'cancel_recovery'}


class SettingsError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(value, message, code='invalid_input'):
    if not value:raise SettingsError(code, message)


def find_directory(explicit=None):
    if explicit is not None:return Path(explicit).expanduser().resolve()
    default = monitor.default_directory()
    for path in (default, default.parent/'monitor-proxy', default.parent/'monitor-ds'):
        if monitor.load_json(path/'settings.json', {}).get('edition') == p.EDITION:return path
    return default


def records(directory):
    directory = Path(directory)
    settings = monitor.load_json(directory/'settings.json', {})
    require(settings.get('edition') == p.EDITION, '请使用与已安装版本对应的设置入口。', 'edition_mismatch')
    require(settings.get('baseline') and settings.get('codex_home'), '尚未找到已安装的适配层，请先完成首次安装。', 'not_installed')
    build = Path(settings['baseline'])
    manifest = monitor.load_json(build/'manifest.json', {})
    require(manifest.get('edition') == p.EDITION and manifest.get('architecture') == 'official-client-v1',
            '请先迁移到原生适配版本，再使用设置窗口。', 'migration_required')
    return settings, build, manifest


def revision(directory):
    settings, _, _ = records(directory)
    paths = [Path(directory)/name for name in ('settings.json','connections.json','model-preferences.json')]
    paths.append(Path(settings['codex_home'])/'config.toml')
    return hashlib.sha256(b'\0'.join(path.read_bytes() if path.is_file() else b'' for path in paths)).hexdigest()


def connection_document(directory, manifest):
    value = monitor.load_json(Path(directory)/'connections.json')
    if value is None:
        value = {'locals': copy.deepcopy(manifest.get('locals', []))}
    value = copy.deepcopy(value)
    value.setdefault('deepseek_enabled', manifest.get('deepseek_enabled', p.EDITION == 'ds'))
    value.setdefault('proxy', copy.deepcopy(manifest.get('proxy')))
    value.setdefault('proxy_auto_add', True)
    policy = monitor.load_json(Path(directory)/'models.json',{}).get('policy',{}).get('easycli-desktop',{})
    if value.get('proxy') and policy.get('selected') and (
            not value.get('proxy_selection_id') or value['proxy_selection_id']==policy.get('selection_revision')):
        value['proxy']['models'] = list(policy['selected'])
    return value


def _models(directory, build, manifest):
    import model_sync
    preferences = model_sync.validate_preferences(monitor.load_json(Path(directory)/'model-preferences.json', {}))
    cache = monitor.load_json(Path(directory)/'models.json', {})
    installed = monitor.load_json(build/'desktop-models.json', {'models': []})['models']
    entries = {row['slug']: row for row in cache.get('catalog', {}).get('models', [])}
    entries.update({row['slug']: row for row in installed})
    specs = cache.get('specs', {})
    routes = {**cache.get('routes', {}), **manifest.get('model_routes', {})}
    for model in preferences.get('hidden', []):
        if model not in entries:
            spec = specs.get(model, {})
            entries[model] = {'slug': model, 'display_name': spec.get('display_name', model),
                'max_context_window': spec.get('context_window'), 'context_window': spec.get('context_window'),
                'input_modalities': spec.get('input_modalities', ['text']),
                'supported_reasoning_levels': [{'effort': v} for v in spec.get('reasoning_levels', [])]}
    rows = []
    for model, item in entries.items():
        spec = specs.get(model, {})
        maximum = item.get('max_context_window') or item.get('context_window')
        selected = preferences.get('context_windows', {}).get(model)
        overridden = model in preferences.get('context_windows', {})
        effective = (min(selected, maximum) if selected else maximum) if overridden and maximum else item.get('context_window')
        rows.append({'id': model, 'name': item.get('display_name', model),
            'provider': routes.get(model, {}).get('provider', 'GPT'),
            'visible': model not in preferences.get('hidden', []),
            'maximum': maximum, 'effective': effective, 'installed_window': item.get('context_window'),
            'server_window': spec.get('configured_context_window'), 'context_override': selected,
            'compact_override': preferences.get('compact_limits', {}).get(model),
            'compact': preferences.get('compact_limits', {}).get(model) or (int(effective*.9) if effective else None),
            'tools': routes.get(model, {}).get('supports_tools', spec.get('supports_tools', item.get('shell_type') != 'disabled')),
            'images': 'image' in item.get('input_modalities', []),
            'reasoning': [v['effort'] for v in item.get('supported_reasoning_levels', [])],
            'source': spec.get('source', 'official-catalog')})
    return sorted(rows, key=lambda row: (row['provider'] != 'GPT', row['provider'], row['name'].casefold()))


def snapshot(directory):
    directory = Path(directory)
    settings, build, manifest = records(directory)
    state = monitor.load_json(directory/'state.json', {})
    document = connection_document(directory, manifest)
    current = p.tomllib.loads(p.read_config(Path(settings['codex_home'])/'config.toml'))
    jobs = recovery_jobs(settings['codex_home'])
    return {'format': 1, 'revision': revision(directory), 'edition': p.EDITION,
        'edition_label': 'DS 版' if p.EDITION == 'ds' else '扩展版',
        'version': manifest.get('tool_version'), 'app_version': manifest.get('app_version'),
        'phase': state.get('phase', 'idle'), 'status': PHASES.get(state.get('phase'), '等待检查'),
        'queued': (directory/'monitor.request.json').is_file(),
        'models': _models(directory, build, manifest), 'connections': document.get('locals', []),
        'proxy': document.get('proxy'), 'disabled_proxy':document.get('disabled_proxy'),
        'deepseek_enabled': document['deepseek_enabled'],
        'proxy_auto_add': document['proxy_auto_add'],
        'options': {name: settings.get(name, default) for name, default in
                    [('auto_update', True), ('model_sync', True), ('interval', 300), ('progress_ui', True)]},
        'images_enabled': 'cpa_images' in current.get('mcp_servers', {}),
        'recovery': jobs, 'recovery_directory':str(Path(settings['codex_home'])/'model-adapter-operations'),
        'last_checked_at': state.get('last_checked_at'),
        'progress': {k: v for k, v in monitor.load_json(directory/'progress.json', {}).items()
                     if k in ('phase','title','detail','completed','total','version')},
        'help_url': 'https://github.com/limyum-creator/codex-deepseek-patch/tree/' +
                    ('main' if p.EDITION == 'ds' else 'codex/existing-proxy-models')}


def recovery_jobs(home):
    root = Path(home)/'model-adapter-operations'
    if not root.is_dir() or root.is_symlink():return []
    result = []
    for path in root.glob('*.json'):
        if path.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}\.json', path.name):continue
        try:
            if path.stat().st_size > 8192:continue
            value = monitor.load_json(path, {})
            if value.get('format') == 1 and time.time()-value.get('updated_at', 0) < 3600:
                result.append({k: value[k] for k in ('id','model','phase','part','total','level','cache','updated_at') if k in value})
        except (OSError, ValueError, TypeError):continue
    return sorted(result, key=lambda row: row.get('updated_at',0), reverse=True)[:20]


@contextmanager
def edit_lock(directory, expected, cancelled=None, timeout=120):
    deadline = time.monotonic()+timeout
    while True:
        if cancelled and cancelled():raise SettingsError('cancelled', '操作已取消。')
        with monitor.state_lock(directory) as locked:
            if locked:
                require(expected == revision(directory), '设置已被其他操作更新，请刷新后再保存。', 'revision_conflict')
                yield
                return
        if time.monotonic() >= deadline:raise SettingsError('busy', '后台检查仍在进行，请稍后再试。')
        time.sleep(.2)


def _queue(directory):
    monitor.request_update(directory, sync_now=True)
    monitor.wake_monitor(directory)


def probe_connection(connection, cancelled=None):
    import local_support
    connection = local_support.validate(connection)
    def check():
        if cancelled and cancelled():raise SettingsError('cancelled','操作已取消。')
    check()
    catalog = local_support.request(connection, '/models')
    check()
    def fetch(row,path,body=None):
        check()
        value=catalog if path=='/models' else local_support.request(row,path,body)
        check()
        return value
    specs, warnings = local_support.discover({**connection,'models':[]},fetch=fetch)
    check()
    available = [row['id'] for row in catalog.get('data', []) if isinstance(row,dict) and isinstance(row.get('id'),str)]
    return {'available': available, 'specs': specs, 'warnings': warnings,
            'message': '接口已连接；规格未提供的模型可在高级设置中补充。' if warnings else '连接和模型规格检查通过。'}


def private_write(path, data):
    path=Path(path);monitor.private_directory(path.parent)
    handle,temporary=tempfile.mkstemp(prefix='.credential-',dir=path.parent)
    try:
        with os.fdopen(handle,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:Path(temporary).unlink(missing_ok=True)


def diagnose(directory, *, endpoints=False):
    """Allowlisted report fields only. Never export raw exceptions/config/log text."""
    from official_client_host import health_status
    settings, build, manifest = records(directory)
    state = monitor.load_json(Path(directory)/'state.json', {})
    checks = []
    def add(code, status, message):checks.append({'code': code, 'status': status, 'message': message})
    try:
        from official_client_update import managed_configuration
        managed_configuration(build, manifest, settings['codex_home'])
        add('configuration','ok','受管配置和文件完整。')
    except Exception:add('configuration','error','受管配置或文件发生变化，可重新准备适配层。')
    host = monitor.load_json(build/'host-settings.json', {})
    health = health_status(host)
    add('adapter','ok' if health['ready'] else 'error',
        '本地适配服务正常。' if health['ready'] else '本地适配服务未就绪，请检查或恢复安装。')
    try:
        enabled = monitor.Scheduler(directory).status()['enabled']
        add('monitor','ok' if enabled else 'warning','自动监测已开启。' if enabled else '自动监测未开启。')
    except Exception:add('monitor','error','无法读取系统监测任务状态。')
    models = _models(directory, build, manifest)
    add('models','ok' if models else 'warning', '可识别模型：' + str(len(models)))
    for index, connection in enumerate(connection_document(directory,manifest).get('locals', []),1):
        if not endpoints:continue
        try:
            result = probe_connection(connection)
            add('endpoint_'+str(index),'warning' if result['warnings'] else 'ok',
                '接口可访问，发现 '+str(len(result['available']))+' 个模型。')
        except Exception as error:
            value = public_error(error)
            add('endpoint_'+str(index),'error',value['message'])
    report = {'format': 1, 'edition': p.EDITION, 'patch_version': manifest.get('tool_version'),
              'app_version': manifest.get('app_version'), 'platform': sys.platform,
              'phase': state.get('phase'), 'checks': checks,
              'summary': '检查通过' if all(v['status']=='ok' for v in checks) else '有需要处理的项目'}
    report['text'] = '\n'.join(['Codex 模型适配器 · '+report['summary'],
        '系统：'+sys.platform+'　版本：'+str(report['patch_version'])+'　通道：'+p.EDITION,
        'Codex：'+str(report['app_version']), *[v['status'].upper()+' · '+v['message'] for v in checks]])
    return report


def public_error(error):
    if isinstance(error, SettingsError):return {'code':error.code,'message':str(error)}
    if isinstance(error, urllib.error.HTTPError):
        messages = {401:'接口拒绝了凭证，请检查或更新 API Key。',403:'当前账户没有此接口的访问权限。',
                    404:'未找到模型接口，请检查服务地址是否包含正确的 /v1 路径。',429:'服务额度或请求频率受限。'}
        return {'code':'http_'+str(error.code),'message':messages.get(error.code,'上游服务暂时不可用，请稍后重试。')}
    if isinstance(error, (urllib.error.URLError, ConnectionError, TimeoutError)):
        return {'code':'endpoint_unavailable','message':'无法连接服务，请确认服务已启动、地址正确且网络可达。'}
    if isinstance(error, FileNotFoundError):return {'code':'file_missing','message':'配置或凭证文件不存在，请重新选择。'}
    if isinstance(error, PermissionError):return {'code':'permission_denied','message':'无法访问配置文件，请检查当前用户的文件权限。'}
    if isinstance(error, (ValueError, KeyError, TypeError, p.PatchError)):
        return {'code':'invalid_configuration','message':'配置未通过检查，请核对地址、型号、窗口和凭证文件。'}
    return {'code':'operation_failed','message':'操作未完成，现有配置已保留。请运行诊断后重试。'}


def _validate_connection(value):
    import local_support
    connection = local_support.validate(value)
    require(len(connection['id']) <= 100 and len(connection['name']) <= 100, '连接名称过长。')
    for model, spec in connection['model_specs'].items():
        require(isinstance(model,str) and isinstance(spec,dict), '模型规格格式不正确。')
        if 'context_window' in spec:
            require(type(spec['context_window']) is int and 1024 <= spec['context_window'] <= 100_000_000,
                    '请填写模型实际支持的最大上下文。')
        if 'supports_tools' in spec:require(type(spec['supports_tools']) is bool, '工具能力必须为开启或关闭。')
        if 'reasoning_levels' in spec:
            require(isinstance(spec['reasoning_levels'],list) and all(v in ('none','minimal','low','medium','high','xhigh','max','ultra') for v in spec['reasoning_levels']), '思考档位格式不正确。')
    return connection


def perform(directory, action, payload=None, expected=None, cancelled=None):
    directory = Path(directory)
    payload = payload or {}
    require(action in ACTIONS and isinstance(payload,dict), '未知操作。')
    records(directory)  # Every action remains bound to the installed edition.
    if action == 'snapshot':return snapshot(directory)
    if action == 'diagnose':return diagnose(directory,endpoints=payload.get('endpoints') is True)
    if action == 'probe_connection':
        connection=_validate_connection(payload['connection'])
        if not payload.get('key'):return probe_connection(connection,cancelled)
        with tempfile.TemporaryDirectory(prefix='credential-check-',dir=monitor.private_directory(directory/'settings-ui')) as temp:
            path=Path(temp)/'key';private_write(path,payload['key'].encode());connection['key_file']=str(path)
            return probe_connection(connection,cancelled)
    if action == 'compare_models':
        rows = {row['id']:row for row in snapshot(directory)['models']}
        source, target = rows[payload['from']], rows[payload['to']]
        smaller = bool(source['effective'] and target['effective'] and target['effective'] < source['effective'])
        return {'smaller':smaller,'from':source['effective'],'to':target['effective'],
            'message':'目标模型窗口更小，长对话可能需要先压缩；历史恢复使用目标模型的额度。' if smaller else '目标模型的窗口未缩小。',
            'tools':target['tools'],'images':target['images'],'reasoning':target['reasoning']}
    if action == 'show_progress':
        from update_progress import Progress
        progress = Progress(directory,directory/'runtime')
        require(bool(progress.read()),'目前没有进行中的更新。','no_update')
        progress.present(force=True)
        return {'message':'已打开更新进度。'}
    if action in ('check_updates','retry_update'):
        monitor.request_update(directory,sync_now=True,code_now=True,retry=action=='retry_update')
        monitor.wake_monitor(directory)
        return {'message':'检查已排队；可继续使用 Codex。'}
    if action in ('cleanup_preview','cleanup'):
        from updater import prune_update_cache
        return prune_update_cache(directory,dry_run=action=='cleanup_preview')
    settings, build, manifest = records(directory)
    if action == 'cancel_recovery':
        identifier = payload.get('id','')
        require(re.fullmatch(r'[a-f0-9]{32}',identifier),'无效的恢复任务。')
        root = Path(settings['codex_home'])/'model-adapter-operations'
        task = monitor.load_json(root/(identifier+'.json'),{})
        require(task.get('id')==identifier and task.get('phase') not in ('completed','failed','cancelled'), '该恢复任务已结束。')
        monitor.atomic_json(root/(identifier+'.cancel'),{'id':identifier})
        return {'message':'已请求取消历史恢复，对话记录会保留。'}
    if action in ('recover','restore'):
        require(payload.get('confirmed') is True,'请先确认恢复操作。')
        require(not p.active_app_processes(Path(settings['app']),read_only_source=True), '请先正常退出 Codex，再执行恢复。','app_running')
        if action=='restore':
            p.restore(SimpleNamespace(directory=build,state_dir=directory,settings_revision=expected))
        else:
            with edit_lock(directory,expected,cancelled):
                from official_client_install import recover
                recover(build)
        return {'message':'恢复操作已完成。'}
    changed_models = False
    with edit_lock(directory,expected,cancelled):
        settings, build, manifest = records(directory)
        document = connection_document(directory,manifest)
        if action in ('save_connection','remove_connection'):
            require(p.EDITION=='proxy','此功能需要扩展版。','edition_feature')
            if action=='save_connection':
                connection = _validate_connection(payload['connection'])
                identifier = connection['id']
            else:identifier = payload['id']
            document['locals'] = [row for row in document.get('locals',[]) if row['id']!=identifier]
            key_path=None;previous_key=None
            if action=='save_connection':
                if payload.get('key'):
                    key=payload['key'];require(isinstance(key,str) and 0<len(key)<=8192 and not any(ord(c)<33 for c in key),'API Key 格式不正确。')
                    key_path=directory/'credentials'/(identifier+'.key')
                    require(not key_path.is_symlink(),'凭证路径无效。')
                    previous_key=key_path.read_bytes() if key_path.exists() else None
                    private_write(key_path,key.encode());connection['key_file']=str(key_path)
                document['locals'].append(connection)
            try:monitor.atomic_json(directory/'connections.json',document)
            except Exception:
                if key_path is not None:
                    if previous_key is None:key_path.unlink(missing_ok=True)
                    else:private_write(key_path,previous_key)
                raise
            changed_models=True
        elif action=='save_proxy':
            require(p.EDITION=='proxy','此功能需要扩展版。','edition_feature')
            from proxy_support import validate
            value = payload.get('proxy')
            if value is None and document.get('proxy'):
                document['disabled_proxy'] = copy.deepcopy(document['proxy'])
            elif value is not None:
                document.pop('disabled_proxy',None)
            document['proxy'] = validate(value) if value is not None else None
            require(type(payload.get('auto_add',True)) is bool,'自动加入设置无效。')
            document['proxy_auto_add'] = payload.get('auto_add',True)
            document['proxy_selection_id'] = uuid.uuid4().hex
            monitor.atomic_json(directory/'connections.json',document);changed_models=True
        elif action=='save_deepseek':
            enabled = payload.get('enabled')
            require(type(enabled) is bool and (enabled or p.EDITION=='proxy'),'DS 版需要启用 DeepSeek。')
            secret = payload.get('key')
            if secret:
                require(isinstance(secret,str) and len(secret)<=2560 and not any(ord(c)<33 for c in secret),'API Key 格式不正确。')
                save_deepseek_key(settings,manifest,secret)
            document['deepseek_enabled'] = enabled
            monitor.atomic_json(directory/'connections.json',document);changed_models=True
        elif action=='save_model':
            import model_sync
            models = {row['id']:row for row in _models(directory,build,manifest)}
            model = payload['id'];require(model in models,'模型已变更，请刷新列表。')
            value = model_sync.validate_preferences(monitor.load_json(directory/'model-preferences.json',{}))
            hidden = set(value.get('hidden',[]))
            if 'visible' in payload:
                require(type(payload['visible']) is bool,'显示设置无效。')
                hidden.discard(model) if payload['visible'] else hidden.add(model)
            value['hidden'] = sorted(hidden)
            for field, key in [('context','context_windows'),('compact','compact_limits')]:
                if field not in payload:continue
                number=payload[field];maximum=models[model]['maximum']
                require(number is None or type(number) is int and maximum and 0<number<=maximum,'窗口必须为正整数且不超过模型最大值。')
                value.setdefault(key,{})[model]=number
            requested=value.get('context_windows',{}).get(model) or models[model]['maximum']
            compact=value.get('compact_limits',{}).get(model)
            require(compact is None or requested and compact<=int(requested*.9),'压缩阈值不能超过使用窗口的 90%。')
            monitor.atomic_json(directory/'model-preferences.json',model_sync.validate_preferences(value));changed_models=True
        elif action=='save_options':
            require(set(payload)<= {'auto_update','model_sync','interval','progress_ui'},'存在未知设置。')
            for key,value in payload.items():
                require(type(value) is int and 60<=value<=86400 if key=='interval' else type(value) is bool,'检查周期或开关格式无效。')
            previous=dict(settings);settings.update(payload);monitor.atomic_json(directory/'settings.json',settings)
            try:
                if settings['interval']!=previous['interval']:monitor.Scheduler(directory).enable(settings['interval'])
            except Exception:
                monitor.atomic_json(directory/'settings.json',previous)
                raise
        elif action=='save_images':
            from cpa_images import configure
            require(type(payload.get('enabled')) is bool,'图片工具开关无效。')
            proxy=document.get('proxy') or {}
            configure(SimpleNamespace(codex_home=Path(settings['codex_home']),disable=not payload['enabled'],
                proxy_config=payload.get('config') or proxy.get('config'),
                proxy_base_url=payload.get('url') or proxy.get('base_url','http://127.0.0.1:8317/v1'),
                script=directory/'runtime/cpa_images.py'))
        else:raise SettingsError('unknown_action','未知操作。')
    if changed_models:_queue(directory)
    return {'message':'已保存；模型变更将在验证通过、Codex 退出后应用。' if changed_models else '设置已保存。'}


def save_deepseek_key(settings, manifest, secret):
    service = manifest.get('keychain_service') or settings.get('keychain_service') or p.KEYCHAIN_SERVICE
    account = manifest.get('keychain_account') or settings.get('keychain_account') or __import__('getpass').getuser()
    if sys.platform=='win32':
        from windows_support import credential_set
        credential_set(service,account,secret)
        return
    require(sys.platform=='darwin','此系统不支持凭证保存。')
    import ctypes as c
    cf=c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    sec=c.CDLL('/System/Library/Frameworks/Security.framework/Security')
    cf.CFStringCreateWithCString.argtypes=[c.c_void_p,c.c_char_p,c.c_uint32];cf.CFStringCreateWithCString.restype=c.c_void_p
    cf.CFDataCreate.argtypes=[c.c_void_p,c.c_void_p,c.c_long];cf.CFDataCreate.restype=c.c_void_p
    cf.CFDictionaryCreateMutable.argtypes=[c.c_void_p,c.c_long,c.c_void_p,c.c_void_p];cf.CFDictionaryCreateMutable.restype=c.c_void_p
    cf.CFDictionarySetValue.argtypes=[c.c_void_p,c.c_void_p,c.c_void_p];cf.CFRelease.argtypes=[c.c_void_p]
    sec.SecItemUpdate.argtypes=[c.c_void_p,c.c_void_p];sec.SecItemUpdate.restype=c.c_int32
    sec.SecItemAdd.argtypes=[c.c_void_p,c.c_void_p];sec.SecItemAdd.restype=c.c_int32
    keys=c.addressof(c.c_char.in_dll(cf,'kCFTypeDictionaryKeyCallBacks'))
    values=c.addressof(c.c_char.in_dll(cf,'kCFTypeDictionaryValueCallBacks'))
    allocated=[]
    def const(name):return c.c_void_p.in_dll(sec,name).value
    def string(value):
        result=cf.CFStringCreateWithCString(None,value.encode(),0x08000100);allocated.append(result);return result
    def dictionary():
        result=cf.CFDictionaryCreateMutable(None,0,keys,values);allocated.append(result);return result
    try:
        query=dictionary();attrs=dictionary()
        cf.CFDictionarySetValue(query,const('kSecClass'),const('kSecClassGenericPassword'))
        cf.CFDictionarySetValue(query,const('kSecAttrService'),string(service))
        cf.CFDictionarySetValue(query,const('kSecAttrAccount'),string(account))
        raw=secret.encode();data=cf.CFDataCreate(None,c.c_char_p(raw),len(raw));allocated.append(data)
        cf.CFDictionarySetValue(attrs,const('kSecValueData'),data)
        status=sec.SecItemUpdate(query,attrs)
        if status==-25300:
            cf.CFDictionarySetValue(query,const('kSecValueData'),data);status=sec.SecItemAdd(query,None)
        require(status==0,'系统未能保存凭证，请确认钥匙串可用后重试。','credential_save_failed')
    finally:
        for value in reversed(allocated):
            if value:cf.CFRelease(value)


def run_request(directory, identifier):
    require(re.fullmatch(r'[a-f0-9]{32}',identifier),'无效的操作编号。')
    jobs=monitor.private_directory(Path(directory)/'settings-ui/jobs')
    request=jobs/(identifier+'.request.json');result=jobs/(identifier+'.result.json')
    cancelled=lambda:(jobs/(identifier+'.cancel')).exists()
    monitor.atomic_json(result,{'status':'running','message':'正在处理，可继续查看其他设置。'})
    try:
        require(request.is_file() and not request.is_symlink() and request.stat().st_size<=131072,'无效的设置请求。')
        try:value=json.loads(request.read_text(encoding='utf-8'))
        finally:request.unlink(missing_ok=True)  # Remove password input even if JSON was truncated.
        require(isinstance(value,dict),'无效的设置请求。')
        require(value.get('action') in ACTIONS,'未知设置操作。')
        data=perform(directory,value['action'],value.get('payload'),value.get('revision'),cancelled)
        response={'status':'completed','data':data}
        if value['action'] not in ('snapshot','probe_connection','diagnose','compare_models','restore'):
            response['snapshot']=snapshot(directory)
    except Exception as error:response={'status':'failed','error':public_error(error)}
    finally:
        if request.is_file() and not request.is_symlink():request.unlink(missing_ok=True)
    monitor.atomic_json(result,response)
    return response


def show(directory, *, self_test=False):
    from official_client_host import windowless_python
    directory=Path(directory).resolve();ui=monitor.private_directory(directory/'settings-ui')
    monitor.private_directory(ui/'jobs')
    with monitor.state_lock(ui) as locked:
        if not locked:
            monitor.atomic_json(ui/'raise.json',{'time':uuid.uuid4().hex})
            return subprocess.CompletedProcess([],0,b'',b'')
        monitor.atomic_json(ui/'view.json',snapshot(directory))
        runtime=windowless_python(sys.executable)
        if sys.platform=='darwin':
            command=['/usr/bin/osascript','-l','JavaScript',str(ROOT/'settings_macos.js'),str(directory),str(runtime),str(ROOT/'management.py')]
        elif sys.platform=='win32':
            from windows_support import native_ui_command
            command=native_ui_command(ROOT/'settings_windows.ps1',{'StateDirectory':str(directory),
                'PythonExecutable':str(runtime),'ControllerPath':str(ROOT/'management.py'),'SelfTest':self_test})
        else:raise SettingsError('unsupported_platform','设置窗口支持 macOS 和 Windows。')
        if self_test and sys.platform=='darwin':command.append('--self-test')
        if self_test and sys.platform=='win32':
            from windows_support import isolated_desktop_options, desktop_process
            import shutil
            command[0]=shutil.which(command[0]) or command[0]
            log_path=ui/'native-selftest.log'
            with isolated_desktop_options() as options, log_path.open('wb') as log:
                child=desktop_process(command,options['desktop'],cwd=directory,stdout=log,
                    env={**os.environ,'CODEX_SETTINGS_PRIVATE_DESKTOP':'1'})
                try:code=child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait(timeout=5)
                    raise
            raw=log_path.read_bytes()
            return subprocess.CompletedProcess(command,code,raw if code==0 else b'',raw if code else b'')
        return subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=30 if self_test else None,
                              **(__import__('windows_support').background_options() if os.name=='nt' else {}))


def ensure_launcher(directory):
    """Create one per-user entry; no change to the official Codex shortcut/app."""
    from official_client_host import windowless_python
    directory=Path(directory).resolve()
    settings, _, manifest=records(directory)
    runtime=directory/'runtime'
    require((runtime/'management.py').is_file(), '设置组件尚未安装。')
    ui=monitor.private_directory(directory/'settings-ui')
    owned=monitor.load_json(ui/'launcher.json',{})
    python=str(windowless_python(settings.get('python_path') or sys.executable))
    command=[python,str(runtime/'management.py'),'show','--state-dir',str(directory)]
    label='Codex 模型设置（'+('DS 版' if p.EDITION=='ds' else '扩展版')+'）'
    if sys.platform=='darwin':
        destination=Path.home()/'Applications'/(label+'.app')
        bundle='com.limyum.codex-model-settings.'+p.EDITION
        files={'Contents/MacOS/settings':('#!/bin/sh\nexec '+shlex.join(command)+'\n').encode(),
            'Contents/Info.plist':plistlib.dumps({'CFBundleIdentifier':bundle,'CFBundleName':label,
                'CFBundleDisplayName':label,'CFBundleExecutable':'settings','CFBundlePackageType':'APPL',
                'LSUIElement':True,'NSHighResolutionCapable':True})}
    elif sys.platform=='win32':
        destination=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs'/(label+'.lnk')
        files=None
    else:raise SettingsError('unsupported_platform','设置入口支持 macOS 和 Windows。')
    require(not destination.is_symlink(),'设置入口路径已被占用。')
    if destination.exists():
        require(owned.get('path')==str(destination),'已有同名入口，请保留并手动调整名称。')
        for name,digest in owned.get('files',{}).items():
            path=destination/name if destination.is_dir() else destination
            require(path.is_file() and not path.is_symlink() and p.file_sha(path)==digest,'设置入口已被用户修改，已保留。')
        if owned.get('command')==command:return str(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    if files is not None:
        for name,raw in files.items():
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            private_write(target,raw)
            target.chmod(0o755 if name.endswith('/settings') else 0o644)
        hashes={name:p.sha(raw) for name,raw in files.items()}
    else:
        from official_client_shortcut import write_link, _atomic
        staged=ui/'settings-entry.lnk'
        write_link(staged,{'target':python,'arguments':subprocess.list2cmdline(command[1:]),
            'working_directory':str(directory),'icon':str(Path(settings['app'])/'ChatGPT.exe')+',0'})
        _atomic(destination,staged.read_bytes());staged.unlink()
        hashes={'':p.file_sha(destination)}
    monitor.atomic_json(ui/'launcher.json',{'format':1,'edition':p.EDITION,'path':str(destination),'command':command,'files':hashes})
    return str(destination)


def maintenance(directory):
    """Reuse an idle monitor tick for bounded cleanup and a silent UI entry."""
    directory=Path(directory)
    if not (directory/'runtime/management.py').is_file():return
    ui=monitor.private_directory(directory/'settings-ui')
    previous=monitor.load_json(ui/'maintenance.json',{})
    if time.time()-previous.get('checked_at',0)<86400:return
    with monitor.state_lock(ui/'maintenance') as locked:
        if not locked:return
        outcome={'checked_at':time.time()}
        try:outcome['launcher']=ensure_launcher(directory)
        except Exception as error:outcome['launcher_error']=public_error(error)['code']
        try:
            from updater import prune_update_cache
            outcome['cleanup']=prune_update_cache(directory,dry_run=False)
        except Exception as error:outcome['cleanup_error']=public_error(error)['code']
        monitor.atomic_json(ui/'maintenance.json',outcome)


def remove_launcher(directory):
    """Remove only the exact generated entry, preserving any independent edits."""
    document=Path(directory)/'settings-ui/launcher.json'
    owned=monitor.load_json(document,{})
    if owned.get('format')!=1 or owned.get('edition')!=p.EDITION:return False
    path=Path(owned['path'])
    if path.is_symlink():return False
    if not path.exists():document.unlink(missing_ok=True);return True
    files=owned.get('files',{})
    if not files:return False
    if path.is_dir():
        existing={str(f.relative_to(path)) for f in path.rglob('*') if f.is_file() or f.is_symlink()}
        if existing!=set(files):return False
    for name,digest in files.items():
        item=path/name if path.is_dir() else path
        if not item.is_file() or item.is_symlink() or p.file_sha(item)!=digest:return False
    if path.is_dir():__import__('shutil').rmtree(path)
    else:path.unlink()
    document.unlink(missing_ok=True)
    return True


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('show','execute','diagnose'))
    parser.add_argument('--state-dir',type=Path)
    parser.add_argument('--request')
    parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args(argv);directory=find_directory(args.state_dir)
    try:
        if args.action=='execute':run_request(directory,args.request)
        elif args.action=='diagnose':print(json.dumps(diagnose(directory,endpoints=True),ensure_ascii=False,indent=2))
        else:
            result=show(directory,self_test=args.self_test)
            if args.self_test:print(result.stdout.decode('utf-8',errors='replace'))
            return result.returncode
        return 0
    except Exception as error:
        print(json.dumps(public_error(error),ensure_ascii=False),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
