"""개인 리서치 화면. 정적 셸이고 값은 로그인한 계정의 `/api/research`가 채운다.

**리서치**(`services/web/personal_research.py`)는 운영자 봇 리서치와 같은 분석으로 뉴스에서 뷰(주제)와 맞는 종목을
찾는다. 주제·관심종목 이름이 외부 AI로 가므로 동의 전에는 실행 버튼을 잠근다. 결과의 모델 문자열은 전부
`esc()`를 거친다.

**뷰 검증**(`services/web/viewcheck/`)은 같은 뷰를 집단 예측 컨센서스에 대조하고 다듬은 뷰로 ETF·ETN·관심종목의
포지션 아이디어를 낸다. **운영자 개인용이라 다른 계정에는 보이지 않는다** — 버튼과 섹션은 `hidden`으로 실려 있고
`/api/research`가 `view_check: true`를 줄 때만 드러난다. 공개 문구(안내·동의·설명)에는 이 기능을 적지 않는다.
확률 막대의 폭은 `data-width`로 싣고 스크립트가 넣는다 — CSP가 인라인 style 속성을 막는다.
"""
from services.web.core import config
from services.web.pages.shell import I_LAYERS, JS_UTIL, h1, page

_MAIN = """<style>
.vc-claim{padding:18px 0;border-bottom:1px solid var(--line)}.vc-claim h4{margin:6px 0;font-size:16px;overflow-wrap:anywhere}
.vc-claim>p{font-size:14px;line-height:1.8;margin:6px 0;color:var(--ink-soft)}
.vc-signal{margin:10px 0 0;padding:10px 12px;background:var(--fill-1)}.vc-q{font-size:var(--fs-sm);overflow-wrap:anywhere}
.vc-opt{display:grid;grid-template-columns:minmax(90px,200px) 1fr auto auto;gap:8px;align-items:center;font-size:var(--fs-sm);margin-top:6px}
.vc-opt b{font-variant-numeric:tabular-nums}.vc-opt.on span:first-child{font-weight:700;color:var(--ink)}
.vc-bar{height:8px;background:var(--fill-2);overflow:hidden}.vc-bar i{display:block;height:100%;background:var(--gold)}
.vc-move{font-size:var(--fs-xs);font-variant-numeric:tabular-nums;white-space:nowrap}
.vc-alert{border-left:3px solid var(--gold);background:var(--fill-1);padding:10px 14px;margin:14px 0;color:var(--ink-soft);font-size:var(--fs-sm)}
.vc-refined{border-top:1px solid var(--rule);border-bottom:1px solid var(--line);padding:16px 0;margin:24px 0}
.vc-caution{color:var(--warnc)}
@media(max-width:520px){.vc-opt{grid-template-columns:1fr auto}.vc-bar{display:none}.vc-move{grid-column:1/-1}}
</style>""" + h1(I_LAYERS, "내 리서치") + """
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
  <button class='action primary' id='v-create' type='button' hidden>컨센서스로 뷰 검증</button>
  <button class='action primary' id='r-create' type='button'>리서치 실행</button>
 </form>
 <p class='sub2' id='v-status' hidden></p>
 <p class='sub2' id='r-status'></p>
 <p class='sub2' id='r-consented' hidden>AI 분석에 동의함 · <button class='linklike' id='r-withdraw' type='button'>동의 철회</button></p>
 <section class='histbox' id='v-box' hidden><h2 class='histh'>뷰 검증 — 컨센서스와 포지션 아이디어</h2>
  <p class='sub2'>뷰를 예상 하나하나로 나눠, 참여자들이 실제로 무게를 실은 기대(집단 예측 컨센서스)와 대조합니다.
  확률과 상품 정보는 서버가 붙인 값이고, 판정과 이유는 AI가 씁니다. 1~5분 걸립니다.</p>
  <dl class='brief research-meta'><div><dt>검증 시각</dt><dd id='v-time'>–</dd></div><div><dt>컨센서스 수집 시각</dt><dd id='v-basis'>–</dd></div></dl>
  <p id='v-view-used' class='sub2'></p>
  <div id='v-drift'></div>
  <div id='v-claims'><p class='sub2'>아직 검증한 뷰가 없습니다. 뷰를 적고 "컨센서스로 뷰 검증"을 눌러 보세요.</p></div>
  <div id='v-refined'></div><div id='v-blind'></div><div id='v-positions'></div>
  <p class='sub2' id='v-disclaimer'></p>
 </section>
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
const VERDICT={supported:['컨센서스가 받침','tag-add'],contradicted:['컨센서스와 어긋남','tag-remove'],mixed:['엇갈림','tag-watch'],unpriced:['확인할 질문 없음','tag-watch']};
const STANCE={long:['비중 확대 검토','tag-add'],reduce:['비중 축소 검토','tag-remove'],hedge:['헤지 검토','tag-watch'],wait:['지켜보기','tag-watch']};
const MARKET={KR:'한국 상장',US:'미국 상장'};
let state=null,poll=null,view=null,viewPoll=null;
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
const prob=v=>(Number(v)*100).toFixed(1).replace(/\\.0$/,'')+'%';
// 검증 때 보인 확률과 지금 수집분의 차이. 질문이 닫혔으면 그렇게 적는다.
function shift(sig,opt,drift){
 const now=drift&&drift.now?drift.now[sig.id]:undefined;if(now===undefined)return null;
 if(now===null||now[opt.label]==null)return NaN;return now[opt.label]-opt.probability;
}
function moved(d){
 if(d===null)return '';if(Number.isNaN(d))return "<span class='vc-move'>지금은 닫힘</span>";
 if(Math.abs(d)<0.005)return "<span class='vc-move'></span>";
 return "<span class='vc-move "+(d>0?'pos':'neg')+"'>검증 뒤 "+(d>0?'+':'−')+(Math.abs(d)*100).toFixed(1)+"%p</span>";
}
function signal(s,drift){
 return "<div class='vc-signal'><div class='vc-q'><b>"+esc(s.summary||s.title)+"</b> <span class='sub2'>"+esc(s.title)+" · 마감 "+esc(String(s.end_date||'').slice(0,10))+"</span></div>"
  +(s.options||[]).map(o=>"<div class='vc-opt"+(o.focus?" on":"")+"'><span>"+esc(o.label)+"</span><span class='vc-bar'><i data-width='"+Math.max(0,Math.min(100,o.probability*100)).toFixed(1)+"%'></i></span><b>"+prob(o.probability)+"</b>"+moved(shift(s,o,drift))+"</div>").join('')+"</div>";
}
function renderView(d){
 view=d;const rep=d.report,run=d.run,running=run&&run.status==='running',consented=!!(state&&state.profile.consented_at);
 el('v-create').disabled=!consented||running||!d.ready;
 el('v-status').textContent=!d.ready?'예측 자료를 준비하고 있습니다. 잠시 뒤 다시 시도하세요.'
  :running?'뷰를 검증하고 있습니다(1~5분). 화면을 닫아도 계속되고, 끝나면 이 화면에 결과가 나옵니다.'
  :(run&&run.status==='failed'?(run.error||'뷰 검증을 실행하지 못했습니다.')+' ':'')+'뷰 검증 오늘 '+d.usage.used+'/'+d.usage.limit+'회 사용';
 clearTimeout(viewPoll);if(running)viewPoll=setTimeout(()=>loadView().catch(()=>{}),5000);
 if(!rep)return;
 const drift=d.drift,claims=rep.claims||[];
 el('v-time').textContent=stamp(rep.generated_at)+' (한국 시간)';
 el('v-basis').textContent=stamp(rep.consensus_generated_at)+(drift&&drift.generated_at&&drift.generated_at!==rep.consensus_generated_at?' → 지금 '+stamp(drift.generated_at):'');
 el('v-view-used').textContent='검증한 뷰: '+(rep.view||'');
 const sigs=claims.flatMap(c=>c.signals||[]).concat((rep.blind_spots||[]).map(b=>b.signal));
 const big=Math.max(0,...sigs.flatMap(s=>(s.options||[]).map(o=>{const v=shift(s,o,drift);return v===null||Number.isNaN(v)?0:Math.abs(v);})));
 el('v-drift').innerHTML=drift&&big>=drift.alert?"<p class='vc-alert'>검증한 뒤 컨센서스가 크게 움직인 질문이 있습니다(가장 큰 이동 "+(big*100).toFixed(1)+"%p). 뷰를 다시 검증해 보세요.</p>":'';
 el('v-claims').innerHTML="<h3>뷰의 전제와 컨센서스</h3>"+claims.map(c=>{const v=VERDICT[c.verdict]||[c.verdict,'tag-watch'];
  return "<div class='vc-claim'><span class='tag "+v[1]+"'>"+esc(v[0])+"</span><h4>"+esc(c.c)+". "+esc(c.claim)+"</h4>"
   +(c.note?"<p>"+esc(c.note)+"</p>":'')+(c.signals||[]).map(s=>signal(s,drift)).join('')+"</div>";}).join('');
 el('v-refined').innerHTML=rep.refined_view?"<div class='vc-refined'><h3>컨센서스에 비춰 다듬은 뷰</h3><p class='body-text'>"+esc(rep.refined_view)+"</p>"
  +"<p><button class='action' type='button' id='v-adopt'>이 뷰를 내 주제로 쓰기</button></p></div>":'';
 el('v-blind').innerHTML=(rep.blind_spots||[]).length?"<h3>뷰가 다루지 않은 주요 컨센서스</h3>"+rep.blind_spots.map(b=>"<div class='vc-claim'><p>"+esc(b.note)+"</p>"+signal(b.signal,drift)+"</div>").join(''):'';
 const names=Object.fromEntries(claims.map(c=>[c.c,c.claim])),watched=rep.watched||[];
 const pos=rep.positions||[];
 el('v-positions').innerHTML="<h3>포지션 아이디어</h3>"+(pos.length?pos.map(p=>{const st=STANCE[p.stance]||[p.stance,'tag-watch'],done=watched.includes(p.code);
  return "<article class='news-row'><div class='news-meta'><span class='tag "+st[1]+"'>"+esc(st[0])+"</span> "+esc(p.kind)+(p.market?" · "+esc(MARKET[p.market]||p.market):'')
   +(p.theme?" · "+esc(p.theme):'')+(p.direction?" · "+esc(p.direction):'')+(p.tracks?" · "+esc(p.tracks):'')+"</div>"
   +"<h4>"+esc(p.name)+" <small>"+esc(p.code)+"</small></h4><p>"+esc(p.reason)+"</p>"
   +(p.invalidation?"<p class='sub2'><b>"+(p.stance==='wait'?'방향을 정할 조건':'접는 조건')+"</b> "+esc(p.invalidation)+"</p>":'')
   +"<p class='sub2'><b>근거 전제</b> "+(p.claims||[]).map(c=>esc(c)+". "+esc(names[c]||'')).join(' / ')+"</p>"
   +((p.alternatives||[]).length?"<p class='sub2'><b>같은 성격의 다른 상품</b> "+p.alternatives.map(a=>esc(a.name)+" ("+esc(MARKET[a.market]||a.market)+" "+esc(a.kind)+")").join(', ')+"</p>":'')
   +(p.cautions||[]).map(c=>"<p class='sub2 vc-caution'>"+esc(c)+"</p>").join('')
   +(done?"<p class='sub2'>관심종목에 담음</p>":"<button class='action' type='button' data-code='"+esc(p.code)+"'>관심종목에 담기</button>")+"</article>";}).join('')
  :"<p class='sub2'>"+(rep.positions_status==='failed'?'포지션 아이디어를 만들지 못했습니다. 판정은 위와 같습니다. 다시 검증하면 새로 만듭니다.'
   :rep.positions_status==='skipped'?'컨센서스로 확인한 전제가 없어 포지션 아이디어를 내지 않았습니다.':'이 뷰에서 이어지는 포지션 아이디어가 없습니다.')+"</p>");
 el('v-disclaimer').textContent=rep.disclaimer||'';
 document.querySelectorAll('#v-box [data-width]').forEach(b=>{b.style.width=b.dataset.width});
 const adopt=el('v-adopt');if(adopt)adopt.addEventListener('click',async()=>{
  el('r-topic').value=rep.refined_view.slice(0,el('r-topic').maxLength);
  try{await save();el('r-message').textContent='다듬은 뷰를 주제로 저장했습니다. 이 뷰로 다시 검증하거나 리서치를 실행할 수 있습니다.';await load();}catch(e){el('r-message').textContent=e.message;}});
 el('v-positions').querySelectorAll('button[data-code]').forEach(b=>b.addEventListener('click',async()=>{
  b.disabled=true;try{await api('/view/watch',{method:'POST',body:JSON.stringify({code:b.dataset.code})});
  el('r-message').textContent='관심종목에 담았습니다.';await loadView();}catch(e){el('r-message').textContent=e.message;b.disabled=false;}}));
}
async function loadView(){renderView(await api('/view'));}
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
async function load(){
 const d=await api('');render(d);
 // 뷰 검증은 운영자 계정에만 있다. 아니면 섹션을 숨긴 채 두고 그 API도 부르지 않는다.
 for(const id of ['v-create','v-status','v-box'])el(id).hidden=!d.view_check;
 if(d.view_check)await loadView();
}
async function save(){return api('/profile',{method:'PUT',body:JSON.stringify({topic:el('r-topic').value})});}
el('r-agree').addEventListener('change',()=>{el('r-consent-btn').disabled=!el('r-agree').checked;});
el('r-consent-btn').addEventListener('click',async()=>{try{await api('/consent',{method:'PUT',body:JSON.stringify({agree:true})});await load();}catch(e){el('r-message').textContent=e.message;}});
el('r-withdraw').addEventListener('click',async()=>{try{await api('/consent',{method:'PUT',body:JSON.stringify({agree:false})});el('r-agree').checked=false;el('r-consent-btn').disabled=true;await load();el('r-message').textContent='동의를 철회했습니다. 다시 동의하기 전에는 리서치를 실행하지 않습니다.';}catch(e){el('r-message').textContent=e.message;}});
el('r-form').addEventListener('submit',async e=>{e.preventDefault();try{await save();el('r-message').textContent='주제를 저장했습니다.';}catch(e){el('r-message').textContent=e.message;}});
el('v-create').addEventListener('click',async()=>{
 el('v-create').disabled=true;
 try{await save();await api('/view',{method:'POST'});el('r-message').textContent='뷰 검증을 시작했습니다.';await loadView();}
 catch(e){el('r-message').textContent=e.message;await loadView().catch(()=>{});}
});
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
