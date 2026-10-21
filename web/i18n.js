/* Bundled UI dictionaries. Research content is never passed to the translator. */
const Locale=(()=>{
 const supported=['zh-CN','zh-TW','en','ja','ko'],names={'zh-CN':'简体中文','zh-TW':'繁體中文',en:'English',ja:'日本語',ko:'한국어'};
 let language='zh-CN';try{const saved=localStorage.getItem('yanxu-language-v1');if(supported.includes(saved))language=saved}catch{}
 const catalogs=window.ResearchLocales||{},patterns={};
 function text(source,args=[]){source=String(source);const key=source.trim(),edge=source.match(/^(\s*)[\s\S]*?(\s*)$/);let message=language==='zh-CN'?key:catalogs[language]?.[key];
  if(language!=='zh-CN'&&message===undefined&&source.includes('\n'))return source.split('\n').map(line=>{const prefix=line.match(/^(?:#{1,6}\s+|>\s*|[-*]\s+)/)?.[0]||'';return prefix+text(line.slice(prefix.length))}).join('\n');
  if(message===undefined&&!/[\u3400-\u9fff]/.test(key))message=key;
  if(message===undefined){if(!patterns[language])patterns[language]=Object.entries(catalogs[language]||{}).filter(([s])=>/\{\d+\}/.test(s)).sort((a,b)=>b[0].length-a[0].length).map(([s,t])=>({pattern:new RegExp('^'+s.split(/(\{\d+\})/).map(x=>/^\{\d+\}$/.test(x)?'([\\s\\S]*?)':x.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('')+'$'),fields:s.match(/\{\d+\}/g),text:t}));
   const found=patterns[language].find(item=>item.pattern.test(key));if(found){const match=found.pattern.exec(key),values=Object.fromEntries(found.fields.map((f,i)=>[f,match[i+1]]));message=found.text.replace(/\{\d+\}/g,x=>values[x]??x)}else message=key;}
  return edge[1]+message.replace(/\{(\d+)\}/g,(whole,n)=>n<args.length?String(args[n]):whole)+edge[2];}
 function apply(value){language=supported.includes(value)?value:'zh-CN';document.documentElement.lang=language;document.documentElement.dataset.locale=language;document.title=language==='zh-CN'?'研序 · Research Studio':'Yanxu · Research Studio';try{localStorage.setItem('yanxu-language-v1',language)}catch{}}
 apply(language);
 function ui(value,key='',catalog=false){
  if(Array.isArray(value))return value.map(x=>ui(x,key,catalog));
  if(value&&typeof value==='object'){
   if('role' in value&&'text' in value)return {...value,text:value.mode==='local'?(value.navigation_guide?new NavigationGuide(value.text,value.navigation_guide):new LocalizedText(value.text)):value.text,actions:ui(value.actions),sources:ui(value.sources,'sources')};
   const fields=new Set(['error','progress','message','warnings','warning','label','description','subtitle','reason','reasons','notes','availability','meaning','connection_label','history_note','sources','hint','help','placeholder','detail']);
   const trees=new Set(['catalog','projects','methods','parameters','fields','controls','followup','followup_next','next','timeline','issues','checks','audit','actions','job','jobs','runs','features','runtime','routing_recommendation','alternative','recommendations','messages']);
   return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,k==='research_status'?{...v,guidance:L(v.guidance||''),warnings:(v.warnings||[]).map(L)}:k==='catalog'?ui(v,k,true):k==='title'&&['next','followup_next'].includes(key)?new LocalizedText(value.title_template||v,(value.title_args||[]).map(L)):['title','status'].includes(k)&&['timeline','checks'].includes(key)?L(v):catalog||fields.has(k)||trees.has(k)?ui(v,k,catalog):v]));
  }
  if(typeof value==='string'&&/[\u3400-\u9fff]/.test(value)&&(catalog&&!['id','route','version','commit','source','skill','agent','scientific_skills','stages','flows','color','family'].includes(key)||['error','progress','message','warnings','warning','label','description','subtitle','reason','reasons','notes','availability','meaning','connection_label','history_note','sources','hint','help','placeholder','detail'].includes(key)))return new LocalizedText(value);
  return value;
 }
 return {supported,names,text,current:()=>language,apply,ui};
})();
class LocalizedText extends String{constructor(source,args=[]){super(source);this.source=source;this.args=args}toString(){return Locale.text(this.source,this.args)}toJSON(){return this.toString()}[Symbol.toPrimitive](){return this.toString()}}
class NavigationGuide extends LocalizedText{constructor(source,guide){super(source);this.guide=guide}toString(){
 const paths={drawing:['项目','研究工具','图表与绘图','绘图与结果重绘'],analysis:['项目','研究工具','图表与绘图','数据分析 → 出图'],paper:['项目','研究工具','图表与绘图','整篇论文图表'],experiments:['项目','研究工具','实验设计'],work:['项目','研究流程'],materials:['项目','文献与材料'],research:['项目','研究工具'],overview:['项目','继续研究'],history:['项目','运行记录'],settings:['模型与连接'],backups:['备份与恢复'],help:['使用指南'],pathways:['研究路径'],projects:['研究项目']};
 const path=paths[this.guide.target];if(!path)return super.toString();const name=Locale.text(path.at(-1));
 return (path.length>1?Locale.text('{0}在「{1}」。',[name,path.map(x=>Locale.text(x)).join(' → ')])+'\n\n'+Locale.text(this.guide.project?'点击下方按钮直接进入本项目。':'选择下面的项目，即可直接进入对应功能。没有项目时可以先新建。'):Locale.text('可以直接打开「{0}」。点击下方按钮即可进入。',[name]))+'\n\n'+Locale.text('本条为本机入口指导，不调用模型。');
}}
function L(value){return value instanceof LocalizedText?value:new LocalizedText(String(value))}
function H(strings,...values){
 const source=strings.map((part,i)=>part+(i<values.length?'{'+i+'}':'')).join('');
 if(!/<[a-z!/]/i.test(source))return Locale.text(source,values);
 const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const localized=source.split(/(<[^>]*>)/g).map(part=>part.startsWith('<')?part.replace(/((?:aria-label|title|placeholder|alt)=)(['"])(.*?)\2/g,(_,attr,q,value)=>attr+q+escape(Locale.text(value))+q):Locale.current()==='zh-CN'?part:escape(Locale.text(part))).join('');
 return localized.replace(/\{(\d+)\}/g,(whole,index)=>index<values.length?String(values[index]??''):whole);
}
window.ResearchI18n=Locale;
