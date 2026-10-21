/* Local appearance only. Never changes project records or connection settings. */
(() => {
 const key='yanxu-appearance';
 let theme='light';
 try { if(localStorage.getItem(key)==='dark')theme='dark'; } catch {}
 function apply(next) {
  theme=next==='dark'?'dark':'light';
  document.documentElement.dataset.theme=theme;
  document.querySelectorAll('[data-action="toggle-theme"]').forEach(button=>{
   button.setAttribute('aria-pressed',String(theme==='dark'));
   button.setAttribute('aria-label',theme==='dark'?L('切换为浅色主题'):L('切换为深色主题'));
   button.title=theme==='dark'?L('切换为浅色主题'):L('切换为深色主题');
  });
 }
 apply(theme);
 window.ResearchAppearance={current:()=>theme};
 document.addEventListener('click',event=>{
  if(!event.target.closest('[data-action="toggle-theme"]'))return;
  apply(theme==='dark'?'light':'dark');
  try { localStorage.setItem(key,theme); } catch {}
 });
})();
