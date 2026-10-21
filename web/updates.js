/* Release checks update their own controls, preserving open research forms. */
let updateState=null, updateTimer, updateNotified='';
function updateSummary(s){
 if(!s)return L('正在读取版本状态…');
 if(s.phase==='checking')return L('正在检查 GitHub 正式版本…');
 if(s.phase==='downloading')return H`正在下载 ${Math.min(100,Math.round(s.received/s.release.size*100))}%`;
 if(s.phase==='installing')return L('正在退出并安装，窗口将自动重新打开…');
 if(s.phase==='error')return s.error;
 if(s.phase==='ready')return H`v${s.release.version} 已下载并通过校验，可以安装。`;
 if(s.can_update)return H`发现新版本 v${s.release.version}`;
 if(s.phase==='current')return L('当前已是最新正式版本。');
 return L('启动后自动检查，也可以随时手动检查。');
}
function updatePanel(){return H`<section class="panel update-panel"><h3>${icon('download',18)}软件更新</h3><p>当前版本 v${e(S.catalog.version)}</p><div id="update-settings-status" role="status">${e(updateSummary(updateState))}</div><div class="update-buttons">${button('update-open',L('查看更新'),'secondary','download')}</div><p class="field-help">从本项目的 GitHub Releases 下载。安装前由你确认，研究任务运行时不能安装。</p></section>`}
function updateModal(){
 modal(H`<span class="template-icon green">${icon('download',24)}</span><h2>软件更新</h2><p class="modal-description">当前版本 v${e(S.catalog.version)} · GitHub 正式发布</p><label class="ack"><input id="update-auto" type="checkbox" ${updateState?.auto_check!==false?'checked':''}>启动时及每 6 小时自动检查新版</label><p id="update-status" role="status"></p><div id="update-details"></div><label class="ack" id="update-ack-label" hidden><input id="update-ack" type="checkbox">我已保存工作，同意安装并重启软件</label><div id="update-actions" class="update-buttons"></div><p class="field-help">项目和材料保存在原数据文件夹。安装会备份旧程序；复制或启动失败时恢复原程序。</p>`);
 paintUpdate();
}
function paintUpdate(){
 const s=updateState;if(!s)return;
 const badge=$('#update-badge');if(badge){badge.textContent=s.can_update?L('发现新版'):L('软件更新');badge.classList.toggle('has-update',s.can_update)}
 const settings=$('#update-settings-status');if(settings)settings.textContent=updateSummary(s);
 const status=$('#update-status');if(!status)return;
 status.textContent=updateSummary(s);status.classList.toggle('update-error',s.phase==='error');
 const details=$('#update-details');
 // Only replace release notes when their content changes; preserve focus and scroll.
 const html=s.release?H`<h3>正式版本 v${e(s.release.version)}</h3><pre class="release-notes">${e(s.release.notes||L('发布说明见 GitHub。'))}</pre>${button('external',L('查看发布页'),'text','external',`data-url="${e(s.release.page)}"`)}`:'';
 if(details.dataset.rendered!==html){details.innerHTML=html;details.dataset.rendered=html}
 const busy=['checking','downloading','installing'].includes(s.phase);
 const auto=$('#update-auto');auto.checked=s.auto_check;auto.disabled=s.phase==='installing';
 $('#update-ack-label').hidden=s.phase!=='ready'||!s.installable;
 const actions=button('update-check',L('检查更新'),'secondary','refresh',busy?'disabled':'')+
 (s.can_update&&s.installable&&s.phase!=='ready'?button('update-download',L('下载更新'),'primary','download',busy?'disabled':''):'')+
 (s.phase==='ready'&&s.installable?button('update-install',L('安装并重启'),'primary','check',busy?'disabled':''):'')+
 (!s.installable&&s.can_update?H`<p class="field-help">开发模式请从发布页下载 Windows 桌面版。</p>`:'');
 const actionRoot=$('#update-actions');
 if(actionRoot.dataset.rendered!==actions){actionRoot.innerHTML=actions;actionRoot.dataset.rendered=actions}
}
async function refreshUpdates(){
 clearTimeout(updateTimer);
 try{
  updateState=await api('updates/status');paintUpdate();
  if(updateState.can_update&&updateState.release.tag!==updateNotified){updateNotified=updateState.release.tag;toast(L('发现新版本 v')+updateState.release.version+L('，点击右上角“发现新版”查看。'))}
 }catch{/* A closed/restarting desktop will reconnect on its next launch. */}
 updateTimer=setTimeout(refreshUpdates,['checking','downloading','installing'].includes(updateState?.phase)?1000:15000);
}
async function updateAction(action){
 if(action==='update-open'){updateModal();await refreshUpdates();return}
 const kind=action.slice(7);
 if(kind==='install'){
  if(S.draft!==null)throw Error(L('请先保存正在编辑的报告，再安装更新。'));
  if(!$('#update-ack')?.checked)throw Error(L('请先保存工作，并勾选安装与重启确认。'));
 }
 updateState=await api('updates/'+kind,kind==='install'?{acknowledge:true}:{});paintUpdate();await refreshUpdates();
}
document.addEventListener('change',async ev=>{
 if(ev.target.id!=='update-auto')return;
 try{updateState=await api('updates/auto',{enabled:ev.target.checked});paintUpdate()}catch(err){toast(err.message,true);await refreshUpdates()}
});
