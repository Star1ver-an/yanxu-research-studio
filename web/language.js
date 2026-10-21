/* Language changes rerender labels while retaining in-progress forms and views. */
function languageOptions(value,auto=false){return (auto?`<option value="auto" ${value==='auto'?'selected':''}>${e(L('跟随界面语言'))}</option>`:'')+Locale.supported.map(id=>`<option value="${id}" ${value===id?'selected':''}>${e(Locale.names[id])}</option>`).join('')}
function languagePicker(){return `<label class="language-picker" title="Language / 语言"><span aria-hidden="true">◎</span><select id="ui-language" aria-label="Language / 语言">${languageOptions(Locale.current())}</select></label>`}
function outputLanguageField(project){return `<label class="field"><span>${e(L('研究输出语言'))}</span><select name="output_language" aria-label="${e(L('研究输出语言'))}">${languageOptions(project?.output_language||'auto',true)}</select><small>${e(L('仅用于此后生成的内容。已有报告、引用和原始材料保留原文。'))}</small></label>`}
function languagePreferences(){return `<section class="panel language-panel"><h2>${e(L('语言与研究输出'))}</h2><p>${e(L('界面支持简体中文、繁体中文、英语、日语和韩语。顶部可随时切换。'))}</p><p>${e(L('新项目默认跟随界面语言，也可在项目概况中指定研究输出语言。切换界面不会翻译或覆盖已有研究成果。'))}</p><small>${e(L('语言切换在本机完成，不调用模型。'))}</small></section>`}
function languageDOMSnapshot(){
 const root=document.querySelector('#app'),rows=[];if(!root)return {rows};
 const selector=node=>{if(node.id)return '#'+CSS.escape(node.id);const parts=[];while(node&&node!==root){const parent=node.parentElement;if(!parent)return '';parts.unshift(node.tagName.toLowerCase()+':nth-child('+([...parent.children].indexOf(node)+1)+')');node=parent;}return '#app > '+parts.join(' > ')};
 for(const node of root.querySelectorAll('input,textarea,select,details'))if(node.id!=='ui-language'&&node.type!=='file')rows.push({selector:selector(node),value:node.value,checked:node.checked,open:node.open,start:node.selectionStart,end:node.selectionEnd});
 const scroll=[...root.querySelectorAll('*'),document.querySelector('#as-messages')].filter(node=>node&&(node.scrollTop||node.scrollLeft)).map(node=>({id:node.id,selector:root.contains(node)?selector(node):'',top:node.scrollTop,left:node.scrollLeft}));
 return {rows,scroll,focus:root.contains(document.activeElement)?selector(document.activeElement):'',x:window.scrollX,y:window.scrollY};
}
function restoreLanguageDOM(snapshot){
 for(const row of snapshot.rows){const node=document.querySelector(row.selector);if(!node)continue;if(node.tagName==='DETAILS')node.open=row.open;else{node.value=row.value;if(node.type==='checkbox'||node.type==='radio')node.checked=row.checked;try{if(row.start!==null)node.setSelectionRange(row.start,row.end)}catch{}}}
 for(const row of snapshot.scroll||[]){const node=row.id?document.getElementById(row.id):document.querySelector(row.selector);if(node){node.scrollTop=row.top;node.scrollLeft=row.left}}
 if(snapshot.focus)document.querySelector(snapshot.focus)?.focus({preventScroll:true});window.scrollTo(snapshot.x,snapshot.y);
}
let languageChanging=false;
async function changeLanguage(next){
 if(languageChanging||!Locale.supported.includes(next)||next===Locale.current())return;
 languageChanging=true;const previous=Locale.current(),snapshot=languageDOMSnapshot();
 try{await api('preferences/language',{ui_language:next});Locale.apply(next);if(typeof AS!=='undefined')AS.preview=null;render();restoreLanguageDOM(snapshot);toast(L('界面语言已更新，正在编辑的内容已保留。'));}
 catch(error){Locale.apply(previous);const picker=document.querySelector('#ui-language');if(picker)picker.value=previous;toast(error.message,true)}finally{languageChanging=false}
}
const bootLabel=document.querySelector('.boot p');if(bootLabel)bootLabel.textContent=L('正在打开你的研究空间…');

document.addEventListener('change',event=>{if(event.target.id==='ui-language')changeLanguage(event.target.value)});
