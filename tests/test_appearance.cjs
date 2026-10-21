const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const code=fs.readFileSync(require('node:path').join(__dirname,'../web/appearance.js'),'utf8');
function start(stored,blocked=false){
 const handlers={},attributes={},saved=[];
 const button={setAttribute:(name,value)=>attributes[name]=value};
 const document={documentElement:{dataset:{}},querySelectorAll:()=>[button],addEventListener:(name,fn)=>handlers[name]=fn};
 const localStorage={getItem:()=>{if(blocked)throw Error('unavailable');return stored},setItem:(...args)=>{if(blocked)throw Error('unavailable');saved.push(args)}};
 const window={};vm.runInNewContext(code,{document,window,localStorage,L:value=>value});
 return {document,window,attributes,saved,click:()=>handlers.click({target:{closest:()=>button}})};
}
let app=start(null);assert.equal(app.window.ResearchAppearance.current(),'light');
app.click();assert.equal(app.document.documentElement.dataset.theme,'dark');
assert.equal(app.attributes['aria-pressed'],'true');assert.equal(app.attributes['aria-label'],'切换为浅色主题');
assert.deepEqual(app.saved[0],['yanxu-appearance','dark']);
app.click();assert.equal(app.document.documentElement.dataset.theme,'light');
app=start('dark');assert.equal(app.document.documentElement.dataset.theme,'dark');
app=start('invalid');assert.equal(app.document.documentElement.dataset.theme,'light');
app=start('dark',true);assert.equal(app.document.documentElement.dataset.theme,'light');
assert.doesNotThrow(()=>app.click());assert.equal(app.document.documentElement.dataset.theme,'dark');
console.log('Appearance checks passed: defaults, persisted preference, toggle labels, storage unavailable.');
