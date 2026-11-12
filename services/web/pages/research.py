"""개인 리서치 화면. 정적 셸이고 값은 로그인한 계정의 `/api/research`가 채운다.

운영자 봇 리서치와 같은 분석이다(`services/web/personal_research.py`). 주제·관심종목 이름이 외부 AI로
가므로 동의 전에는 실행 버튼을 잠근다. 결과의 모델 문자열은 전부 `esc()`를 거친다.
"""
from services.web.core import config
from services.web.pages.shell import I_LAYERS, JS_UTIL, h1, page

_MAIN = h1(I_LAYERS, "내 리서치") + """
<p class='sub2'>자연어로 적은 관심 주제와 관심종목을 바탕으로 최근 원문 뉴스·후보 종목·섹터 흐름을 AI가 분석합니다.</p>
<section id='r-login' class='login-panel'>
 <div class='eyebrow'>개인 리서치</div><h2>내 관점으로 읽는 시장</h2>
 <p>관심 주제, 관심종목, 리서치 기록은 로그인한 계정에만 저장됩니다.</p>
 <a class='action primary' href='/auth/google?next=/research'>Google 계정으로 로그인</a>
 <p class='sub2'>이름·이메일·프로필 사진은 요청하지 않습니다. <a href='/privacy'>개인정보 처리방침</a></p>
</section>
<div id='r-app' hidden>
 <div class='workspace-toolbar'><span class='eyebrow'>내 작업 공간</span>
 <div><a href='/portfolio'>관심종목·계정 관리</a> <button id='r-logout' class='action'>로그아웃</button></div></div>
 <section id='r-consent' class='histbox' hidden>
  <h2 class='histh'>AI 분석 동의</h2>
  <p class='body-text'>리서치를 실행하면 <b>입력한 주제와 관심종목 이름·코드</b>를 서버가 수집한 공개 원문 뉴스·후보 종목·섹터 요약과 함께
  <b>Cloudflare Workers AI</b>로 보내 분석을 받습니다. 계정 식별값과 내 자산(금액·종류·메모)은 보내지 않습니다.
  분석 결과는 이 계정에만 저장되며, 동의는 언제든 철회할 수 있습니다.</p>
  <p class='sub2'>주제에 실명·연락처·계좌 등 개인정보를 적지 마세요. <a href='/privacy'>개인정보 처리방침</a></p>
  <label class='sub2'><input type='checkbox' id='r-agree'> 위 내용을 확인했고 주제·관심종목을 AI 분석에 보내는 데 동의합니다.</label>
  <p><button class='action primary' id='r-consent-btn' type='button' disabled>동의하고 시작</button></p>
 </section>
 <form id='r-form' class='research-form'>
  <label>관심 주제(자연어)<textarea id='r-topic' maxlength='""" + str(config.RESEARCH_TOPIC_MAX_CHARS) + """' rows='3'
   placeholder='예: 미국 국채금리가 최근 올랐는데 앞으로 더 오를 수 있을까? 금리 부담이 기술주에 어떤 영향을 줄까?'></textarea></label>
  <button class='action' type='submit'>주제 저장</button>
  <button class='action primary' id='r-create' type='button'>리서치 실행</button>
 </form>
 <p class='sub2' id='r-status'></p>
 <p class='sub2' id='r-consented' hidden>AI 분석에 동의함 · <button class='linklike' id='r-withdraw' type='button'>동의 철회</button></p>
 <section class='histbox'><h2 class='histh'>리서치 결과</h2>
  <dl class='brief research-meta'><div><dt>분석 시각</dt><dd id='r-time'>–</dd></div><div><dt>시장 자료 기준</dt><dd id='r-basis'>–</dd></div></dl>
  <p id='r-topic-used' class='sub2'></p>
  <p id='r-summary' class='body-text'>아직 리서치 결과가 없습니다.</p>
  <div id='r-actions'></div><div id='r-risks'></div><div id='r-critique'></div>
  <p class='sub2'>AI 분석은 오류가 있을 수 있고 매수·매도 판단이 아닙니다. 근거 기사의 원문을 확인하세요.
  관심종목 제안은 '적용'을 눌러야 내 관심종목에 반영됩니다.</p>
 </section>
 <section class='histbox' id='r-history-box' hidden><h2 class='histh'>이전 분석</h2><div id='r-history'></div></section>
</div><p id='r-message' role='status' aria-live='polite'></p>
"""

_SCRIPT = "<script>" + JS_UTIL + """
const el=id=>document.getElementById(id);
const ACTION={add:'추가 제안',remove:'삭제 제안',watch:'관찰'};
let state=null,poll=null;
async function api(path,opt={}){
 const r=await fetch('/api/research'+path,{credentials:'same-origin',...opt,headers:opt.body?{'Content-Type':'application/json'}:{}});
 if(r.status===401){location.replace('/research');throw new Error('로그인이 만료되었습니다.');}
 const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'요청을 처리하지 못했습니다.');return d;
}
const share=v=>v==null?'–':Math.round(Number(v)*100)+'%';
function evidence(list){
 return (list||[]).filter(Boolean).map(e=>"<li>"+(/^https?:\\/\\//.test(e.url||'')?"<a href='"+esc(e.url)+"' target='_blank' rel='noopener noreferrer'>"+esc(e.title||'원문')+" ↗</a>":esc(e.title||''))
  +" <span class='sub2'>"+esc(e.source||'')+"</span></li>").join('');
}
function renderReport(rep){
 if(!rep){return;}
 const res=rep.result||{},applied=rep.applied||{};
 el('r-time').textContent=stamp(rep.generated_at)+' (한국 시간)';
 el('r-basis').textContent=rep.inputs_generated_at?stamp(rep.inputs_generated_at)+' · 뉴스 '+esc(rep.news_count)+'건':'–';
 el('r-topic-used').textContent='주제: '+(rep.topic||'');
 el('r-summary').textContent=res.summary||'';
 const acts=res.actions||[];
 el('r-actions').innerHTML=acts.length?"<h3>관심종목 제안</h3>"+acts.map(a=>{
  const done=applied[a.ticker]===a.action,can=a.action==='add'||a.action==='remove';
  return "<article class='news-row'><div class='news-meta'>"+esc(ACTION[a.action]||a.action)+" · 확신 "+share(a.confidence)+" · 관련도 "+share(a.relevance)+"</div>"
   +"<h4>"+esc(a.name||a.ticker)+" <small>"+esc(a.ticker)+"</small></h4><p>"+esc(a.reason||'')+"</p>"
   +(a.evidence&&a.evidence.length?"<ul class='sub2'>"+evidence(a.evidence)+"</ul>":'')
   +(can?(done?"<p class='sub2'>적용함</p>":"<button class='action' type='button' data-ticker='"+esc(a.ticker)+"' data-action='"+esc(a.action)+"'>"+(a.action==='add'?'관심종목에 추가':'관심종목에서 삭제')+"</button>"):'')
   +"</article>";}).join(''):"<p class='sub2'>이번 분석에는 관심종목 변경 제안이 없습니다.</p>";
 el('r-risks').innerHTML=(res.risks||[]).length?"<h3>리스크</h3><ul>"+res.risks.map(r=>"<li>"+esc(r)+"</li>").join('')+"</ul>":'';
 el('r-critique').innerHTML=(res.view_critique||[]).length?"<h3>주제에 대한 반론</h3><ul>"+res.view_critique.map(c=>"<li>"+esc(c.point)
  +(c.evidence?" <ul class='sub2'>"+evidence([c.evidence])+"</ul>":'')+"</li>").join('')+"</ul>":'';
 el('r-actions').querySelectorAll('button[data-ticker]').forEach(b=>b.addEventListener('click',async()=>{
  b.disabled=true;try{await api('/actions',{method:'POST',body:JSON.stringify({ticker:b.dataset.ticker,action:b.dataset.action})});
  el('r-message').textContent='관심종목에 반영했습니다.';await load();}catch(e){el('r-message').textContent=e.message;b.disabled=false;}}));
}
function render(d){
 state=d;
 const consented=!!d.profile.consented_at;
 el('r-consent').hidden=consented;el('r-consented').hidden=!consented;
 if(document.activeElement!==el('r-topic'))el('r-topic').value=d.profile.topic||'';
 const run=d.run,running=run&&run.status==='running';
 el('r-create').disabled=!consented||running||!d.inputs.ready;
 el('r-status').textContent=!d.inputs.ready?'분석할 최신 시장 자료를 준비하고 있습니다. 잠시 뒤 다시 시도하세요.'
  :running?'분석 중입니다(최대 10분). 화면을 닫아도 분석은 계속되고, 끝나면 이 화면에 결과가 나옵니다.'
  :(run&&run.status==='failed'?(run.error||'리서치를 실행하지 못했습니다.')+' ':'')+'시장 자료 기준 '+stamp(d.inputs.generated_at)+' · 하루 '+d.limits.daily+'회까지 실행할 수 있습니다.';
 renderReport(d.report);
 el('r-history-box').hidden=!(d.history||[]).length;
 el('r-history').innerHTML=(d.history||[]).slice().reverse().map(h=>"<p class='sub2'><b>"+esc(stamp(h.generated_at))+"</b> "+esc(h.summary)+"</p>").join('');
 clearTimeout(poll);if(running)poll=setTimeout(load,10000);
}
async function load(){render(await api(''));}
async function save(){return api('/profile',{method:'PUT',body:JSON.stringify({topic:el('r-topic').value})});}
el('r-agree').addEventListener('change',()=>{el('r-consent-btn').disabled=!el('r-agree').checked;});
el('r-consent-btn').addEventListener('click',async()=>{try{await api('/consent',{method:'PUT',body:JSON.stringify({agree:true})});await load();}catch(e){el('r-message').textContent=e.message;}});
el('r-withdraw').addEventListener('click',async()=>{try{await api('/consent',{method:'PUT',body:JSON.stringify({agree:false})});el('r-agree').checked=false;el('r-consent-btn').disabled=true;await load();el('r-message').textContent='동의를 철회했습니다. 다시 동의하기 전에는 리서치를 실행하지 않습니다.';}catch(e){el('r-message').textContent=e.message;}});
el('r-form').addEventListener('submit',async e=>{e.preventDefault();try{await save();el('r-message').textContent='주제를 저장했습니다.';}catch(e){el('r-message').textContent=e.message;}});
el('r-create').addEventListener('click',async()=>{
 el('r-create').disabled=true;
 try{await save();await api('/reports',{method:'POST'});el('r-message').textContent='리서치를 시작했습니다.';await load();}
 catch(e){el('r-message').textContent=e.message;await load().catch(()=>{});}
});
el('r-logout').addEventListener('click',async()=>{const r=await fetch('/api/account/session',{method:'DELETE'});if(r.ok)location.replace('/research');});
window.addEventListener('pageshow',e=>{if(e.persisted)location.reload();});
fetch('/api/account/session').then(r=>r.json()).then(async s=>{
 if(!s.configured){el('r-message').textContent='Google 로그인을 준비 중입니다.';el('r-login').querySelector('.action').hidden=true;return;}
 if(!s.unlocked)return;el('r-login').hidden=true;el('r-app').hidden=false;await load();
}).catch(e=>el('r-message').textContent=e.message);
</script>"""

RESEARCH_HTML = page("내 리서치", "/research", _MAIN, _SCRIPT,
                     description="Google 로그인 후 자연어로 적은 관심 주제와 관심종목을 바탕으로 최근 원문 뉴스·후보 종목·섹터 흐름을 AI가 분석하는 개인 리서치입니다.")
