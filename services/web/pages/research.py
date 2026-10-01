"""Private research workspace. Static shell, authenticated personal data."""
from services.web.pages.shell import I_LAYERS, JS_UTIL, h1, page

_MAIN = h1(I_LAYERS, "내 리서치") + """
<p class='sub2'>관심 주제와 종목에 맞춰 공개 뉴스와 시장 자료를 정리합니다.</p>
<section id='r-login' class='login-panel'>
 <div class='eyebrow'>개인 리서치</div><h2>내 관심사로 읽는 시장</h2>
 <p>관심 주제, 관심종목, 리서치 기록은 로그인한 계정에만 저장됩니다.</p>
 <a class='action primary' href='/auth/google?next=/research'>Google 계정으로 로그인</a>
 <p class='sub2'>이름·이메일·프로필 사진은 요청하지 않습니다. <a href='/privacy'>개인정보 처리방침</a></p>
</section>
<div id='r-app' hidden>
 <div class='workspace-toolbar'><span class='eyebrow'>내 작업 공간</span>
 <div><a href='/portfolio'>관심종목·계정 관리</a> <button id='r-logout' class='action'>로그아웃</button></div></div>
 <form id='r-form' class='research-form'>
  <label>관심 주제<input id='r-topic' maxlength='200' placeholder='예: 반도체, 미국 금리' autocomplete='off'></label>
  <label>시장<select id='r-market'><option value=''>전체 시장</option><option value='KR'>한국</option><option value='US'>미국</option><option value='CN'>중국</option><option value='HK'>홍콩</option><option value='JP'>일본</option><option value='EU'>유럽</option></select></label>
  <label>자료 기간<select id='r-days'><option value='7'>최근 7일</option><option value='14'>최근 14일</option><option value='30'>최근 30일</option></select></label>
  <button class='action' type='submit'>조건 저장</button>
  <button class='action primary' id='r-create' type='button'>리서치 만들기</button>
 </form>
 <label class='sub2'><input type='checkbox' id='r-ai'> 공개 근거에 대한 AI 해설 추가 (선택)</label>
 <p class='sub2'>선택하면 공개 뉴스·시장 요약 최대 15건을 Cloudflare Workers AI로 보내 해설을 받습니다.
 계정 식별값·내 자산·입력한 주제·관심종목 목록은 보내지 않습니다. 선택된 자료에서 관심 분야가 추론될 수 있습니다.</p>
 <p class='sub2'>실명·연락처 등 개인정보는 주제에 입력하지 마세요. 관심종목은 내 자산에서 관리합니다.</p>
 <section class='histbox'><h2 class='histh'>연구 결과</h2>
  <dl class='brief research-meta'><div><dt>자료 정리 시각</dt><dd id='r-time'>–</dd></div><div><dt>보관 범위</dt><dd>최근 결과 한 건</dd></div></dl>
  <p id='r-summary' class='body-text'>아직 저장된 리서치가 없습니다.</p>
  <section id='r-analysis-box' hidden><h3>공개 근거 해설</h3><p id='r-analysis' class='body-text'></p><p class='sub2'>AI 해설은 오류가 있을 수 있습니다. 아래 번호별 근거와 원문을 확인하세요.</p><div id='r-analysis-sources'></div></section>
  <div id='r-sections'></div><p id='r-method' class='sub2'></p>
 </section>
</div><p id='r-message' role='status' aria-live='polite'></p>
"""

_SCRIPT = "<script>" + JS_UTIL + """
const el=id=>document.getElementById(id);
async function api(path,opt={}){
 const r=await fetch('/api/research'+path,{credentials:'same-origin',...opt,headers:opt.body?{'Content-Type':'application/json'}:{}});
 if(r.status===401){location.replace('/research');throw new Error('로그인이 만료되었습니다.');}
 const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'요청을 처리하지 못했습니다.');return d;
}
function render(d){
 if(!d)return;el('r-time').textContent=stamp(d.generated_at)+' (한국 시간)';el('r-summary').textContent=d.summary;
 el('r-method').textContent=d.method+' '+d.limitations;
 el('r-analysis-box').hidden=!d.analysis;el('r-analysis').textContent=d.analysis||'';
 el('r-analysis-sources').innerHTML=(d.analysis_sources||[]).map(a=>'<p class="sub2">['+esc(a.number)+'] '+esc(a.title||'시장 요약')+' · '+esc(a.date)+'</p>').join('');
 if(!d.analysis&&d.analysis_status!=='not_requested')el('r-method').textContent+=' AI 해설을 만들지 못해 근거 자료만 저장했습니다.';
 el('r-sections').innerHTML=d.sections.map(s=>"<section class='evidence-section'><h3>"+esc(s.query)+" <small>관련 자료 "+esc(s.total)+"건</small></h3>"+
 (s.evidence.length?s.evidence.map(a=>"<article class='news-row'><div class='news-meta'>"+esc(a.market_label||'')+" · "+esc(a.source||'시장 요약')+" · "+esc(a.date)+"</div><h4>"+esc(a.title||'시장 요약')+"</h4><p>"+esc(a.text||'')+"</p>"+
 (/^https?:\\/\\//.test(a.url||'')?"<a href='"+esc(a.url)+"' target='_blank' rel='noopener noreferrer'>원문 확인 ↗</a>":'')+"</article>").join(''):"<p class='sub2'>이 조건에 일치하는 자료가 없습니다.</p>")+"</section>").join('');
}
async function save(){return api('/profile',{method:'PUT',body:JSON.stringify({topic:el('r-topic').value,market:el('r-market').value,days:Number(el('r-days').value)})});}
el('r-form').addEventListener('submit',async e=>{e.preventDefault();try{await save();el('r-message').textContent='조건을 저장했습니다. 기존 결과는 생성 당시 조건으로 남아 있습니다.';}catch(e){el('r-message').textContent=e.message;}});
el('r-create').addEventListener('click',async()=>{
 el('r-create').disabled=true;el('r-message').textContent='관련 자료를 정리하는 중입니다.';
 try{await save();render(await api('/reports',{method:'POST',body:JSON.stringify({public_evidence_ai:el('r-ai').checked})}));el('r-message').textContent='리서치를 저장했습니다.';}catch(e){el('r-message').textContent=e.message;}finally{el('r-create').disabled=false;}
});
el('r-logout').addEventListener('click',async()=>{const r=await fetch('/api/account/session',{method:'DELETE'});if(r.ok)location.replace('/research');});
window.addEventListener('pageshow',e=>{if(e.persisted)location.reload();});
fetch('/api/account/session').then(r=>r.json()).then(async s=>{
 if(!s.configured){el('r-message').textContent='Google 로그인을 준비 중입니다.';el('r-login').querySelector('.action').hidden=true;return;}
 if(!s.unlocked)return;el('r-login').hidden=true;el('r-app').hidden=false;
 const d=await api('');el('r-topic').value=d.profile.topic;el('r-market').value=d.profile.market;el('r-days').value=d.profile.days;render(d.report);
}).catch(e=>el('r-message').textContent=e.message);
</script>"""

RESEARCH_HTML = page("내 리서치", "/research", _MAIN, _SCRIPT,
                     description="Google 로그인 후 관심 주제와 관심종목에 맞는 뉴스·시장 자료를 모으고 나만의 리서치 결과를 저장하는 개인 서비스입니다.")
