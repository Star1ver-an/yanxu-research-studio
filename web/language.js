/* Language changes rerender labels while retaining in-progress forms and views. */
function languageOptions(value,auto=false){return (auto?`<option value="auto" ${value==='auto'?'selected':''}>${e(L('跟随界面语言'))}</option>`:'')+Locale.supported.map(id=>`<option value="${id}" ${value===id?'selected':''}>${e(Locale.names[id])}</option>`).join('')}
function languagePicker(){
 const current=Locale.current();
 return `<div class="language-picker"><button type="button" id="ui-language" class="language-trigger" aria-label="Language / 语言: ${e(Locale.names[current])}" title="Language / 语言" aria-haspopup="listbox" aria-expanded="false" aria-controls="language-menu"><svg class="language-globe" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z"/></svg><span>${e(Locale.names[current])}</span><svg class="language-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg></button><div id="language-menu" class="language-menu" role="listbox" aria-label="Language / 语言" hidden>${Locale.supported.map(id=>`<button type="button" class="language-option" role="option" data-language-choice="${id}" lang="${id}" aria-selected="${current===id}" tabindex="-1"><span>${e(Locale.names[id])}</span><span class="language-check" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m5 12 4 4 10-10"/></svg></span></button>`).join('')}</div></div>`;
}
function closeLanguageMenu(restoreFocus=false){
 const trigger=document.getElementById('ui-language'),menu=document.getElementById('language-menu');
 if(menu)menu.hidden=true;if(trigger)trigger.setAttribute('aria-expanded','false');
 if(restoreFocus)trigger?.focus({preventScroll:true});
}
function openLanguageMenu(index=Locale.supported.indexOf(Locale.current())){
 if(languageChanging)return;
 const trigger=document.getElementById('ui-language'),menu=document.getElementById('language-menu');if(!trigger||!menu)return;
 menu.hidden=false;trigger.setAttribute('aria-expanded','true');
 const rect=trigger.getBoundingClientRect(),width=menu.offsetWidth,gap=10,margin=12;
 menu.style.left=Math.max(margin,Math.min(rect.right-width,window.innerWidth-width-margin))+'px';
 menu.style.maxHeight=Math.max(0,window.innerHeight-margin*2)+'px';
 const height=menu.offsetHeight;
 menu.style.top=Math.max(margin,Math.min(rect.bottom+gap,window.innerHeight-height-margin))+'px';
 const option=menu.querySelectorAll('[data-language-choice]')[index];option?.focus({preventScroll:true});option?.scrollIntoView({block:'nearest'});
}
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
 closeLanguageMenu(true);languageChanging=true;const previous=Locale.current(),snapshot=languageDOMSnapshot();
 const trigger=document.getElementById('ui-language');if(trigger){trigger.disabled=true;trigger.setAttribute('aria-busy','true')}
 try{await api('preferences/language',{ui_language:next});Locale.apply(next);if(typeof AS!=='undefined')AS.preview=null;render();restoreLanguageDOM(snapshot);toast(L('界面语言已更新，正在编辑的内容已保留。'));}
 catch(error){Locale.apply(previous);toast(error.message,true)}finally{languageChanging=false;const picker=document.getElementById('ui-language');if(picker){picker.disabled=false;picker.removeAttribute('aria-busy')}}
}
const bootLabel=document.querySelector('.boot p');if(bootLabel)bootLabel.textContent=L('正在打开你的研究空间…');

document.addEventListener('click',event=>{
 const choice=event.target.closest('[data-language-choice]');
 if(choice){closeLanguageMenu(true);changeLanguage(choice.dataset.languageChoice);return}
 const trigger=event.target.closest('#ui-language');
 if(trigger){document.getElementById('language-menu')?.hidden?openLanguageMenu():closeLanguageMenu();return}
 if(!event.target.closest('.language-picker'))closeLanguageMenu();
});
document.addEventListener('keydown',event=>{
 const picker=event.target.closest('.language-picker');if(!picker||event.isComposing)return;
 const menu=document.getElementById('language-menu');if(!menu)return;
 if(event.key==='Escape'){if(!menu.hidden){event.preventDefault();closeLanguageMenu(true)}return}
 if(event.key==='Tab'){closeLanguageMenu();return}
 const options=[...menu.querySelectorAll('[data-language-choice]')],index=options.indexOf(event.target.closest('[data-language-choice]'));
 let next;
 if(event.key==='Home')next=0;
 else if(event.key==='End')next=options.length-1;
 else if(event.key==='ArrowDown')next=menu.hidden?Locale.supported.indexOf(Locale.current()):(index+1)%options.length;
 else if(event.key==='ArrowUp')next=menu.hidden?Locale.supported.indexOf(Locale.current()):(index-1+options.length)%options.length;
 else if(event.key.length===1&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&event.key!==' '){
  const key=event.key.toLocaleLowerCase();next=options.findIndex((option,i)=>i>index&&(option.textContent.trim().toLocaleLowerCase().startsWith(key)||option.dataset.languageChoice.toLowerCase().startsWith(key)));
  if(next<0)next=options.findIndex(option=>option.textContent.trim().toLocaleLowerCase().startsWith(key)||option.dataset.languageChoice.toLowerCase().startsWith(key));
 }
 if(next!==undefined&&next>=0){event.preventDefault();openLanguageMenu(next)}
});
document.addEventListener('focusout',event=>{if(event.target.closest('.language-picker')&&!event.relatedTarget?.closest('.language-picker'))closeLanguageMenu()});
window.addEventListener('resize',()=>closeLanguageMenu());
document.addEventListener('scroll',event=>{if(!event.target.closest?.('.language-menu'))closeLanguageMenu()},true);
