"""개인 화면(`/portfolio`): 전체 자산 어드바이저.

화면은 정적 껍데기다. Google 로그인 뒤 브라우저가 `/api/portfolio/*`·`/api/account/newsletter`에서
자산·관심종목·조언·뉴스레터 구독을 채운다. 값은 전부 `esc()`를 거쳐 넣는다 — 조언 본문은 모델이
쓴 문자열이다. 금액 입력은 만원 단위로 받아 원으로 바꿔 보낸다.
"""

from services.web.pages.shell import I_DOC, I_LAYERS, I_SCALE, I_SHIELD, I_SPEC, JS_UTIL, h1, icon, page

_MAIN = (
    """<style>
.pf-hide{display:none!important}
.pf-card{background:var(--surface-2);border:0;border-top:1px solid var(--rule);border-radius:0;padding:16px 0;margin:16px 0}
.pf-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pf-row-end{justify-content:flex-end}.pf-row-spaced{margin-top:10px}
.pf-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.pf-card input,.pf-card select,.pf-btn{min-height:var(--ctl);border:1px solid var(--line);border-radius:var(--r1);
 background:var(--surface-1);color:var(--ink);padding:8px 10px;font:inherit;font-size:var(--fs-sm)}
.pf-btn{cursor:pointer;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.pf-btn.pri{background:var(--gold);color:#fff;border-color:var(--gold)}
.pf-btn:disabled{opacity:.5;cursor:wait}
.pf-field{display:flex;flex-direction:column;gap:4px;font-size:var(--fs-xs);color:var(--mut)}
.pf-scroll{overflow-x:auto}
.pf-table{width:100%;border-collapse:collapse;font-size:var(--fs-sm)}
.pf-table th,.pf-table td{border-top:1px solid var(--line2);padding:8px 6px;text-align:left;vertical-align:top}
.pf-table td{overflow-wrap:anywhere}
.pf-table td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
/* 동작 칸은 금액과 달리 줄을 바꿔도 읽는 데 지장이 없다. 좁은 화면에서
   버튼 두 개가 아래로 쌓이면 칸 폭이 버튼 하나 너비로 줄어든다. */
.pf-table td.act{white-space:normal}
.pf-table td.act .pf-btn{min-height:32px;padding:5px 8px}
.pf-bar{height:10px;background:var(--fill-2);border-radius:0;overflow:hidden}.pf-bar i{display:block;height:100%;background:var(--acc)}
.pf-cls{display:grid;grid-template-columns:90px 1fr 110px;gap:10px;align-items:center;margin:6px 0;font-size:var(--fs-sm)}
.pf-msg{font-size:var(--fs-sm);color:var(--mut);margin:8px 0}.pf-msg.err{color:var(--warnc)}
.pf-advice p{line-height:1.8;margin:0 0 12px}
.pf-find{margin:6px 0;padding-left:10px;border-left:3px solid var(--line);font-size:var(--fs-sm)}
.pf-find.warn{border-color:var(--warnc)}
.pf-hist button{background:none;border:0;color:var(--acc);cursor:pointer;padding:2px 0;font:inherit;font-size:var(--fs-sm)}
@media(max-width:620px){.pf-cls{grid-template-columns:70px 1fr 90px}.pf-table .opt{display:none}
.pf-table th,.pf-table td{padding:8px 4px}}
</style>"""
    + h1(I_SHIELD, "내 자산")
    + "<div class='disc'>주식·채권·예적금·부동산을 한곳에서 보고, 요청할 때 조언을 받습니다. "
    "Google 계정별로 저장되며 입력한 자산은 본인에게만 보입니다. 계좌번호·실명·상세 주소는 입력하지 마세요. "
    "조언은 <b>참고 정보이며 투자 권유가 아닙니다.</b></div>"
    + """
<div id='pf-lock' class='login-panel'><div class='eyebrow'>개인 포트폴리오</div><h2>내 자산을 한눈에</h2><p>자산 구성부터 만기와 편중까지, 나만의 포트폴리오를 관리하세요.</p>
 <a id='pf-login' class='pf-btn pri' href='/auth/google?next=/portfolio'>Google 계정으로 로그인</a>
 <p class='pf-msg'>Google 계정의 이름·이메일·프로필 사진은 받아 오지 않습니다. <a href='/privacy'>개인정보 처리방침</a></p>
 <p id='pf-lock-msg' class='pf-msg'></p>
</div>
<div id='pf-app' class='pf-hide'>
 <div class='pf-row pf-row-end'><button id='pf-logout' class='pf-btn' type='button'>로그아웃</button> <a class='pf-btn' href='/api/account/export'>내 데이터 내려받기</a> <button id='pf-delete-account' class='pf-btn' type='button'>내 데이터 전체 삭제·탈퇴</button></div>
 <section class='histbox'><div class='histh'><span class='phico'>"""
    + icon(I_LAYERS)
    + """</span>자산 구성</div><div id='pf-summary' class='pf-card'>불러오는 중…</div></section>
 <section class='histbox'><div class='histh'><span class='phico'>"""
    + icon(I_SPEC)
    + """</span>자산 목록</div>
  <div class='pf-card'><div class='pf-scroll' tabindex='0' role='region' aria-label='자산 목록 표'><table class='pf-table'><thead><tr><th>구분</th><th>이름</th><th class='n'>평가액</th><th class='opt'>세부</th><th><span class='sr'>동작</span></th></tr></thead><tbody id='pf-assets'></tbody></table></div></div>
  <form id='pf-form' class='pf-card'>
   <div class='pf-grid'>
    <label class='pf-field'>구분<select name='kind' id='pf-kind'><option value='stock'>주식</option><option value='bond'>채권</option><option value='deposit'>예적금</option><option value='real_estate'>부동산</option></select></label>
    <label class='pf-field'>이름<input name='name' maxlength='60' required></label>
    <label class='pf-field'>평가액(만원)<input name='value_man' type='number' min='0' step='1' required></label>
    <label class='pf-field'>매입가·원금(만원)<input name='cost_man' type='number' min='0' step='1'></label>
    <label class='pf-field k-stock'>시장<select name='market'><option value='KR'>한국</option><option value='US'>미국</option><option value='CN'>중국</option><option value='HK'>홍콩</option><option value='JP'>일본</option></select></label>
    <label class='pf-field k-stock'>종목 코드<input name='code' maxlength='20'></label>
    <label class='pf-field k-deposit'>상품<select name='product'><option value='deposit'>예금</option><option value='saving'>적금</option></select></label>
    <label class='pf-field k-bond'>채권 종류<select name='bond_type'><option value='government'>국채</option><option value='corporate'>회사채</option><option value='other'>기타</option></select></label>
    <label class='pf-field k-bond k-deposit'>금리·쿠폰(%)<input name='rate_pct' type='number' min='0' max='100' step='0.01'></label>
    <label class='pf-field k-bond k-deposit'>만기일<input name='maturity' type='date'></label>
    <label class='pf-field k-real_estate'>시군구 코드(5자리)<input name='region_code' pattern='\\d{5}' placeholder='예: 11680'></label>
    <label class='pf-field k-real_estate'>단지명(선택)<input name='complex' maxlength='40'></label>
    <label class='pf-field k-real_estate'>전용면적(㎡)<input name='area_m2' type='number' min='0' step='0.01'></label>
    <label class='pf-field k-real_estate'>대출 잔액(만원)<input name='loan_man' type='number' min='0' step='1'></label>
    <label class='pf-field'>메모<input name='note' maxlength='200'></label>
   </div>
   <div class='pf-row pf-row-spaced'><button class='pf-btn pri' type='submit' id='pf-save'>추가</button><button class='pf-btn' type='button' id='pf-cancel'>새로 입력</button></div>
   <p id='pf-form-msg' class='pf-msg'></p>
  </form>
 </section>
 <section class='histbox'><div class='histh'><span class='phico'>"""
    + icon(I_SPEC)
    + """</span>관심종목</div>
  <div class='pf-card'>
   <p class='pf-msg'>내 리서치가 이 목록을 참고합니다. 다른 계정이나 운영자 봇과 공유되지 않습니다.</p>
   <div class='pf-scroll' tabindex='0' role='region' aria-label='관심종목 표'><table class='pf-table' aria-label='관심종목 목록'><tbody id='pf-watch'></tbody></table></div>
   <form id='pf-watch-form' class='pf-row pf-row-spaced'>
    <select name='ex' aria-label='거래소'><option value='KR:KOSPI'>코스피</option><option value='KR:KOSDAQ'>코스닥</option><option value='US:NASDAQ'>나스닥</option><option value='US:NYSE'>뉴욕</option><option value='CN:SH'>상하이</option><option value='CN:SZ'>선전</option><option value='HK:HKEX'>홍콩</option></select>
    <input name='code' placeholder='종목 코드' maxlength='20' aria-label='관심종목 코드' required>
    <input name='name' placeholder='이름' maxlength='60' aria-label='관심종목 이름' required>
    <button class='pf-btn' type='submit'>추가</button>
   </form>
   <p id='pf-watch-msg' class='pf-msg'></p>
  </div>
 </section>
 <section class='histbox'><div class='histh'><span class='phico'>"""
    + icon(I_SCALE)
    + """</span>조언</div>
  <div class='pf-card'>
   <div class='pf-row'><button id='pf-advise' class='pf-btn pri' type='button'>자산 진단하기</button><span id='pf-usage' class='pf-msg'></span></div>
   <p class='pf-msg'>공개 금리·실거래가 자료와 내 자산을 비교해 편중·만기·대출 부담을 계산합니다. 개인 자산은 외부 AI에 보내지 않습니다. 수십 초 걸릴 수 있습니다.</p>
   <div id='pf-advice' class='pf-advice'></div>
   <div id='pf-history' class='pf-hist'></div>
  </div>
 </section>
 <section class='histbox' id='newsletter'><div class='histh'><span class='phico'>"""
    + icon(I_DOC)
    + """</span>뉴스레터</div>
  <div class='pf-card'>
   <p class='pf-msg'>매일 아침 8시 30분, 여섯 시장의 논조와 시장상황 보고서, 주요 기사를 이메일로 받습니다. 공개 화면과 같은 자료이며 내 자산·관심종목은 담기지 않습니다. 확인 코드를 입력한 주소로만 보냅니다.</p>
   <p id='nl-state' class='pf-msg'>불러오는 중…</p>
   <form id='nl-email-form' class='pf-row pf-hide'>
    <input name='email' type='email' maxlength='254' placeholder='받을 이메일 주소' aria-label='받을 이메일 주소' autocomplete='email' required>
    <button class='pf-btn pri' type='submit'>확인 코드 받기</button>
   </form>
   <form id='nl-code-form' class='pf-row pf-row-spaced pf-hide'>
    <input name='code' inputmode='numeric' pattern='\\d{6}' maxlength='6' placeholder='6자리 코드' aria-label='확인 코드' autocomplete='one-time-code' required>
    <button class='pf-btn pri' type='submit'>구독 확인</button>
   </form>
   <div class='pf-row pf-row-spaced'><button id='nl-off' class='pf-btn pf-hide' type='button'>뉴스레터 끄기</button></div>
   <p id='nl-msg' class='pf-msg'></p>
  </div>
 </section>
</div>
"""
)

_SCRIPT = (
    "<script>" + JS_UTIL + """
const KIND={stock:'주식',bond:'채권',deposit:'예적금',real_estate:'부동산'};
const SRC={deposit_rates:'예적금 금리(금감원)',market_rates:'시장 금리(한국은행)',real_estate:'실거래가(국토부)'};
const STATE={ok:'정상',missing_key:'키 없음',error:'실패'};
const won=v=>{v=Math.round(Number(v)||0);const e=Math.floor(Math.abs(v)/1e8),m=Math.floor(Math.abs(v)%1e8/1e4),p=[];if(e)p.push(e.toLocaleString()+'억');if(m||!e)p.push(m.toLocaleString()+'만');return (v<0?'-':'')+p.join(' ')+'원'};
const $=id=>document.getElementById(id);let assets=[],watch={},editing=null;
async function call(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:opt.body?{'Content-Type':'application/json'}:{},...opt});
 if(r.status===401){location.replace('/portfolio');throw new Error('로그인이 만료되었습니다.')}
 if(!r.ok){let d='';try{d=(await r.json()).detail}catch(e){}throw new Error(typeof d==='string'&&d?d:('요청 실패 '+r.status))}
 return r.status===204?null:r.json()}
const api=(path,opt)=>call('/api/portfolio/'+path,opt),nl=(path,opt)=>call('/api/account/newsletter'+path,opt);
function showLock(msg){$('pf-lock').classList.remove('pf-hide');$('pf-app').classList.add('pf-hide');$('pf-lock-msg').textContent=msg||''}
function showApp(){$('pf-lock').classList.add('pf-hide');$('pf-app').classList.remove('pf-hide');loadAll()}
async function boot(){const s=await fetch('/api/portfolio/session',{credentials:'same-origin'}).then(r=>r.json());
 if(!s.configured){showLock('Google 로그인을 준비 중입니다.');$('pf-login').classList.add('pf-hide');return}
 s.unlocked?showApp():showLock('')}
$('pf-logout').addEventListener('click',async()=>{const r=await fetch('/api/account/session',{method:'DELETE',credentials:'same-origin'});if(r.ok)location.replace('/portfolio')});
$('pf-delete-account').addEventListener('click',async()=>{
 if(!confirm('이 계정의 자산·관심종목·조언·리서치를 모두 삭제하고 탈퇴합니다. 되돌릴 수 없습니다. 계속할까요?'))return;
 const r=await fetch('/api/account',{method:'DELETE',credentials:'same-origin'});
 if(r.ok)location.replace('/portfolio');else alert('삭제하지 못했습니다. 진행 중인 작업이 끝난 뒤 다시 시도하세요.');
});
window.addEventListener('pageshow',e=>{if(e.persisted)location.reload()});
function loadAll(){loadAssets();loadWatch();loadAdviceList();loadNl()}
async function loadAssets(){const d=await api('assets');assets=d.assets||[];renderAssets();renderSummary()}
function detail(a){const p=[];if(a.market)p.push(a.market+(a.code?' '+a.code:''));if(a.rate_pct!=null)p.push(a.rate_pct+'%');if(a.maturity)p.push('만기 '+a.maturity);
 if(a.region_code)p.push('지역 '+a.region_code+(a.complex?' '+a.complex:''));if(a.area_m2)p.push(a.area_m2+'㎡');if(a.loan_krw)p.push('대출 '+won(a.loan_krw));return p.join(' · ')}
function renderAssets(){$('pf-assets').innerHTML=assets.map(a=>"<tr><td>"+esc(KIND[a.kind]||a.kind)+"</td><td>"+esc(a.name)+"</td><td class='n'>"+esc(won(a.value_krw))+"</td><td class='opt'>"+esc(detail(a))+"</td><td class='n act'><button class='pf-btn' data-edit='"+esc(a.id)+"'>수정</button> <button class='pf-btn' data-del='"+esc(a.id)+"'>삭제</button></td></tr>").join('')||"<tr><td colspan='5'>아직 입력한 자산이 없습니다.</td></tr>";
 $('pf-assets').querySelectorAll('[data-edit]').forEach(b=>b.addEventListener('click',()=>startEdit(b.dataset.edit)));
 $('pf-assets').querySelectorAll('[data-del]').forEach(b=>b.addEventListener('click',async()=>{if(!confirm('삭제할까요?'))return;await api('assets/'+encodeURIComponent(b.dataset.del),{method:'DELETE'});loadAssets()}))}
function renderSummary(){const total=assets.reduce((s,a)=>s+(Number(a.value_krw)||0),0),loans=assets.reduce((s,a)=>s+(a.kind==='real_estate'?Number(a.loan_krw)||0:0),0);
 if(!total){$('pf-summary').textContent='자산을 입력하면 구성이 여기에 나옵니다.';return}
 $('pf-summary').innerHTML="<p><b>총자산 "+esc(won(total))+"</b> · 순자산 "+esc(won(total-loans))+"</p>"+Object.keys(KIND).map(k=>{const v=assets.filter(a=>a.kind===k).reduce((s,a)=>s+(Number(a.value_krw)||0),0),p=v/total*100;return "<div class='pf-cls'><span>"+KIND[k]+"</span><div class='pf-bar'><i data-width='"+p.toFixed(1)+"%'></i></div><span class='n'>"+p.toFixed(1)+"% · "+esc(won(v))+"</span></div>"}).join('');$('pf-summary').querySelectorAll('[data-width]').forEach(el=>{el.style.width=el.dataset.width})}
function syncKind(){const k=$('pf-kind').value;document.querySelectorAll('#pf-form [class*="k-"]').forEach(el=>el.classList.toggle('pf-hide',!el.classList.contains('k-'+k)))}
$('pf-kind').addEventListener('change',syncKind);
function resetForm(){editing=null;$('pf-form').reset();$('pf-save').textContent='추가';$('pf-form-msg').textContent='';syncKind()}
$('pf-cancel').addEventListener('click',resetForm);
function startEdit(id){const a=assets.find(x=>x.id===id);if(!a)return;resetForm();editing=id;const f=$('pf-form').elements;
 f.kind.value=a.kind;f.name.value=a.name||'';f.value_man.value=Math.round((a.value_krw||0)/1e4);f.cost_man.value=a.cost_krw!=null?Math.round(a.cost_krw/1e4):'';f.note.value=a.note||'';
 ['market','code','product','bond_type','rate_pct','maturity','region_code','complex','area_m2'].forEach(n=>{if(a[n]!=null)f[n].value=a[n]});f.loan_man.value=a.loan_krw!=null?Math.round(a.loan_krw/1e4):'';
 $('pf-save').textContent='수정 저장';syncKind();$('pf-form').scrollIntoView({behavior:'smooth'})}
const FIELDS={stock:['market','code'],bond:['bond_type','rate_pct','maturity'],deposit:['product','rate_pct','maturity'],real_estate:['region_code','complex','area_m2']};
$('pf-form').addEventListener('submit',async e=>{e.preventDefault();const f=$('pf-form').elements,kind=f.kind.value,num=v=>v===''?null:Number(v);
 const body={kind,name:f.name.value.trim(),value_krw:Math.round(Number(f.value_man.value)*1e4),note:f.note.value.trim()};
 if(f.cost_man.value!=='')body.cost_krw=Math.round(Number(f.cost_man.value)*1e4);
 FIELDS[kind].forEach(n=>{const v=f[n].value;if(v==='')return;body[n]=['rate_pct','area_m2'].includes(n)?num(v):v});
 if(kind==='real_estate'&&f.loan_man.value!=='')body.loan_krw=Math.round(Number(f.loan_man.value)*1e4);
 try{await api(editing?'assets/'+encodeURIComponent(editing):'assets',{method:editing?'PUT':'POST',body:JSON.stringify(body)});resetForm();loadAssets()}
 catch(err){$('pf-form-msg').textContent=err.message;$('pf-form-msg').classList.add('err')}});
async function loadWatch(){const d=await api('watchlist');watch=d.items||{};renderWatch()}
function renderWatch(){$('pf-watch').innerHTML=Object.entries(watch).map(([c,n])=>"<tr><td>"+esc(n)+"</td><td>"+esc(c)+"</td><td class='n act'><button class='pf-btn' data-wdel='"+esc(c)+"'>빼기</button></td></tr>").join('')||"<tr><td>관심종목이 없습니다.</td></tr>";
 $('pf-watch').querySelectorAll('[data-wdel]').forEach(b=>b.addEventListener('click',()=>{const next={...watch};delete next[b.dataset.wdel];saveWatch(Object.entries(next).map(([code,name])=>({code,name})))}))}
async function saveWatch(items){try{const d=await api('watchlist',{method:'PUT',body:JSON.stringify({items})});watch=d.items||{};renderWatch();$('pf-watch-msg').textContent=''}catch(err){$('pf-watch-msg').textContent=err.message;$('pf-watch-msg').classList.add('err')}}
$('pf-watch-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.target.elements,[market,exchange]=f.ex.value.split(':');
 // 저장 직전에 다시 읽는다 — 그사이 봇의 리서치가 목록을 바꿨을 수 있다.
 await loadWatch();const items=Object.entries(watch).map(([code,name])=>({code,name}));items.push({code:f.code.value.trim(),name:f.name.value.trim(),market,exchange});await saveWatch(items);e.target.reset()});
async function loadAdviceList(){const d=await api('advice');$('pf-usage').textContent='오늘 '+d.usage.today+' / '+d.usage.max_daily+'회';
 $('pf-history').innerHTML=(d.items||[]).length?'<p class="pf-msg">지난 조언</p>'+d.items.map(i=>"<button data-adv='"+esc(i.id)+"'>"+esc(stamp(i.created_at))+(i.llm_status==='ok'?'':' (진단만)')+"</button>").join('<br>'):'';
 $('pf-history').querySelectorAll('[data-adv]').forEach(b=>b.addEventListener('click',async()=>renderAdvice(await api('advice/'+encodeURIComponent(b.dataset.adv)))));
 if(d.items&&d.items.length&&!$('pf-advice').innerHTML)renderAdvice(await api('advice/latest'))}
function renderAdvice(a){const g=a.diagnosis||{},parts=[];parts.push("<p class='pf-msg'>"+esc(stamp(a.created_at))+" 기준</p>");
 if(a.text)parts.push(a.text.split('\\n\\n').map(p=>'<p>'+esc(p)+'</p>').join(''));else parts.push("<p class='pf-msg'>개인 데이터를 외부 AI에 보내지 않고 계산한 자산 진단입니다.</p>");
 if((g.findings||[]).length)parts.push('<p class="pf-msg">규칙 진단</p>'+g.findings.map(f=>"<div class='pf-find "+esc(f.level)+"'>"+esc(f.message)+"</div>").join(''));
 parts.push("<p class='pf-msg'>외부 자료: "+Object.entries(a.sources||{}).map(([k,v])=>esc(SRC[k]||k)+' '+esc(STATE[v]||v)).join(' · ')+"</p>");
 parts.push("<p class='pf-msg'>"+esc(a.disclaimer||'')+"</p>");$('pf-advice').innerHTML=parts.join('')}
$('pf-advise').addEventListener('click',async e=>{const b=e.currentTarget;b.disabled=true;b.textContent='진단하는 중…';
 try{renderAdvice(await api('advice',{method:'POST'}));loadAdviceList()}catch(err){if(err.message!=='locked')$('pf-advice').innerHTML="<p class='pf-msg err'>"+esc(err.message)+"</p>"}
 finally{b.disabled=false;b.textContent='자산 진단하기'}});
function nlMsg(text,err){$('nl-msg').textContent=text||'';$('nl-msg').classList.toggle('err',!!err)}
function renderNl(d){const on=d.status==='active',wait=d.status==='pending';
 $('nl-state').textContent=!d.configured?'메일 발송을 준비 중입니다.':on?d.email+' 주소로 받고 있습니다.'+(d.last_sent_on?' 마지막 발송 '+d.last_sent_on+'.':'')
  :wait?d.email+' 주소로 보낸 6자리 코드를 입력하세요. 코드는 10분 동안 유효합니다(오늘 '+d.codes_today+' / '+d.codes_per_day+'회).':'구독하지 않았습니다.';
 $('nl-email-form').classList.toggle('pf-hide',on||!d.configured);$('nl-code-form').classList.toggle('pf-hide',!wait);
 $('nl-off').classList.toggle('pf-hide',d.status==='none');$('nl-off').textContent=on?'뉴스레터 끄기':'신청 취소'}
async function loadNl(){try{renderNl(await nl(''))}catch(err){nlMsg(err.message,true)}}
$('nl-email-form').addEventListener('submit',async e=>{e.preventDefault();const b=e.submitter;if(b)b.disabled=true;
 try{renderNl(await nl('',{method:'PUT',body:JSON.stringify({email:e.target.elements.email.value.trim()})}));nlMsg('확인 코드를 보냈습니다. 메일함(스팸함 포함)을 확인하세요.')}
 catch(err){nlMsg(err.message,true);loadNl()}finally{if(b)b.disabled=false}});
$('nl-code-form').addEventListener('submit',async e=>{e.preventDefault();
 try{renderNl(await nl('/confirm',{method:'POST',body:JSON.stringify({code:e.target.elements.code.value.trim()})}));e.target.reset();nlMsg('구독했습니다. 다음 날 아침부터 받습니다.')}
 catch(err){nlMsg(err.message,true);loadNl()}});
$('nl-off').addEventListener('click',async()=>{if(!confirm('뉴스레터를 끄고 저장한 주소를 지웁니다. 계속할까요?'))return;
 try{await nl('',{method:'DELETE'});nlMsg('뉴스레터를 껐습니다.');loadNl()}catch(err){nlMsg(err.message,true)}});
syncKind();boot();
</script>"""
)

PORTFOLIO_HTML = page(
    "내 자산",
    "/portfolio",
    _MAIN,
    _SCRIPT,
    description="주식·채권·예적금·부동산을 한곳에서 보고 조언을 받는 개인 화면입니다. "
    "Google 계정별로 저장하며 입력한 자산은 다른 이용자에게 공개되지 않습니다.",
)
