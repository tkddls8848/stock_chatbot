"""개인 화면(`/portfolio`): 전체 자산 어드바이저.

화면은 정적 껍데기다. 비밀번호로 잠금을 연 뒤 브라우저가 `/api/portfolio/*`에서
자산·관심종목·조언을 채운다. 값은 전부 `esc()`를 거쳐 넣는다 — 조언 본문은 모델이
쓴 문자열이다. 금액 입력은 만원 단위로 받아 원으로 바꿔 보낸다.
"""

from services.web.pages.shell import I_LAYERS, I_SCALE, I_SHIELD, I_SPEC, JS_UTIL, h1, icon, page

_MAIN = (
    """<style>
.pf-hide{display:none!important}
.pf-card{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r2);padding:16px;margin:12px 0}
.pf-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pf-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.pf-card input,.pf-card select,.pf-btn{min-height:var(--ctl);border:1px solid var(--line);border-radius:var(--r1);
 background:var(--surface-1);color:var(--ink);padding:8px 10px;font:inherit;font-size:var(--fs-sm)}
.pf-btn{cursor:pointer;font-weight:700}.pf-btn.pri{background:var(--acc);color:#fff;border-color:var(--acc)}
.pf-btn:disabled{opacity:.5;cursor:wait}
.pf-field{display:flex;flex-direction:column;gap:4px;font-size:var(--fs-xs);color:var(--mut)}
.pf-table{width:100%;border-collapse:collapse;font-size:var(--fs-sm)}
.pf-table th,.pf-table td{border-top:1px solid var(--line2);padding:8px 6px;text-align:left;vertical-align:top}
.pf-table td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pf-bar{height:10px;background:var(--fill-2,#e8e3d6);border-radius:99px;overflow:hidden}.pf-bar i{display:block;height:100%;background:var(--acc)}
.pf-cls{display:grid;grid-template-columns:90px 1fr 110px;gap:10px;align-items:center;margin:6px 0;font-size:var(--fs-sm)}
.pf-msg{font-size:var(--fs-sm);color:var(--mut);margin:8px 0}.pf-msg.err{color:var(--warnc)}
.pf-advice p{line-height:1.8;margin:0 0 12px}
.pf-find{margin:6px 0;padding-left:10px;border-left:3px solid var(--line);font-size:var(--fs-sm)}
.pf-find.warn{border-color:var(--warnc)}
.pf-hist button{background:none;border:0;color:var(--acc);cursor:pointer;padding:2px 0;font:inherit;font-size:var(--fs-sm)}
@media(max-width:620px){.pf-cls{grid-template-columns:70px 1fr 90px}.pf-table .opt{display:none}}
</style>"""
    + h1(I_SHIELD, "내 자산")
    + "<div class='disc'>주식·채권·예적금·부동산을 한곳에서 보고, 요청할 때 조언을 받습니다. "
    "이 화면은 비밀번호로 잠겨 있고 입력한 자산은 공개 화면에 나오지 않습니다. "
    "조언은 <b>참고 정보이며 투자 권유가 아닙니다.</b></div>"
    + """
<div id='pf-lock' class='pf-card'>
 <form id='pf-login' class='pf-row' autocomplete='off'>
  <input id='pf-password' type='password' placeholder='비밀번호' aria-label='비밀번호' required>
  <button class='pf-btn pri' type='submit'>열기</button>
 </form>
 <p id='pf-lock-msg' class='pf-msg'></p>
</div>
<div id='pf-app' class='pf-hide'>
 <div class='pf-row' style='justify-content:flex-end'><button id='pf-logout' class='pf-btn' type='button'>잠그기</button></div>
 <section class='histbox'><div class='histh'><span class='phico'>"""
    + icon(I_LAYERS)
    + """</span>자산 구성</div><div id='pf-summary' class='pf-card'>불러오는 중…</div></section>
 <section class='histbox'><div class='histh'><span class='phico'>"""
    + icon(I_SPEC)
    + """</span>자산 목록</div>
  <div class='pf-card'><table class='pf-table'><thead><tr><th>구분</th><th>이름</th><th class='n'>평가액</th><th class='opt'>세부</th><th></th></tr></thead><tbody id='pf-assets'></tbody></table></div>
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
   <div class='pf-row' style='margin-top:10px'><button class='pf-btn pri' type='submit' id='pf-save'>추가</button><button class='pf-btn' type='button' id='pf-cancel'>새로 입력</button></div>
   <p id='pf-form-msg' class='pf-msg'></p>
  </form>
 </section>
 <section class='histbox'><div class='histh'><span class='phico'>"""
    + icon(I_SPEC)
    + """</span>관심종목</div>
  <div class='pf-card'>
   <p class='pf-msg'>텔레그램 봇의 뉴스 수집·리서치·브리핑이 이 목록을 읽습니다. 리서치가 자동으로 넣고 빼기도 합니다.</p>
   <table class='pf-table'><tbody id='pf-watch'></tbody></table>
   <form id='pf-watch-form' class='pf-row' style='margin-top:10px'>
    <select name='ex' aria-label='거래소'><option value='KR:KOSPI'>코스피</option><option value='KR:KOSDAQ'>코스닥</option><option value='US:NASDAQ'>나스닥</option><option value='US:NYSE'>뉴욕</option><option value='CN:SH'>상하이</option><option value='CN:SZ'>선전</option><option value='HK:HKEX'>홍콩</option></select>
    <input name='code' placeholder='종목 코드' maxlength='20' required>
    <input name='name' placeholder='이름' maxlength='60' required>
    <button class='pf-btn' type='submit'>추가</button>
   </form>
   <p id='pf-watch-msg' class='pf-msg'></p>
  </div>
 </section>
 <section class='histbox'><div class='histh'><span class='phico'>"""
    + icon(I_SCALE)
    + """</span>조언</div>
  <div class='pf-card'>
   <div class='pf-row'><button id='pf-advise' class='pf-btn pri' type='button'>지금 조언 받기</button><span id='pf-usage' class='pf-msg'></span></div>
   <p class='pf-msg'>외부 금리·실거래가 자료를 읽고 규칙으로 진단한 뒤 AI가 풀어 씁니다. 수십 초 걸립니다. 숫자는 진단이 계산한 값만 씁니다.</p>
   <div id='pf-advice' class='pf-advice'></div>
   <div id='pf-history' class='pf-hist'></div>
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
async function api(path,opt={}){const r=await fetch('/api/portfolio/'+path,{credentials:'same-origin',headers:opt.body?{'Content-Type':'application/json'}:{},...opt});
 if(r.status===401){showLock('잠겨 있습니다.');throw new Error('locked')}
 if(!r.ok){let d='';try{d=(await r.json()).detail}catch(e){}throw new Error(typeof d==='string'&&d?d:('요청 실패 '+r.status))}
 return r.status===204?null:r.json()}
function showLock(msg){$('pf-lock').classList.remove('pf-hide');$('pf-app').classList.add('pf-hide');$('pf-lock-msg').textContent=msg||''}
function showApp(){$('pf-lock').classList.add('pf-hide');$('pf-app').classList.remove('pf-hide');loadAll()}
async function boot(){const s=await fetch('/api/portfolio/session',{credentials:'same-origin'}).then(r=>r.json());
 if(!s.configured){showLock('개인 화면이 아직 설정되지 않았습니다(서버의 PORTFOLIO_PASSWORD).');$('pf-login').classList.add('pf-hide');return}
 s.unlocked?showApp():showLock('')}
// 잠금 해제는 api()를 거치지 않는다 — 틀린 비밀번호의 401을 '잠겨 있음'으로 바꿔 보이면 안 된다.
$('pf-login').addEventListener('submit',async e=>{e.preventDefault();const r=await fetch('/api/portfolio/session',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:$('pf-password').value})});
 if(r.status===204){$('pf-password').value='';showApp();return}let d='';try{d=(await r.json()).detail}catch(x){}$('pf-lock-msg').textContent=typeof d==='string'&&d?d:'열지 못했습니다('+r.status+').'});
$('pf-logout').addEventListener('click',async()=>{await fetch('/api/portfolio/session',{method:'DELETE',credentials:'same-origin'});showLock('잠갔습니다.')});
function loadAll(){loadAssets();loadWatch();loadAdviceList()}
async function loadAssets(){const d=await api('assets');assets=d.assets||[];renderAssets();renderSummary()}
function detail(a){const p=[];if(a.market)p.push(a.market+(a.code?' '+a.code:''));if(a.rate_pct!=null)p.push(a.rate_pct+'%');if(a.maturity)p.push('만기 '+a.maturity);
 if(a.region_code)p.push('지역 '+a.region_code+(a.complex?' '+a.complex:''));if(a.area_m2)p.push(a.area_m2+'㎡');if(a.loan_krw)p.push('대출 '+won(a.loan_krw));return p.join(' · ')}
function renderAssets(){$('pf-assets').innerHTML=assets.map(a=>"<tr><td>"+esc(KIND[a.kind]||a.kind)+"</td><td>"+esc(a.name)+"</td><td class='n'>"+esc(won(a.value_krw))+"</td><td class='opt'>"+esc(detail(a))+"</td><td class='n'><button class='pf-btn' data-edit='"+esc(a.id)+"'>수정</button> <button class='pf-btn' data-del='"+esc(a.id)+"'>삭제</button></td></tr>").join('')||"<tr><td colspan='5'>아직 입력한 자산이 없습니다.</td></tr>";
 $('pf-assets').querySelectorAll('[data-edit]').forEach(b=>b.addEventListener('click',()=>startEdit(b.dataset.edit)));
 $('pf-assets').querySelectorAll('[data-del]').forEach(b=>b.addEventListener('click',async()=>{if(!confirm('삭제할까요?'))return;await api('assets/'+encodeURIComponent(b.dataset.del),{method:'DELETE'});loadAssets()}))}
function renderSummary(){const total=assets.reduce((s,a)=>s+(Number(a.value_krw)||0),0),loans=assets.reduce((s,a)=>s+(a.kind==='real_estate'?Number(a.loan_krw)||0:0),0);
 if(!total){$('pf-summary').textContent='자산을 입력하면 구성이 여기에 나옵니다.';return}
 $('pf-summary').innerHTML="<p><b>총자산 "+esc(won(total))+"</b> · 순자산 "+esc(won(total-loans))+"</p>"+Object.keys(KIND).map(k=>{const v=assets.filter(a=>a.kind===k).reduce((s,a)=>s+(Number(a.value_krw)||0),0),p=v/total*100;return "<div class='pf-cls'><span>"+KIND[k]+"</span><div class='pf-bar'><i style='width:"+p.toFixed(1)+"%'></i></div><span class='n'>"+p.toFixed(1)+"% · "+esc(won(v))+"</span></div>"}).join('')}
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
function renderWatch(){$('pf-watch').innerHTML=Object.entries(watch).map(([c,n])=>"<tr><td>"+esc(n)+"</td><td>"+esc(c)+"</td><td class='n'><button class='pf-btn' data-wdel='"+esc(c)+"'>빼기</button></td></tr>").join('')||"<tr><td>관심종목이 없습니다.</td></tr>";
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
 if(a.text)parts.push(a.text.split('\\n\\n').map(p=>'<p>'+esc(p)+'</p>').join(''));else parts.push("<p class='pf-msg err'>조언 본문을 만들지 못해 진단만 보여 줍니다. ("+esc(a.llm_status)+")</p>");
 if((g.findings||[]).length)parts.push('<p class="pf-msg">규칙 진단</p>'+g.findings.map(f=>"<div class='pf-find "+esc(f.level)+"'>"+esc(f.message)+"</div>").join(''));
 parts.push("<p class='pf-msg'>외부 자료: "+Object.entries(a.sources||{}).map(([k,v])=>esc(SRC[k]||k)+' '+esc(STATE[v]||v)).join(' · ')+"</p>");
 parts.push("<p class='pf-msg'>"+esc(a.disclaimer||'')+"</p>");$('pf-advice').innerHTML=parts.join('')}
$('pf-advise').addEventListener('click',async e=>{const b=e.currentTarget;b.disabled=true;b.textContent='조언을 만드는 중…';
 try{renderAdvice(await api('advice',{method:'POST'}));loadAdviceList()}catch(err){if(err.message!=='locked')$('pf-advice').innerHTML="<p class='pf-msg err'>"+esc(err.message)+"</p>"}
 finally{b.disabled=false;b.textContent='지금 조언 받기'}});
syncKind();boot();
</script>"""
)

PORTFOLIO_HTML = page("내 자산", "/portfolio", _MAIN, _SCRIPT)
