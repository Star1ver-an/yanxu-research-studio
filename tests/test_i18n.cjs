const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const bundle=JSON.parse(fs.readFileSync(path.join(__dirname,'../resources/locales/messages.json'),'utf8'));
const doc={documentElement:{dataset:{},lang:''},title:''},saved=[];
const ctx=vm.createContext({window:{ResearchLocales:bundle},document:doc,localStorage:{getItem:()=>null,setItem:(...args)=>saved.push(args)}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/i18n.js'),'utf8'),ctx);
const run=code=>vm.runInContext(code,ctx);
run(`const stableLabel=L('研究项目');const userTitle='研究项目';const data=Locale.ui({title:userTitle,topic:'中文目标',outputs:{research:{text:'已有中文报告'}},assets:[{name:'研究项目'}],research:{tasks:[{title:'研究项目',description:'说明'}]},catalog:{flows:{full:{name:'完整研究流程',route:'ars',color:'green',stages:['research']}}},methods:[{id:'km',family:'临床与模型评估',label:'选择方法'}],messages:[{role:'user',text:'研究项目'},{role:'assistant',mode:'api',text:'原文回答'},{role:'assistant',mode:'local',text:'工作区帮助'}]});`);
for(const [locale,label]of [['zh-CN','研究项目'],['zh-TW','研究專案'],['en','Projects'],['ja','研究プロジェクト'],['ko','연구 프로젝트']]){
 run(`Locale.apply('${locale}')`);assert.equal(run('String(stableLabel)'),label);assert.equal(doc.documentElement.lang,locale);
 assert.equal(run('data.title'),'研究项目');assert.equal(run('data.topic'),'中文目标');assert.equal(run('data.outputs.research.text'),'已有中文报告');assert.equal(run('data.assets[0].name'),'研究项目');assert.equal(run('data.research.tasks[0].description'),'说明');
 assert.equal(run('data.methods[0].id'),'km');assert.equal(run('data.methods[0].family'),'临床与模型评估');assert.equal(run('data.catalog.flows.full.route'),'ars');assert.equal(run('data.catalog.flows.full.stages[0]'),'research');
 assert.equal(run('data.messages[0].text'),'研究项目');assert.equal(run('data.messages[1].text'),'原文回答');
}
run(`Locale.apply('en')`);
assert.equal(run('String(data.catalog.flows.full.name)'),'Full research workflow');assert.equal(run('String(data.messages[2].text)'),'Workspace help');
assert.equal(run("H`<button title=\"研究项目\">研究项目</button>`"),'<button title="Projects">Projects</button>');
assert.equal(run("H`<h1>${userTitle}</h1><button>研究项目</button>`"),'<h1>研究项目</h1><button>Projects</button>');
assert.equal(run("H`<input placeholder=\"项目名称\" value=\"${'原始内容'}\">`"),'<input placeholder="Project name" value="原始内容">');
const missing={};for(const locale of Object.keys(bundle)){for(const key of Object.keys(bundle['zh-CN'])){assert.ok(key in bundle[locale]);assert.deepEqual(bundle[locale][key].match(/\{\d+\}/g)?.sort()||[],key.match(/\{\d+\}/g)?.sort()||[]);}}
assert.equal(run(`Locale.text('No translation needed')`),'No translation needed');assert.equal(run(`JSON.stringify({text:L('保存')})`),'{'+'"text":"Save"}');
run(`const home=Locale.ui({projects:[{title:'研究项目',followup_next:{title:'开始「研究探索」',title_template:'开始「{0}」',title_args:['研究探索']}}]});`);
assert.equal(run('home.projects[0].title'),'研究项目');assert.equal(run('String(home.projects[0].followup_next.title)'),'Start “Research exploration”');
run(`const guide=Locale.ui({role:'assistant',mode:'local',text:'绘图入口',navigation_guide:{target:'drawing',project:true}});`);
assert.ok(run('String(guide.text)').includes('Project → Research tools'));assert.ok(!/[\u3400-\u9fff]/.test(run('String(guide.text)')));
run(`Locale.apply('ja')`);assert.ok(run('String(guide.text)').includes('プロジェクト'));assert.equal(run('home.projects[0].title'),'研究项目');
console.log('Localization checks passed: five languages, live labels, placeholders, attributes, canonical IDs/filter values and untouched user data.');
