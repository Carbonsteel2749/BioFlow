'use strict';
let bioflowUser=null;
let bioflowCsrf='';
function guestView(){
 return `<div class="welcome"><div class="welcome-orbit" aria-hidden="true"><span></span><span></span><span></span><i>BF</i></div><div class="eyebrow">RESEARCH, CONNECTED</div><h1>让研究，<br>从此连贯。</h1><p>文献、数据、分析与写作，<br>在同一个科研工作空间中继续。</p><div class="welcome-actions"><button class="primary" data-action="signin">登录，进入工作空间 <span>↗</span></button><button data-action="signup">创建账户</button></div><div class="welcome-features"><span>文献证据</span><span>分析流程</span><span>科研图表</span><span>论文写作</span></div></div>`;
}
function showAuth(mode='login'){
 const overlay=document.querySelector('#ai4s-prototype .overlay');
 overlay.hidden=false;
 overlay.innerHTML=`<div class="auth-backdrop"><section class="auth-modal" data-auth-modal role="dialog" aria-modal="true" aria-label="${mode==='register'?'注册':'登录'} BioFlow"><div class="auth-heading"><span class="auth-wordmark">BioFlow<span> / RESEARCH STUDIO</span></span><button data-auth-action="close" aria-label="关闭登录窗口">×</button></div><div class="auth-tabs"><button data-auth-action="login" class="${mode==='login'?'selected':''}">登录</button><button data-auth-action="register" class="${mode==='register'?'selected':''}">注册</button></div><h2>${mode==='register'?'建立个人研究空间':'继续研究旅程'}</h2><p class="muted">${mode==='register'?'项目与研究记录保存在自己的账户中。':'使用账户访问项目、资料和研究记录。'}</p><form data-auth-form="${mode}"><label>用户名<input name="username" autocomplete="username" pattern="[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}" minlength="3" maxlength="32" required placeholder="输入用户名"></label><label>密码<input name="password" type="password" autocomplete="${mode==='login'?'current-password':'new-password'}" minlength="8" maxlength="128" required placeholder="输入密码"></label>${mode==='register'?'<label>确认密码<input name="confirm" type="password" autocomplete="new-password" minlength="8" required placeholder="再次输入密码"></label>':''}<div class="auth-error" role="alert"></div><button class="primary" type="submit">${mode==='register'?'注册并进入工作台':'登录工作台'} <span>→</span></button></form><p class="auth-footnote">BioFlow · Your research, in flow.</p></section></div>`;
 overlay.querySelector('[name="username"]').focus();
}
function updateAccount(){
 const button=document.querySelector('.avatar');
 if(button){button.textContent=bioflowUser?bioflowUser.username.slice(0,1).toUpperCase():'↗';button.title=bioflowUser?bioflowUser.username:'登录 / 注册';button.setAttribute('aria-label',bioflowUser?'账户 '+bioflowUser.username:'登录或注册');}
 const caption=document.querySelector('.project-head .caption');
 if(caption)caption.textContent=bioflowUser?.role==='developer'?'开发者项目':'我的项目';
 const settings=document.querySelector('[data-action="settings"]');
 if(settings)settings.textContent=bioflowUser?.role==='developer'?'服务设置':'服务状态';
}
async function authSuccess(user){
 bioflowUser=user;bioflowCsrf=user.csrf;
 state.project=localStorage.getItem('bioflow.project.'+user.id)||'';
 state.page='home';state.session='';state.task='';state.chapter='';
 document.querySelector('.overlay').hidden=true;
 updateAccount();
 await loadProjects();
}
document.addEventListener('click',async event=>{
 const authButton=event.target.closest('[data-auth-action]');
 if(authButton){event.preventDefault();event.stopImmediatePropagation();if(authButton.dataset.authAction==='close')document.querySelector('.overlay').hidden=true;else showAuth(authButton.dataset.authAction);return;}
 const control=event.target.closest('button,a,input,select,textarea');
 if(!control||control.closest('[data-auth-modal]')||!control.closest('#ai4s-prototype'))return;
 if(!bioflowUser&&control.matches('[data-home-command]'))return;
 if(!bioflowUser){event.preventDefault();event.stopImmediatePropagation();showAuth(control.dataset.action==='signup'?'register':'login');return;}
 if(control.classList.contains('avatar')||control.dataset.action==='account'){
   event.preventDefault();event.stopImmediatePropagation();modal('账户',`<p class="account-name">${esc(bioflowUser.username)}</p><p class="muted">${bioflowUser.role==='developer'?'开发者账户':'研究者账户'}</p><div class="row"><button data-action="password">修改密码</button><button data-action="signout">退出登录</button></div>`);
 }
},true);
document.addEventListener('submit',async event=>{
 const form=event.target.closest('[data-auth-form]');if(!form)return;
 event.preventDefault();event.stopImmediatePropagation();const error=form.querySelector('.auth-error');const submit=form.querySelector('[type=submit]');submit.disabled=true;error.textContent='';
 try{const data=new FormData(form);if(data.get('confirm')!==null&&data.get('password')!==data.get('confirm'))throw Error('两次输入的密码不同');const response=await fetch('/api/auth/'+form.dataset.authForm,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:data.get('username'),password:data.get('password')})});const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'请检查用户名和密码');await authSuccess(result.user);}catch(e){error.textContent=e.message;}finally{submit.disabled=false;}
},true);
