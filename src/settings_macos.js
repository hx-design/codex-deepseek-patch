/* Disposable AppKit settings window. Fixed actions, private JSON transport. */
ObjC.import('AppKit');
ObjC.import('Foundation');
function run(argv) {
    const directory=argv[0], python=argv[1], controller=argv[2], testing=argv.indexOf('--self-test')>=0;
    for(const value of [directory,python,controller]) if(!value || value[0]!=='/' || /[\x00\r\n]/.test(value)) throw Error('Absolute paths required');
    const ui=directory+'/settings-ui', jobs=ui+'/jobs';
    const app=$.NSApplication.sharedApplication;app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
    let state=read(ui+'/view.json'), pending=null, closing=false, filtered=[], selected=null, report='', onResult=null, lastRaise='',lastRecoveryPoll=0;
    const actions={}, labels={}, fields={};let actionNumber=10;
    function read(path){try{const text=$.NSString.stringWithContentsOfFileEncodingError($(path),$.NSUTF8StringEncoding,null);return text?JSON.parse(ObjC.unwrap(text)):null;}catch(_){return null;}}
    function write(path,value){$(JSON.stringify(value)).writeToFileAtomicallyEncodingError($(path),true,$.NSUTF8StringEncoding,null);$.NSFileManager.defaultManager.setAttributesOfItemAtPathError($({NSFilePosixPermissions:384}),$(path),null);}
    function text(control){return ObjC.unwrap(control.stringValue);}
    function number(control){const value=text(control).trim();if(value==='')return null;const parsed=Number(value);if(!Number.isSafeInteger(parsed)||parsed<1)throw Error('请输入正整数');return parsed;}
    function checked(control){return Number(control.state)===1;}
    function human(value){return value?Number(value).toLocaleString('en-US')+' tokens':'未提供';}
    function message(title,detail){const alert=$.NSAlert.alloc.init;alert.messageText=title;alert.informativeText=detail;alert.addButtonWithTitle('知道了');if(!testing)alert.runModal;}
    function confirm(title,detail){if(testing)return false;const alert=$.NSAlert.alloc.init;alert.messageText=title;alert.informativeText=detail;alert.addButtonWithTitle('继续');alert.addButtonWithTitle('取消');return Number(alert.runModal)===1000;}
    function bind(control,callback){const tag=actionNumber++;actions[tag]=callback;control.tag=tag;control.target=receiver;control.action='clicked:';return control;}
    ObjC.registerSubclass({name:'CodexModelSettingsController',superclass:'NSObject',protocols:['NSWindowDelegate','NSTableViewDataSource','NSTableViewDelegate'],methods:{
        'clicked:':{types:['void',['id']],implementation:function(sender){try{const fn=actions[Number(sender.tag)];if(fn)fn();}catch(_){banner.stringValue='操作未完成，请检查填写内容。';}}},
        'poll:':{types:['void',['id']],implementation:function(){poll();}},
        'windowWillClose:':{types:['void',['id']],implementation:function(){closing=true;if(!testing)app.terminate(null);}},
        'numberOfRowsInTableView:':{types:['long',['id']],implementation:function(){return filtered.length;}},
        'tableView:objectValueForTableColumn:row:':{types:['id',['id','id','long']],implementation:function(t,c,i){const row=filtered[Number(i)];if(!row)return $('');const k=ObjC.unwrap(c.identifier);return $(String(k==='name'?row.name:k==='window'?human(row.effective):row.visible?'显示':'隐藏'));}},
        'tableViewSelectionDidChange:':{types:['void',['id']],implementation:function(){selectModel();}}
    }});
    const receiver=$.CodexModelSettingsController.alloc.init;
    const window=$.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer($.NSMakeRect(0,0,900,660),$.NSWindowStyleMaskTitled|$.NSWindowStyleMaskClosable|$.NSWindowStyleMaskMiniaturizable,$.NSBackingStoreBuffered,false);
    window.title='Codex 模型设置';window.delegate=receiver;window.releasedWhenClosed=false;window.center;
    const content=window.contentView;
    function label(parent,x,y,w,h,value,size,bold){const c=$.NSTextField.alloc.initWithFrame($.NSMakeRect(x,y,w,h));c.stringValue=String(value||'');c.editable=false;c.selectable=false;c.bezeled=false;c.drawsBackground=false;c.font=bold?$.NSFont.boldSystemFontOfSize(size||13):$.NSFont.systemFontOfSize(size||13);c.lineBreakMode=$.NSLineBreakByWordWrapping;parent.addSubview(c);return c;}
    function input(parent,x,y,w,value,placeholder,secure){const c=secure?$.NSSecureTextField.alloc.initWithFrame($.NSMakeRect(x,y,w,26)):$.NSTextField.alloc.initWithFrame($.NSMakeRect(x,y,w,26));c.stringValue=String(value||'');if(placeholder)c.placeholderString=placeholder;c.font=$.NSFont.systemFontOfSize(13);parent.addSubview(c);return c;}
    function button(parent,x,y,w,title,callback){const c=$.NSButton.alloc.initWithFrame($.NSMakeRect(x,y,w,32));c.title=title;c.bezelStyle=$.NSBezelStyleRounded;parent.addSubview(c);return bind(c,callback);}
    function toggle(parent,x,y,w,title,value,callback){const c=$.NSButton.alloc.initWithFrame($.NSMakeRect(x,y,w,25));c.setButtonType($.NSSwitchButton);c.title=title;c.state=value?1:0;parent.addSubview(c);if(callback)bind(c,callback);return c;}
    function popup(parent,x,y,w,items,index){const c=$.NSPopUpButton.alloc.initWithFramePullsDown($.NSMakeRect(x,y,w,28),false);c.addItemsWithTitles($(items));c.selectItemAtIndex(index||0);parent.addSubview(c);return c;}
    function area(parent,x,y,w,h){const scroll=$.NSScrollView.alloc.initWithFrame($.NSMakeRect(x,y,w,h));scroll.hasVerticalScroller=true;scroll.borderType=$.NSBezelBorder;const view=$.NSTextView.alloc.initWithFrame($.NSMakeRect(0,0,w,h));view.editable=false;view.selectable=true;view.font=$.NSFont.systemFontOfSize(13);view.textContainerInset=$.NSMakeSize(12,12);scroll.documentView=view;parent.addSubview(scroll);return view;}
    label(content,26,602,500,35,'Codex 模型设置',23,true);
    labels.subtitle=label(content,28,577,830,24,'管理连接、模型与自动维护',12,false);labels.subtitle.textColor=$.NSColor.secondaryLabelColor;
    const tabs=$.NSTabView.alloc.initWithFrame($.NSMakeRect(22,58,856,510));content.addSubview(tabs);
    function page(title){const item=$.NSTabViewItem.alloc.initWithIdentifier($(title));item.label=title;const view=$.NSView.alloc.initWithFrame($.NSMakeRect(0,0,828,472));item.view=view;tabs.addTabViewItem(item);return view;}
    const overview=page('概览'),models=page('模型与连接'),maintenance=page('更新与维护');
    const banner=label(content,28,16,630,30,'',12,false);banner.textColor=$.NSColor.secondaryLabelColor;
    button(content,672,16,108,'取消操作',()=>{if(pending){write(jobs+'/'+pending.id+'.cancel',{id:pending.id});banner.stringValue='已请求取消；正在保存的操作会先安全完成。';}});
    button(content,786,16,90,'刷新',()=>send('snapshot',{},data=>render(data)));
    labels.health=label(overview,22,411,780,36,'',20,true);
    labels.summary=label(overview,22,347,780,56,'',13,false);
    button(overview,18,292,130,'检查更新',()=>send('check_updates'));
    button(overview,154,292,130,'运行诊断',()=>send('diagnose',{endpoints:true},showReport));
    button(overview,290,292,130,'更新进度',()=>send('show_progress'));
    button(overview,426,292,130,'复制诊断',()=>{if(!report)return message('尚无诊断报告','先运行一次诊断。');const paste=$.NSPasteboard.generalPasteboard;paste.clearContents;paste.setStringForType($(report),$.NSPasteboardTypeString);banner.stringValue='已复制脱敏诊断报告。';});
    labels.report=area(overview,22,36,780,238);
    fields.search=input(models,20,421,240,'','搜索模型');button(models,268,420,80,'筛选',filterModels);
    const scroll=$.NSScrollView.alloc.initWithFrame($.NSMakeRect(20,145,470,263));scroll.hasVerticalScroller=true;scroll.borderType=$.NSBezelBorder;
    const table=$.NSTableView.alloc.initWithFrame($.NSMakeRect(0,0,470,263));table.rowHeight=29;table.usesAlternatingRowBackgroundColors=true;
    for(const row of [['name','模型',260],['window','使用窗口',130],['visible','状态',60]]){const col=$.NSTableColumn.alloc.initWithIdentifier($(row[0]));col.title=row[1];col.width=row[2];table.addTableColumn(col);}table.dataSource=receiver;table.delegate=receiver;scroll.documentView=table;models.addSubview(scroll);
    labels.model=label(models,512,411,288,32,'选择一个模型',17,true);
    labels.capabilities=label(models,512,345,288,64,'',12,false);
    labels.maximum=label(models,512,307,288,34,'',12,false);
    label(models,512,277,100,22,'使用窗口',12,false);fields.context=input(models,612,274,184,'','自动按最大值');
    label(models,512,239,100,22,'压缩阈值',12,false);fields.compact=input(models,612,236,184,'','自动：窗口的 90%');
    fields.visible=toggle(models,510,195,286,'在 Codex 模型菜单中显示',true);
    button(models,506,145,130,'恢复默认',()=>{fields.context.stringValue='';fields.compact.stringValue='';saveModel();});
    button(models,644,145,154,'保存模型设置',saveModel);
    label(models,22,104,780,28,'连接的变更会先验证，退出 Codex 后再应用；正在使用的对话保持原样。',12,false);
    if(state.edition==='proxy'){
        button(models,16,51,126,'添加接口',()=>editConnection(null));
        button(models,148,51,126,'管理连接',manageConnections);
        button(models,280,51,142,'现成代理',editProxy);
    }
    button(models,state.edition==='proxy'?428:16,51,138,'DeepSeek',editDeepSeek);
    button(models,state.edition==='proxy'?572:162,51,142,'切换前比较',compareModels);
    fields.code=toggle(maintenance,22,414,365,'自动检查补丁更新',true);
    fields.sync=toggle(maintenance,22,378,365,'自动同步模型与规格',true);
    fields.progress=toggle(maintenance,22,342,365,'发现补丁更新时显示进度',true);
    label(maintenance,430,415,125,22,'检查间隔（分钟）',12,false);fields.interval=input(maintenance,574,412,180,'5','1–1440');
    button(maintenance,570,365,188,'保存维护设置',()=>send('save_options',{auto_update:checked(fields.code),model_sync:checked(fields.sync),progress_ui:checked(fields.progress),interval:number(fields.interval)*60}));
    label(maintenance,22,288,740,26,'常用维护',15,true);
    button(maintenance,16,245,144,'立即检查',()=>send('check_updates'));
    button(maintenance,166,245,144,'重新尝试更新',()=>send('retry_update'));
    button(maintenance,316,245,144,'清理旧缓存',cleanup);
    button(maintenance,466,245,144,'图片工具',editImages);
    labels.recovery=label(maintenance,22,155,770,75,'',12,false);
    button(maintenance,16,119,178,'取消当前历史恢复',cancelRecovery);
    const advanced=toggle(maintenance,22,70,500,'高级维护',false,()=>{recoverButton.hidden=!checked(advanced);restoreButton.hidden=!checked(advanced);});
    const recoverButton=button(maintenance,16,24,190,'恢复中断的安装',()=>recover('recover'));recoverButton.hidden=true;
    const restoreButton=button(maintenance,214,24,190,'撤销适配层',()=>recover('restore'));restoreButton.hidden=true;
    button(maintenance,622,24,140,'使用说明',()=>$.NSWorkspace.sharedWorkspace.openURL($.NSURL.URLWithString($(state.help_url))));
    function filterModels(){const query=text(fields.search).toLowerCase();filtered=state.models.filter(v=>(v.name+' '+v.id+' '+v.provider).toLowerCase().indexOf(query)>=0);table.reloadData;if(filtered.length)table.selectRowIndexesByExtendingSelection($.NSIndexSet.indexSetWithIndex(0),false);else{selected=null;labels.model.stringValue='没有匹配的模型';}selectModel();}
    function selectModel(){const index=Number(table.selectedRow);selected=filtered[index]||null;if(!selected)return;labels.model.stringValue=selected.name;labels.capabilities.stringValue='来源：'+selected.provider+'\n工具 '+(selected.tools?'支持':'不支持')+' · 图片 '+(selected.images?'支持':'不支持')+'\n思考档位：'+(selected.reasoning.join(' / ')||'服务未提供');labels.maximum.stringValue='模型最大：'+human(selected.maximum)+(selected.server_window?'\n服务加载：'+human(selected.server_window):'');fields.context.stringValue=selected.context_override?String(selected.context_override):'';fields.compact.stringValue=selected.compact_override?String(selected.compact_override):'';fields.visible.state=selected.visible?1:0;}
    function saveModel(){if(selected)send('save_model',{id:selected.id,context:number(fields.context),compact:number(fields.compact),visible:checked(fields.visible)});}
    function render(value){state=value;labels.subtitle.stringValue=value.edition_label+' · '+value.version+'     官方 Codex '+value.app_version;labels.health.stringValue=value.status;labels.summary.stringValue='已显示 '+value.models.filter(v=>v.visible).length+' 个模型 · '+value.connections.length+' 个自定义连接\n'+(value.queued?'变更已排队，正在等待后台处理。':'模型目录、登录与工具由官方 Codex 承载。');fields.code.state=value.options.auto_update?1:0;fields.sync.state=value.options.model_sync?1:0;fields.progress.state=value.options.progress_ui?1:0;fields.interval.stringValue=String(value.options.interval/60);filterModels();renderRecovery();if(!report)labels.report.string='可以在这里查看更新状态、运行诊断并复制反馈信息。\n\n设置窗口关闭后退出，不会增加常驻管理进程。';}
    function renderRecovery(){const rows=state.recovery||[];labels.recovery.stringValue=rows.length?rows.slice(0,2).map(v=>v.model+' · '+({recovering:'正在恢复',summarizing:'正在整理摘要',completed:'已完成',failed:'未完成',cancelled:'已取消'}[v.phase]||v.phase)+(v.total?' · '+v.part+'/'+v.total:'')+(v.cache?' · 使用摘要缓存':'')).join('\n'):'当前没有历史恢复任务。\n长历史恢复由当前模型处理；可在此查看进度和取消。';}
    function pollRecovery(){if(Date.now()-lastRecoveryPoll<1000||!state.recovery_directory)return;lastRecoveryPoll=Date.now();try{const files=ObjC.deepUnwrap($.NSFileManager.defaultManager.contentsOfDirectoryAtPathError($(state.recovery_directory),null))||[];state.recovery=files.filter(v=>/^[a-f0-9]{32}\.json$/.test(v)).map(v=>read(state.recovery_directory+'/'+v)).filter(v=>v&&v.format===1&&Date.now()/1000-v.updated_at<3600).sort((a,b)=>b.updated_at-a.updated_at).slice(0,20);renderRecovery();}catch(_){}}
    function showReport(value){report=value.text;labels.report.string=report;tabs.selectTabViewItemAtIndex(0);}
    function send(action,payload,callback){if(pending)return message('有操作正在进行','请等待当前操作完成。关闭设置窗口不会取消已经提交的操作。');const id=ObjC.unwrap($.NSUUID.UUID.UUIDString).replace(/-/g,'').toLowerCase();write(jobs+'/'+id+'.request.json',{action:action,payload:payload||{},revision:state.revision});const task=$.NSTask.alloc.init;task.launchPath=$(python);task.arguments=$([controller,'execute','--state-dir',directory,'--request',id]);task.standardOutput=$.NSFileHandle.fileHandleForWritingAtPath('/dev/null');task.standardError=task.standardOutput;task.launch;pending={id:id,task:task};onResult=callback||null;banner.stringValue='正在处理…';if(testing){task.waitUntilExit;poll();}}
    function poll(){pollRecovery();const raised=read(ui+'/raise.json');if(raised && String(raised.time)!==lastRaise){lastRaise=String(raised.time);window.makeKeyAndOrderFront(null);app.activateIgnoringOtherApps(true);}if(!pending)return;const value=read(jobs+'/'+pending.id+'.result.json');if(!value || value.status==='running'){if(!pending.task.running && (!value||value.status==='running')){pending=null;banner.stringValue='操作未完成，请刷新后重试。';}return;}pending=null;const callback=onResult;onResult=null;if(value.status==='failed'){banner.stringValue=value.error.message;return;}if(value.snapshot)render(value.snapshot);banner.stringValue=value.data.message||'操作完成。';if(callback)callback(value.data);}
    function dialog(title,rows,buttons){const alert=$.NSAlert.alloc.init;alert.messageText=title;alert.informativeText='';for(const title of buttons)alert.addButtonWithTitle(title);const height=rows.length*45+8;const view=$.NSView.alloc.initWithFrame($.NSMakeRect(0,0,520,height));const result={};rows.forEach((row,i)=>{const y=height-43-i*45;label(view,0,y+3,138,24,row[1],12,false);if(row[3]==='check')result[row[0]]=toggle(view,145,y,365,'',!!row[2]);else if(Array.isArray(row[3]))result[row[0]]=popup(view,145,y,365,row[3],row[2]);else {result[row[0]]=input(view,145,y,row[3]==='file'?315:365,row[2],row[4]||'',row[3]==='secret');if(row[3]==='file')button(view,466,y-2,44,'…',()=>{const picker=$.NSOpenPanel.openPanel;picker.canChooseFiles=true;picker.canChooseDirectories=false;picker.allowsMultipleSelection=false;if(Number(picker.runModal)===1)result[row[0]].stringValue=picker.URL.path;});}});alert.accessoryView=view;const answer=testing?buttons.length-1:Number(alert.runModal)-1000;return {answer:answer,controls:result};}
    function editConnection(old){if(state.edition!=='proxy')return message('扩展版功能','DS 版只管理 DeepSeek；其他接口可通过扩展版接入。');const row=old||{id:'local-'+ObjC.unwrap($.NSUUID.UUID.UUIDString).slice(0,8).toLowerCase(),name:'',kind:'openai',base_url:'',wire_api:'chat_completions',models:[],model_specs:{}};const d=dialog(old?'编辑连接':'添加模型接口',[
        ['name','连接名称',row.name,null,'例如：公司本地模型'],['kind','服务类型',['openai','ollama','lmstudio'].indexOf(row.kind),['通用兼容接口','Ollama','LM Studio']],['url','接口地址',row.base_url,null,'http://127.0.0.1:11434/v1'],['key','API Key','','secret','留空保留已有凭证'],['wire','接口协议',row.wire_api==='responses'?0:1,['Responses','Chat Completions']],['models','使用的模型',row.models.join(', '),null,'留空发现全部；多个型号用逗号分隔'],['respect','沿用服务窗口',row.respect_server_context,'check']],['检测并保存','保存','取消']);if(d.answer===2)return;const c=d.controls;const connection=Object.assign({},row,{name:text(c.name).trim(),kind:['openai','ollama','lmstudio'][Number(c.kind.indexOfSelectedItem)],base_url:text(c.url).trim(),wire_api:Number(c.wire.indexOfSelectedItem)===0?'responses':'chat_completions',models:text(c.models).split(/[,，\n]/).map(v=>v.trim()).filter(Boolean),respect_server_context:checked(c.respect)});const key=text(c.key);c.key.stringValue='';if(d.answer===1)return send('save_connection',{connection:connection,key:key});send('probe_connection',{connection:connection,key:key},result=>{const selected=chooseDiscovered(result,connection);if(selected)send('save_connection',{connection:selected,key:key});});}
    function chooseDiscovered(result,connection){
        if(!result.available.length){message('暂无模型','接口可连接，但没有返回可用型号。');return null;}
        const alert=$.NSAlert.alloc.init;alert.messageText='选择加入 Codex 的模型';alert.informativeText='勾选常用型号；服务缺少规格时再补充最大窗口。';alert.addButtonWithTitle('保存所选模型');alert.addButtonWithTitle('取消');
        const scroll=$.NSScrollView.alloc.initWithFrame($.NSMakeRect(0,0,540,300));scroll.hasVerticalScroller=true;const view=$.NSView.alloc.initWithFrame($.NSMakeRect(0,0,520,Math.max(300,result.available.length*30)));scroll.documentView=view;const boxes=[];
        result.available.forEach((id,i)=>boxes.push(toggle(view,8,Number(view.frame.size.height)-30-i*30,500,id,connection.models.length?connection.models.indexOf(id)>=0:true)));
        const panel=$.NSView.alloc.initWithFrame($.NSMakeRect(0,0,540,342));panel.addSubview(scroll);button(panel,0,306,90,'全选',()=>boxes.forEach(v=>v.state=1));button(panel,94,306,90,'清空',()=>boxes.forEach(v=>v.state=0));alert.accessoryView=panel;if(testing||Number(alert.runModal)!==1000)return null;
        connection.models=result.available.filter((id,i)=>checked(boxes[i]));if(!connection.models.length){message('尚未选择模型','至少选择一个型号后再保存。');return null;}
        for(const id of connection.models){if(result.specs[id])continue;const d=dialog('补充 '+id+' 的规格',[['maximum','模型最大上下文','',null,'填写该型号支持的 token 数'],['tools','支持工具调用',true,'check']],['继续','取消']);if(d.answer)return null;const maximum=number(d.controls.maximum);if(!Number.isInteger(maximum)||maximum<1024){message('窗口数值无效','请填写至少 1024 的整数。');return null;}connection.model_specs=Object.assign({},connection.model_specs||{});connection.model_specs[id]=Object.assign({},connection.model_specs[id]||{},{context_window:maximum,supports_tools:checked(d.controls.tools)});}
        return connection;
    }
    function manageConnections(){if(!state.connections.length)return editConnection(null);const d=dialog('管理已有连接',[['connection','选择连接',0,state.connections.map(v=>v.name)]],['编辑','移除','高级选项','取消']);const row=state.connections[Number(d.controls.connection.indexOfSelectedItem)];if(d.answer===0)editConnection(row);else if(d.answer===1 && confirm('移除连接','会从模型目录移除此连接；服务、凭证文件和聊天记录保留。'))send('remove_connection',{id:row.id});else if(d.answer===2)advancedConnection(row);}
    function advancedConnection(row){const d=dialog('连接高级选项',[['keyfile','凭证文件',row.key_file||'','file','绝对路径；留空不使用文件'],['reasoning','支持思考参数',row.supports_reasoning_effort,'check'],['model','补充规格的型号','',null,'填写一个准确的模型 ID'],['maximum','模型最大窗口','',null,'仅用于服务未提供规格的情况'],['levels','思考档位','',null,'例如 low, medium, high'],['tools','支持工具调用',true,'check']],['保存','取消']);if(d.answer)return;const c=d.controls,connection=Object.assign({},row);connection.supports_reasoning_effort=checked(c.reasoning);if(text(c.keyfile).trim())connection.key_file=text(c.keyfile).trim();else delete connection.key_file;const model=text(c.model).trim();if(model){connection.model_specs=Object.assign({},row.model_specs||{});connection.model_specs[model]={context_window:number(c.maximum),reasoning_levels:text(c.levels).split(',').map(v=>v.trim()).filter(Boolean),supports_tools:checked(c.tools)};}send('save_connection',{connection:connection});}
    function editProxy(){if(state.edition!=='proxy')return message('扩展版功能','现成代理接口由扩展版提供。');const row=state.proxy||state.disabled_proxy||{base_url:'http://127.0.0.1:8317/v1',config:'',models:[]};const d=dialog('现成代理',[['url','接口地址',row.base_url],['config','配置文件',row.config,'file','选择已有 EasyCLI / CPA 配置路径'],['models','使用的模型',row.models.join(', ')],['auto','加入系列新版本',state.proxy_auto_add,'check']],['保存','停用','取消']);if(d.answer===2)return;send('save_proxy',{proxy:d.answer===1?null:{base_url:text(d.controls.url).trim(),config:text(d.controls.config).trim(),models:text(d.controls.models).split(/[,，]/).map(v=>v.trim()).filter(Boolean)},auto_add:checked(d.controls.auto)});}
    function editDeepSeek(){const d=dialog('DeepSeek',[['enabled','启用 DeepSeek',state.deepseek_enabled,'check'],['key','API Key','','secret','留空保留现有密钥']],['保存','取消']);if(!d.answer){const key=text(d.controls.key);d.controls.key.stringValue='';send('save_deepseek',{enabled:state.edition==='ds'?true:checked(d.controls.enabled),key:key});}}
    function editImages(){const row=state.proxy||{};const d=dialog('可选图片工具',[['enabled','启用图片工具',state.images_enabled,'check'],['config','现成代理配置',row.config||'','file'],['url','接口地址',row.base_url||'http://127.0.0.1:8317/v1']],['保存','取消']);if(!d.answer)send('save_images',{enabled:checked(d.controls.enabled),config:text(d.controls.config).trim(),url:text(d.controls.url).trim()});}
    function cleanup(){send('cleanup_preview',{},data=>{if(!data.items.length)return message('无需清理','没有可安全清理的过期缓存。');if(confirm('清理旧缓存','可释放约 '+(data.bytes/1048576).toFixed(1)+' MB。当前安装、恢复链和最近的回退版本会保留。'))send('cleanup');});}
    function recover(action){if(confirm(action==='restore'?'撤销适配层':'恢复中断的安装','请先正常退出 Codex。原版应用、账户和聊天记录会保留。'))send(action,{confirmed:true});}
    function compareModels(){const names=state.models.map(v=>v.name);const d=dialog('切换前比较',[['from','当前模型',0,names],['to','目标模型',0,names]],['比较','取消']);if(!d.answer)send('compare_models',{from:state.models[Number(d.controls.from.indexOfSelectedItem)].id,to:state.models[Number(d.controls.to.indexOfSelectedItem)].id},r=>message('窗口与能力',r.message+'\n'+human(r.from)+' → '+human(r.to)+'\n工具：'+(r.tools?'支持':'不支持')+'；图片：'+(r.images?'支持':'不支持')));}
    function cancelRecovery(){const row=(state.recovery||[]).find(v=>['completed','failed','cancelled'].indexOf(v.phase)<0);if(row)send('cancel_recovery',{id:row.id});else message('没有进行中的恢复','点击刷新可重新读取状态。');}
    render(state);app.finishLaunching;
    if(testing){window.appearance=$.NSAppearance.appearanceNamed($.NSAppearanceNameAqua);send('snapshot',{},data=>render(data));for(let i=0;i<3;i++){tabs.selectTabViewItemAtIndex(i);content.layoutSubtreeIfNeeded;content.display;content.dataWithPDFInsideRect(content.bounds).writeToFileAtomically($(ui+'/preview-'+i+'.pdf'),true);const bitmap=content.bitmapImageRepForCachingDisplayInRect(content.bounds);content.cacheDisplayInRectToBitmapImageRep(content.bounds,bitmap);const png=bitmap.representationUsingTypeProperties($.NSBitmapImageFileTypePNG,$({}));png.writeToFileAtomically($(ui+'/preview-'+i+'.png'),true);}const result={tabs:Number(tabs.numberOfTabViewItems),models:filtered.length,request_completed:pending===null,status:ObjC.unwrap(banner.stringValue),closed:false};window.close;result.closed=closing;return JSON.stringify(result);}
    window.makeKeyAndOrderFront(null);app.activateIgnoringOtherApps(true);
    const timer=$.NSTimer.timerWithTimeIntervalTargetSelectorUserInfoRepeats(.5,receiver,'poll:',null,true);$.NSRunLoop.mainRunLoop.addTimerForMode(timer,$.NSRunLoopCommonModes);app.run;timer.invalidate;
}
