param([Parameter(Mandatory=$true)][string]$StateDirectory,[Parameter(Mandatory=$true)][string]$PythonExecutable,[Parameter(Mandatory=$true)][string]$ControllerPath,[switch]$SelfTest)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$script:UI=Join-Path $StateDirectory 'settings-ui';$script:Jobs=Join-Path $script:UI 'jobs'
$script:Pending=$null;$script:After=$null;$script:Report='';$script:Selected=$null;$script:LastRaise=''
function Read-Json([string]$Path){
 try{$stream=[IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete));try{$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);try{return ($reader.ReadToEnd()|ConvertFrom-Json)}finally{$reader.Dispose()}}finally{$stream.Dispose()}}catch{return $null}
}
function Write-Json([string]$Path,$Value){[IO.File]::WriteAllText($Path,($Value|ConvertTo-Json -Depth 50),[Text.UTF8Encoding]::new($false))}
function As-Map($Object){$result=@{};if($null -ne $Object){foreach($p in $Object.PSObject.Properties){$result[$p.Name]=$p.Value}};return $result}
function Tokens($Value){if($Value){return ('{0:N0} tokens' -f [long]$Value)};return '未提供'}
function Optional-Number($Control){if([string]::IsNullOrWhiteSpace($Control.Text)){return $null};$value=[long]0;if(![long]::TryParse($Control.Text.Trim(),[ref]$value) -or $value -lt 1){throw '请输入正整数'};return $value}
function Notify([string]$Title,[string]$Message){if(!$SelfTest){[void][Windows.Forms.MessageBox]::Show($script:Form,$Message,$Title,[Windows.Forms.MessageBoxButtons]::OK,[Windows.Forms.MessageBoxIcon]::Information)}}
function Confirm([string]$Title,[string]$Message){if($SelfTest){return $false};return [Windows.Forms.MessageBox]::Show($script:Form,$Message,$Title,[Windows.Forms.MessageBoxButtons]::OKCancel,[Windows.Forms.MessageBoxIcon]::Question) -eq [Windows.Forms.DialogResult]::OK}
function Label($Parent,$X,$Y,$W,$H,$Text,$Size=10,$Bold=$false){$v=[Windows.Forms.Label]::new();$v.SetBounds($X,$Y,$W,$H);$v.Text=$Text;$v.Font=[Drawing.Font]::new('Microsoft YaHei UI',$Size,$(if($Bold){[Drawing.FontStyle]::Bold}else{[Drawing.FontStyle]::Regular}));$Parent.Controls.Add($v);return $v}
function Input($Parent,$X,$Y,$W,$Text='',$Password=$false){$v=[Windows.Forms.TextBox]::new();$v.SetBounds($X,$Y,$W,29);$v.Text=[string]$Text;$v.UseSystemPasswordChar=$Password;$Parent.Controls.Add($v);return $v}
function Button($Parent,$X,$Y,$W,$Title,[scriptblock]$Action){$v=[Windows.Forms.Button]::new();$v.SetBounds($X,$Y,$W,34);$v.Text=$Title;$v.FlatStyle=[Windows.Forms.FlatStyle]::System;$v.Tag=$Action;$v.Add_Click({param($sender,$event);try{& $sender.Tag}catch{$script:Banner.Text='操作未完成，请核对填写内容。'}});$Parent.Controls.Add($v);return $v}
function Toggle($Parent,$X,$Y,$W,$Title,$Checked=$false){$v=[Windows.Forms.CheckBox]::new();$v.SetBounds($X,$Y,$W,28);$v.Text=$Title;$v.Checked=[bool]$Checked;$Parent.Controls.Add($v);return $v}
function Dialog([string]$Title,$Rows,$Buttons){
 $d=[Windows.Forms.Form]::new();$d.Text=$Title;$d.StartPosition='CenterParent';$d.FormBorderStyle='FixedDialog';$d.MaximizeBox=$false;$d.MinimizeBox=$false;$d.Font=$script:Form.Font;$d.AutoScaleMode='Dpi';$d.ClientSize=[Drawing.Size]::new(600,($Rows.Count*46+78));$d.Tag=($Buttons.Count-1);$fields=@{};$y=18
 foreach($row in $Rows){[void](Label $d 18 $y 145 27 $row.Label);switch($row.Kind){
  'check' {$control=Toggle $d 170 $y 405 '' $row.Value}
  'choice' {$control=[Windows.Forms.ComboBox]::new();$control.SetBounds(170,$y,405,29);$control.DropDownStyle='DropDownList';[void]$control.Items.AddRange([object[]]$row.Items);$control.SelectedIndex=[int]$row.Value;$d.Controls.Add($control)}
  default {$control=Input $d 170 $y $(if($row.Kind -eq 'file'){355}else{405}) $row.Value ($row.Kind -eq 'password');if($row.Kind -eq 'file'){$browse=[Windows.Forms.Button]::new();$browse.SetBounds(532,$y,43,29);$browse.Text='…';$browse.Tag=$control;$browse.Add_Click({param($sender,$event);$pick=[Windows.Forms.OpenFileDialog]::new();try{if($pick.ShowDialog($sender.FindForm()) -eq 'OK'){$sender.Tag.Text=$pick.FileName}}finally{$pick.Dispose()}});$d.Controls.Add($browse)}}
 };$fields[$row.Key]=$control;$y+=46}
 for($i=0;$i -lt $Buttons.Count;$i++){$v=[Windows.Forms.Button]::new();$v.Text=$Buttons[$i];$v.SetBounds((585-($Buttons.Count-$i)*142),($y+8),134,34);$v.Tag=$i;$v.Add_Click({param($sender,$event);$sender.FindForm().Tag=[int]$sender.Tag;$sender.FindForm().Close()});$d.Controls.Add($v)}
 if(!$SelfTest){[void]$d.ShowDialog($script:Form)}
 return @{Answer=[int]$d.Tag;Fields=$fields;Form=$d}
}
$script:State=Read-Json (Join-Path $script:UI 'view.json')
if(!$script:State){throw 'Settings snapshot is missing'}
$script:Form=[Windows.Forms.Form]::new();$script:Form.Text='Codex 模型设置';$script:Form.ClientSize=[Drawing.Size]::new(920,690);$script:Form.StartPosition='CenterScreen';$script:Form.FormBorderStyle='FixedSingle';$script:Form.MaximizeBox=$false;$script:Form.AutoScaleMode='Dpi';$script:Form.Font=[Drawing.Font]::new('Microsoft YaHei UI',9.5);$script:Form.BackColor=[Drawing.Color]::FromArgb(247,248,250)
[void](Label $script:Form 26 22 650 36 'Codex 模型设置' 20 $true)
$script:Subtitle=Label $script:Form 28 61 860 28 '' 9
$tabs=[Windows.Forms.TabControl]::new();$tabs.SetBounds(22,102,876,522);$script:Form.Controls.Add($tabs)
$overview=[Windows.Forms.TabPage]::new('概览');$models=[Windows.Forms.TabPage]::new('模型与连接');$maintenance=[Windows.Forms.TabPage]::new('更新与维护');foreach($page in @($overview,$models,$maintenance)){$page.BackColor=[Drawing.Color]::White;$tabs.TabPages.Add($page)}
$script:Banner=Label $script:Form 28 641 650 30 '' 9
[void](Button $script:Form 692 635 96 '刷新' {Send-Action 'snapshot' @{} {param($value)Render $value}})
[void](Button $script:Form 796 635 96 '取消操作' {if($script:Pending){Write-Json (Join-Path $script:Jobs ($script:Pending.Id+'.cancel')) @{id=$script:Pending.Id};$script:Banner.Text='已请求取消；正在保存的操作会先安全完成。'}})
$script:Health=Label $overview 24 22 805 38 '' 17 $true
$script:Summary=Label $overview 24 70 805 60 '' 10
[void](Button $overview 20 144 140 '检查更新' {Send-Action 'check_updates'})
[void](Button $overview 168 144 140 '运行诊断' {Send-Action 'diagnose' @{endpoints=$true} {param($value)$script:Report=$value.text;$script:ReportBox.Text=$script:Report;$tabs.SelectedIndex=0}})
[void](Button $overview 316 144 140 '更新进度' {Send-Action 'show_progress'})
[void](Button $overview 464 144 140 '复制诊断' {if($script:Report){[Windows.Forms.Clipboard]::SetText($script:Report);$script:Banner.Text='已复制脱敏诊断报告。'}else{Notify '尚无诊断报告' '先运行一次诊断。'}})
$script:ReportBox=[Windows.Forms.TextBox]::new();$script:ReportBox.SetBounds(24,198,805,254);$script:ReportBox.Multiline=$true;$script:ReportBox.ReadOnly=$true;$script:ReportBox.ScrollBars='Vertical';$script:ReportBox.BackColor=[Drawing.Color]::FromArgb(250,251,253);$overview.Controls.Add($script:ReportBox)
[void](Label $models 20 25 72 26 '搜索模型' 9);$script:Search=Input $models 96 22 394 '';$script:Search.Add_TextChanged({Filter-Models})
$script:Grid=[Windows.Forms.DataGridView]::new();$script:Grid.SetBounds(20,64,470,270);$script:Grid.ReadOnly=$true;$script:Grid.AllowUserToAddRows=$false;$script:Grid.AllowUserToDeleteRows=$false;$script:Grid.RowHeadersVisible=$false;$script:Grid.MultiSelect=$false;$script:Grid.SelectionMode='FullRowSelect';$script:Grid.BackgroundColor=[Drawing.Color]::White;$script:Grid.BorderStyle='FixedSingle';$script:Grid.RowTemplate.Height=30
[void]$script:Grid.Columns.Add('name','模型');[void]$script:Grid.Columns.Add('window','使用窗口');[void]$script:Grid.Columns.Add('visible','状态');$script:Grid.Columns[0].Width=235;$script:Grid.Columns[1].Width=140;$script:Grid.Columns[2].Width=70;$script:Grid.Add_SelectionChanged({Select-Model});$models.Controls.Add($script:Grid)
$script:ModelLabel=Label $models 514 22 320 36 '选择一个模型' 14 $true
$script:Capabilities=Label $models 514 66 320 73 '' 9
$script:Maximum=Label $models 514 148 320 43 '' 9
[void](Label $models 514 204 100 24 '使用窗口' 9);$script:Context=Input $models 622 200 208 ''
[void](Label $models 514 246 100 24 '压缩阈值' 9);$script:Compact=Input $models 622 242 208 ''
$script:Visible=Toggle $models 514 283 320 '在 Codex 模型菜单中显示' $true
[void](Button $models 510 327 146 '恢复默认' {$script:Context.Text='';$script:Compact.Text='';Save-Model})
[void](Button $models 664 327 170 '保存模型设置' {Save-Model})
[void](Label $models 22 376 810 38 '留空使用自动窗口与压缩阈值。连接变更会先验证，退出 Codex 后再应用。' 9)
if($script:State.edition -eq 'proxy'){
 [void](Button $models 16 422 128 '添加接口' {Edit-Connection $null})
 [void](Button $models 152 422 128 '管理连接' {Manage-Connections})
 [void](Button $models 288 422 128 '现成代理' {Edit-Proxy})
}
[void](Button $models $(if($script:State.edition -eq 'proxy'){424}else{16}) 422 128 'DeepSeek' {Edit-DeepSeek})
[void](Button $models $(if($script:State.edition -eq 'proxy'){560}else{152}) 422 154 '切换前比较' {Compare-Models})
$script:Code=Toggle $maintenance 24 22 370 '自动检查补丁更新' $true
$script:Sync=Toggle $maintenance 24 62 370 '自动同步模型与规格' $true
$script:Progress=Toggle $maintenance 24 102 370 '发现补丁更新时显示进度' $true
[void](Label $maintenance 445 29 160 26 '检查间隔（分钟）' 9);$script:Interval=Input $maintenance 620 22 195 '5'
[void](Button $maintenance 615 70 204 '保存维护设置' {Send-Action 'save_options' @{auto_update=$script:Code.Checked;model_sync=$script:Sync.Checked;progress_ui=$script:Progress.Checked;interval=((Optional-Number $script:Interval)*60)}})
[void](Label $maintenance 24 163 600 30 '常用维护' 13 $true)
[void](Button $maintenance 18 207 150 '立即检查' {Send-Action 'check_updates'})
[void](Button $maintenance 176 207 150 '重新尝试更新' {Send-Action 'retry_update'})
[void](Button $maintenance 334 207 150 '清理旧缓存' {Cleanup})
[void](Button $maintenance 492 207 150 '图片工具' {Edit-Images})
$script:Recovery=Label $maintenance 24 267 790 66 '' 9
[void](Button $maintenance 18 337 200 '取消当前历史恢复' {Cancel-Recovery})
$advanced=Toggle $maintenance 24 391 300 '高级维护' $false
$script:RecoverButton=Button $maintenance 18 430 200 '恢复中断的安装' {Recover 'recover'};$script:RecoverButton.Visible=$false
$script:RestoreButton=Button $maintenance 226 430 200 '撤销适配层' {Recover 'restore'};$script:RestoreButton.Visible=$false
$advanced.Add_CheckedChanged({param($sender,$event)$script:RecoverButton.Visible=$sender.Checked;$script:RestoreButton.Visible=$sender.Checked})
[void](Button $maintenance 661 430 158 '使用说明' {[Diagnostics.Process]::Start($script:State.help_url)|Out-Null})
function Filter-Models {
 $query=$script:Search.Text.ToLowerInvariant();$script:Grid.Rows.Clear()
 foreach($row in $script:State.models){if(($row.name+' '+$row.id+' '+$row.provider).ToLowerInvariant().Contains($query)){$i=$script:Grid.Rows.Add([string]$row.name,(Tokens $row.effective),$(if($row.visible){'显示'}else{'隐藏'}));$script:Grid.Rows[$i].Tag=$row}}
 if($script:Grid.Rows.Count){$script:Grid.Rows[0].Selected=$true};Select-Model
}
function Select-Model {
 if(!$script:Grid.SelectedRows.Count){$script:Selected=$null;return};$script:Selected=$script:Grid.SelectedRows[0].Tag;if(!$script:Selected){return};$m=$script:Selected
 $script:ModelLabel.Text=$m.name;$script:Capabilities.Text='来源：'+$m.provider+"`r`n工具 "+$(if($m.tools){'支持'}else{'不支持'})+' · 图片 '+$(if($m.images){'支持'}else{'不支持'})+"`r`n思考档位："+$(if($m.reasoning.Count){$m.reasoning -join ' / '}else{'服务未提供'})
 $script:Maximum.Text='模型最大：'+(Tokens $m.maximum)+$(if($m.server_window){"`r`n服务加载："+(Tokens $m.server_window)}else{''})
 $script:Context.Text=[string]$m.context_override;$script:Compact.Text=[string]$m.compact_override;$script:Visible.Checked=[bool]$m.visible
}
function Save-Model {if($script:Selected){Send-Action 'save_model' @{id=$script:Selected.id;context=(Optional-Number $script:Context);compact=(Optional-Number $script:Compact);visible=$script:Visible.Checked}}}
function Render($Value){
 $script:State=$Value;$script:Subtitle.Text=$Value.edition_label+' · '+$Value.version+'     官方 Codex '+$Value.app_version;$script:Health.Text=$Value.status
 $script:Summary.Text='已显示 '+@($Value.models|Where-Object visible).Count+' 个模型 · '+@($Value.connections).Count+" 个自定义连接`r`n"+$(if($Value.queued){'变更已排队，正在等待后台处理。'}else{'模型目录、登录与工具由官方 Codex 承载。'})
 $script:Code.Checked=[bool]$Value.options.auto_update;$script:Sync.Checked=[bool]$Value.options.model_sync;$script:Progress.Checked=[bool]$Value.options.progress_ui;$script:Interval.Text=[string]($Value.options.interval/60)
 Filter-Models
 if(@($Value.recovery).Count){$script:Recovery.Text=(@($Value.recovery|Select-Object -First 2|ForEach-Object {$_.model+' · '+$_.phase+$(if($_.total){' · '+$_.part+'/'+$_.total}else{''})}) -join "`r`n")}else{$script:Recovery.Text="当前没有历史恢复任务。`r`n长历史恢复由当前模型处理；可在此查看进度和取消。"}
 if(!$script:Report){$script:ReportBox.Text="可以在这里查看更新状态、运行诊断并复制反馈信息。`r`n`r`n设置窗口关闭后退出，不会增加常驻管理进程。"}
}
function Send-Action([string]$Action,$Payload=@{},[scriptblock]$Callback=$null){
 if($script:Pending){Notify '有操作正在进行' '请等待当前操作完成。关闭设置窗口不会取消已经提交的操作。';return}
 $id=[guid]::NewGuid().ToString('N');Write-Json (Join-Path $script:Jobs ($id+'.request.json')) @{action=$Action;payload=$Payload;revision=$script:State.revision}
 $start=[Diagnostics.ProcessStartInfo]::new();$start.FileName=$PythonExecutable;$start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WindowStyle='Hidden'
 $start.Arguments=(@($ControllerPath,'execute','--state-dir',$StateDirectory,'--request',$id)|ForEach-Object {'"'+$_+'"'}) -join ' '
 $process=[Diagnostics.Process]::Start($start);$script:Pending=@{Id=$id;Process=$process};$script:After=$Callback;$script:Banner.Text='正在处理…'
 if($SelfTest){if(!$process.WaitForExit(30000)){throw 'Settings self-test worker timed out'};Poll}
}
$script:LastRecoveryPoll=[datetime]::MinValue
function Refresh-Recovery {
 if(([datetime]::Now-$script:LastRecoveryPoll).TotalSeconds -lt 1){return};$script:LastRecoveryPoll=[datetime]::Now
 if(!$script:State.recovery_directory -or !(Test-Path -LiteralPath $script:State.recovery_directory)){return}
 $rows=@(Get-ChildItem -LiteralPath $script:State.recovery_directory -Filter '*.json' -File | Where-Object {$_.Name -match '^[a-f0-9]{32}\.json$' -and $_.Length -lt 8192 -and !($_.Attributes -band [IO.FileAttributes]::ReparsePoint)} | ForEach-Object {Read-Json $_.FullName} | Where-Object {$_.format -eq 1 -and ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-$_.updated_at) -lt 3600} | Sort-Object updated_at -Descending | Select-Object -First 20)
 $script:State.recovery=$rows;$names=@{recovering='正在恢复';summarizing='正在整理摘要';completed='已完成';failed='未完成';cancelled='已取消'}
 if($rows.Count){$script:Recovery.Text=(@($rows|Select-Object -First 2|ForEach-Object {$_.model+' · '+$names[$_.phase]+$(if($_.total){' · '+$_.part+'/'+$_.total}else{''})+$(if($_.cache){' · 使用摘要缓存'}else{''})}) -join "`r`n")}
}
function Poll {
 Refresh-Recovery
 $raised=Read-Json (Join-Path $script:UI 'raise.json');if($raised -and [string]$raised.time -ne $script:LastRaise){$script:LastRaise=[string]$raised.time;$script:Form.WindowState='Normal';$script:Form.Activate();$script:Form.BringToFront()}
 if(!$script:Pending){return};$value=Read-Json (Join-Path $script:Jobs ($script:Pending.Id+'.result.json'))
 if(!$value -or $value.status -eq 'running'){if($script:Pending.Process.HasExited){$script:Pending.Process.Dispose();$script:Pending=$null;$script:Banner.Text='操作未完成，请刷新后重试。'};return}
 $script:Pending.Process.Dispose();$script:Pending=$null;$callback=$script:After;$script:After=$null
 if($value.status -eq 'failed'){$script:Banner.Text=$value.error.message;return}
 if($value.snapshot){Render $value.snapshot};$script:Banner.Text=$(if($value.data.message){$value.data.message}else{'操作完成。'});if($callback){& $callback $value.data}
}
function Edit-Connection($Old){
 if($script:State.edition -ne 'proxy'){Notify '扩展版功能' 'DS 版只管理 DeepSeek；其他接口可通过扩展版接入。';return}
 $row=if($Old){As-Map $Old}else{@{id=('local-'+[guid]::NewGuid().ToString('N').Substring(0,8));name='';kind='openai';base_url='';wire_api='chat_completions';models=@();model_specs=@{}}}
 $d=Dialog $(if($Old){'编辑连接'}else{'添加模型接口'}) @(
  @{Key='name';Label='连接名称';Value=$row.name},@{Key='kind';Label='服务类型';Kind='choice';Value=([array]::IndexOf(@('openai','ollama','lmstudio'),$row.kind));Items=@('通用兼容接口','Ollama','LM Studio')},@{Key='url';Label='接口地址';Value=$row.base_url},@{Key='key';Label='API Key（可留空）';Kind='password';Value=''},@{Key='wire';Label='接口协议';Kind='choice';Value=$(if($row.wire_api -eq 'responses'){0}else{1});Items=@('Responses','Chat Completions')},@{Key='models';Label='型号（逗号分隔）';Value=($row.models -join ', ')},@{Key='respect';Label='沿用服务窗口';Kind='check';Value=$row.respect_server_context}
 ) @('检测并保存','保存','取消')
 try{if($d.Answer -eq 2){return};$c=$d.Fields;$row.name=$c.name.Text.Trim();$row.kind=@('openai','ollama','lmstudio')[$c.kind.SelectedIndex];$row.base_url=$c.url.Text.Trim();$row.wire_api=@('responses','chat_completions')[$c.wire.SelectedIndex];$row.models=@($c.models.Text -split '[,，\r\n]'|ForEach-Object {$_.Trim()}|Where-Object {$_});$row.respect_server_context=$c.respect.Checked;$key=$c.key.Text;$c.key.Clear();if($d.Answer -eq 1){Send-Action 'save_connection' @{connection=$row;key=$key}}else{$script:Draft=@{connection=$row;key=$key};Send-Action 'probe_connection' $script:Draft {param($result)$selected=Choose-Discovered $result $script:Draft.connection;if($selected){$script:Draft.connection=$selected;Send-Action 'save_connection' $script:Draft};$script:Draft=$null}}}finally{$d.Form.Dispose()}
}
function Choose-Discovered($Result,$Connection){
 if(!@($Result.available).Count){Notify '暂无模型' '接口可连接，但没有返回可用型号。';return $null}
 $d=[Windows.Forms.Form]::new();$d.Text='选择加入 Codex 的模型';$d.StartPosition='CenterParent';$d.FormBorderStyle='FixedDialog';$d.MaximizeBox=$false;$d.MinimizeBox=$false;$d.Font=$script:Form.Font;$d.ClientSize=[Drawing.Size]::new(610,440);$d.Tag=$false
 [void](Label $d 18 14 570 32 '勾选常用型号；服务缺少规格时再补充最大窗口。')
 $list=[Windows.Forms.CheckedListBox]::new();$list.SetBounds(18,50,574,300);$list.CheckOnClick=$true;$d.Controls.Add($list)
 foreach($id in $Result.available){[void]$list.Items.Add([string]$id,(!@($Connection.models).Count -or $id -in $Connection.models))}
 foreach($choice in @(@('全选',$true),@('清空',$false))){$b=[Windows.Forms.Button]::new();$b.SetBounds($(if($choice[1]){18}else{108}),358,82,30);$b.Text=$choice[0];$b.Tag=@{list=$list;selected=$choice[1]};$b.Add_Click({param($sender,$event);for($i=0;$i -lt $sender.Tag.list.Items.Count;$i++){$sender.Tag.list.SetItemChecked($i,$sender.Tag.selected)}});$d.Controls.Add($b)}
 $save=[Windows.Forms.Button]::new();$save.SetBounds(304,396,160,32);$save.Text='保存所选模型';$save.Add_Click({param($sender,$event);$sender.FindForm().Tag=$true;$sender.FindForm().Close()});$d.Controls.Add($save)
 $cancel=[Windows.Forms.Button]::new();$cancel.SetBounds(474,396,116,32);$cancel.Text='取消';$cancel.Add_Click({param($sender,$event);$sender.FindForm().Close()});$d.Controls.Add($cancel)
 try{if(!$SelfTest){[void]$d.ShowDialog($script:Form)};if(!$d.Tag){return $null};$Connection.models=@($list.CheckedItems|ForEach-Object {[string]$_})}finally{$d.Dispose()}
 if(!@($Connection.models).Count){Notify '尚未选择模型' '至少选择一个型号后再保存。';return $null}
 foreach($id in $Connection.models){if($Result.specs.PSObject.Properties[$id]){continue};$spec=Dialog ('补充 '+$id+' 的规格') @(@{Key='maximum';Label='模型最大上下文';Value=''},@{Key='tools';Label='支持工具调用';Kind='check';Value=$true}) @('继续','取消');try{if($spec.Answer){return $null};$maximum=Optional-Number $spec.Fields.maximum;if(!$maximum -or $maximum -lt 1024){Notify '窗口数值无效' '请填写至少 1024 的整数。';return $null};if(!$Connection.model_specs){$Connection.model_specs=@{}}elseif($Connection.model_specs -isnot [System.Collections.IDictionary]){$Connection.model_specs=As-Map $Connection.model_specs};$Connection.model_specs[$id]=@{context_window=$maximum;supports_tools=$spec.Fields.tools.Checked}}finally{$spec.Form.Dispose()}}
 return $Connection
}
function Manage-Connections {
 if(!@($script:State.connections).Count){Edit-Connection $null;return}
 $d=Dialog '管理已有连接' @(@{Key='connection';Label='选择连接';Kind='choice';Value=0;Items=@($script:State.connections|ForEach-Object name)}) @('编辑','移除','高级选项','取消')
 try{$row=$script:State.connections[$d.Fields.connection.SelectedIndex];switch($d.Answer){0{Edit-Connection $row}1{if(Confirm '移除连接' '会从模型目录移除此连接；服务、凭证文件和聊天记录保留。'){Send-Action 'remove_connection' @{id=$row.id}}}2{Advanced-Connection $row}}}finally{$d.Form.Dispose()}
}
function Advanced-Connection($Row){
 $d=Dialog '连接高级选项' @(@{Key='keyfile';Label='凭证文件';Kind='file';Value=$Row.key_file},@{Key='reasoning';Label='支持思考参数';Kind='check';Value=$Row.supports_reasoning_effort},@{Key='model';Label='补充规格的型号';Value=''},@{Key='maximum';Label='模型最大窗口';Value=''},@{Key='levels';Label='思考档位（逗号）';Value=''},@{Key='tools';Label='支持工具调用';Kind='check';Value=$true}) @('保存','取消')
 try{if($d.Answer){return};$c=$d.Fields;$row=As-Map $Row;$row.supports_reasoning_effort=$c.reasoning.Checked;if($c.keyfile.Text.Trim()){$row.key_file=$c.keyfile.Text.Trim()}else{$row.Remove('key_file')};if($c.model.Text.Trim()){$row.model_specs=As-Map $Row.model_specs;$row.model_specs[$c.model.Text.Trim()]=@{context_window=(Optional-Number $c.maximum);reasoning_levels=@($c.levels.Text -split ','|ForEach-Object {$_.Trim()}|Where-Object {$_});supports_tools=$c.tools.Checked}};Send-Action 'save_connection' @{connection=$row}}finally{$d.Form.Dispose()}
}
function Edit-Proxy {
 if($script:State.edition -ne 'proxy'){Notify '扩展版功能' '现成代理接口由扩展版提供。';return}
 $row=if($script:State.proxy){$script:State.proxy}else{[pscustomobject]@{base_url='http://127.0.0.1:8317/v1';config='';models=@()}}
 $d=Dialog '现成代理' @(@{Key='url';Label='接口地址';Value=$row.base_url},@{Key='config';Label='现有配置文件';Kind='file';Value=$row.config},@{Key='models';Label='型号（逗号分隔）';Value=($row.models -join ', ')},@{Key='auto';Label='加入系列新版本';Kind='check';Value=$script:State.proxy_auto_add}) @('保存','停用','取消')
 try{if($d.Answer -eq 2){return};$proxy=if($d.Answer -eq 1){$null}else{@{base_url=$d.Fields.url.Text.Trim();config=$d.Fields.config.Text.Trim();models=@($d.Fields.models.Text -split '[,，]'|ForEach-Object {$_.Trim()}|Where-Object {$_})}};Send-Action 'save_proxy' @{proxy=$proxy;auto_add=$d.Fields.auto.Checked}}finally{$d.Form.Dispose()}
}
function Edit-DeepSeek {$d=Dialog 'DeepSeek' @(@{Key='enabled';Label='启用 DeepSeek';Kind='check';Value=$script:State.deepseek_enabled},@{Key='key';Label='API Key（可留空）';Kind='password';Value=''}) @('保存','取消');try{if(!$d.Answer){$key=$d.Fields.key.Text;$d.Fields.key.Clear();Send-Action 'save_deepseek' @{enabled=($script:State.edition -eq 'ds' -or $d.Fields.enabled.Checked);key=$key}}}finally{$d.Form.Dispose()}}
function Edit-Images {$row=$script:State.proxy;$d=Dialog '可选图片工具' @(@{Key='enabled';Label='启用图片工具';Kind='check';Value=$script:State.images_enabled},@{Key='config';Label='现成代理配置';Kind='file';Value=$row.config},@{Key='url';Label='接口地址';Value=$(if($row.base_url){$row.base_url}else{'http://127.0.0.1:8317/v1'})}) @('保存','取消');try{if(!$d.Answer){Send-Action 'save_images' @{enabled=$d.Fields.enabled.Checked;config=$d.Fields.config.Text.Trim();url=$d.Fields.url.Text.Trim()}}}finally{$d.Form.Dispose()}}
function Cleanup {Send-Action 'cleanup_preview' @{} {param($value)if(!@($value.items).Count){Notify '无需清理' '没有可安全清理的过期缓存。'}elseif(Confirm '清理旧缓存' ('可释放约 '+[math]::Round($value.bytes/1MB,1)+' MB。当前安装、恢复链和最近的回退版本会保留。')){Send-Action 'cleanup'}}}
function Recover($Action){if(Confirm $(if($Action -eq 'restore'){'撤销适配层'}else{'恢复中断的安装'}) '请先正常退出 Codex。原版应用、账户和聊天记录会保留。'){Send-Action $Action @{confirmed=$true}}}
function Compare-Models {$names=@($script:State.models|ForEach-Object name);$d=Dialog '切换前比较' @(@{Key='from';Label='当前模型';Kind='choice';Value=0;Items=$names},@{Key='to';Label='目标模型';Kind='choice';Value=0;Items=$names}) @('比较','取消');try{if(!$d.Answer){Send-Action 'compare_models' @{from=$script:State.models[$d.Fields.from.SelectedIndex].id;to=$script:State.models[$d.Fields.to.SelectedIndex].id} {param($v)Notify '窗口与能力' ($v.message+"`r`n"+(Tokens $v.from)+' → '+(Tokens $v.to))}}}finally{$d.Form.Dispose()}}
function Cancel-Recovery {$row=$script:State.recovery|Where-Object {$_.phase -notin @('completed','failed','cancelled')}|Select-Object -First 1;if($row){Send-Action 'cancel_recovery' @{id=$row.id}}else{Notify '没有进行中的恢复' '点击刷新可重新读取状态。'}}
Render $script:State
if($SelfTest){if($env:CODEX_SETTINGS_PRIVATE_DESKTOP -eq '1'){$script:Form.Show();[Windows.Forms.Application]::DoEvents()};$script:Form.CreateControl();Send-Action 'snapshot' @{} {param($value)Render $value};for($i=0;$i -lt $tabs.TabPages.Count;$i++){$tabs.SelectedIndex=$i;$script:Form.PerformLayout();[Windows.Forms.Application]::DoEvents();$bitmap=[Drawing.Bitmap]::new($script:Form.Width,$script:Form.Height);try{$script:Form.DrawToBitmap($bitmap,[Drawing.Rectangle]::new(0,0,$bitmap.Width,$bitmap.Height));$bitmap.Save((Join-Path $script:UI ('preview-'+$i+'.png')),[Drawing.Imaging.ImageFormat]::Png)}finally{$bitmap.Dispose()}};$result=@{tabs=$tabs.TabPages.Count;models=$script:Grid.Rows.Count;request_completed=($null -eq $script:Pending);status=$script:Banner.Text;closed=$false};$script:Form.Close();$script:Form.Dispose();$result.closed=$script:Form.IsDisposed;$result|ConvertTo-Json -Compress;exit 0}
$timer=[Windows.Forms.Timer]::new();$timer.Interval=500;$timer.Add_Tick({Poll});$timer.Start()
try{[void]$script:Form.ShowDialog()}finally{$timer.Stop();$timer.Dispose();$script:Form.Dispose()}
