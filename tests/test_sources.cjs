const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const els={'meta[name="research-token"]':{content:'fixture'}},ctx=vm.createContext({L:s=>s,H:(s,...v)=>s.map((a,i)=>a+(v[i]??'')).join(''),
 document:{querySelector:s=>els[s],querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){}},
 localStorage:{getItem(){return null},setItem(){},removeItem(){}},setTimeout(){},clearTimeout(){},queueMicrotask(){},URL,Date,Intl,FormData:class{},navigator:{clipboard:{writeText:async()=>{}}}});
for(const f of ['pathways.js','research.js','sources.js','app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',f),'utf8').replace(/\binit\(\);\s*$/,''),ctx);
const run=s=>vm.runInContext(s,ctx);
(async()=>{
 run(`S.catalog={flows:{full:{stages:['research']}},stages:{research:{title:'Research'}}};S.project={id:'a',flow:'full',cursor:0,assets:[],outputs:{},research:{sources:[{id:'s',title:'<img src=x>',coverage:'fulltext',decision:'included',url:'https://example.org/?x=1&y=2',reason:'<script>x</script>'}],tasks:[{id:'t',title:'Task'}]}};S.activeStage='research';render=()=>{};`);
 const html=run('sourceView(S.project)');assert.ok(!html.includes('<img src=x>'));assert.ok(!html.includes('<script>x</script>'));
 assert.match(html,/搜索项目材料/);assert.match(run(`rtField('sources','task_id',{})`),/Task/);
 assert.match(run(`rtField('sources','source_date',{})`),/获取日期/);
 assert.match(run(`rtField('searches','source_date',{})`),/检索日期/);
 run(`SourceUI.audit={pid:'a',stamp:sourceStamp(S.project),result:{meaning:'scope',counts:{claims:0,sources:1,locatable:0,issues:0},issues:[],claims:[]}}`);
 assert.match(run('sourceAuditView(S.project)'),/尚未登记论断/);
 run(`S.project.research.sources[0].reason='changed'`);assert.match(run('sourceAuditView(S.project)'),/记录已变更/);
 run(`SourceUI.search={pid:'other',stamp:JSON.stringify(S.project.assets),result:{query:'leaked query',results:[]}}`);
 assert.ok(!run('sourceSearchView(S.project)').includes('leaked query'));
 const control={disabled:false};ctx.form={dataset:{project:'a'},elements:{query:{value:'cost'},enabled_only:{checked:true}},querySelector:()=>control};
 run(`api=async()=>{throw Error('offline')}`);await assert.rejects(run('sourceSearch(form)'),/offline/);assert.equal(control.disabled,false);
 let resolve;ctx.api=()=>new Promise(r=>resolve=r);
 const pending=run('sourceSearch(form)');run(`S.project={...S.project,id:'b'}`);resolve({query:'cost',results:[]});await pending;
 assert.equal(run('SourceUI.search.pid'),'other','response from old project must not replace new project state');
 ctx.api=async()=>({meaning:'scope',counts:{claims:0,sources:0,locatable:0,issues:0},issues:[],claims:[]});
 assert.equal(await run(`sourceAction('rt-source-audit',{disabled:false})`),true);
 assert.equal(run('SourceUI.audit.pid'),'b');assert.equal(await run(`sourceAction('rt-other',{})`),false);
 let scrolled=0;els['.rt-reader']={scrollIntoView(){scrolled++}};
 const resolvers=[];ctx.api=()=>new Promise(r=>resolvers.push(r));
 const oldPage=run(`rtLoadPage('first',1)`),newPage=run(`rtLoadPage('second',2)`);
 resolvers[1]({id:'second',page:2,text:'Current page'});await newPage;
 resolvers[0]({id:'first',page:1,text:'Old page'});await oldPage;
 assert.equal(run('RT.reader.id'),'second','late page response must not replace latest selection');assert.equal(scrolled,1);
 console.log('Sources UI escaping, source levels, stale audits, project isolation and failed-search recovery passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
