"""현재 집단 예측 컨센서스 화면(`/forecast`).

화면 문구에 출처 서비스 이름·베팅·거래 표현을 쓰지 않는다(`code_guide.md`)."""

from services.web.pages.shell import (
    I_BARS,
    I_CHART,
    I_LAYERS,
    I_SCALE,
    I_SPEC,
    JS_UTIL,
    h1,
    icon,
    page,
)

_POLYMARKET_MAIN = (
    """<style>
.pm-brief-g{border-top:1px solid var(--line2);padding:12px 0}.pm-brief-g:first-child{border-top:0}
.pm-brief-h{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline;margin-bottom:6px}
.pm-brief-h b{font-size:var(--fs-md)}.pm-brief-h span{font-size:var(--fs-xs);color:var(--mut)}
.pm-brief-g p{margin:0;line-height:1.75}.pm-brief-g p.empty{color:var(--mut)}
.pm-trend{display:grid;gap:8px}
.pm-trend-row{display:grid;grid-template-columns:1fr auto auto;gap:6px 14px;align-items:center;width:100%;border:1px solid var(--line);border-radius:var(--r2);background:var(--surface-2);padding:12px 14px;text-align:left;color:var(--ink);font:inherit;cursor:pointer}
.pm-trend-row:hover{border-color:var(--gold-a40)}
.pm-trend-row .t{font-weight:750}
.pm-trend-move{font-size:var(--fs-lg);font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
.pm-trend-now{font-size:var(--fs-sm);color:var(--mut);white-space:nowrap;font-variant-numeric:tabular-nums}
.pm-trend-side{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:12px}
.pm-trend-side h4{margin:0 0 8px;font-size:var(--fs-sm)}
@media(max-width:520px){.pm-trend-row{grid-template-columns:1fr auto}.pm-trend-now{grid-column:1/-1}}
.pm-alert{border:1px solid var(--gold-a25);background:var(--surface-1);border-radius:var(--r2);padding:10px 14px;margin:14px 0;color:var(--ink-soft);font-size:var(--fs-sm)}
.pm-alert.warn{border-color:rgba(143,69,17,.35);color:var(--warnc)}
.pm-bars,.pm-ranks,.pm-events{display:grid;gap:10px}.pm-bar{display:grid;grid-template-columns:minmax(110px,180px) 1fr minmax(78px,auto);gap:10px;align-items:center}
.pm-bar-track{height:10px;background:var(--fill-2);border-radius:99px;overflow:hidden}.pm-bar-fill{display:block;height:100%;background:linear-gradient(90deg,var(--gold),#c59a3c);border-radius:99px}
.pm-bar-label{font-weight:700}.pm-bar-value{text-align:right;font-variant-numeric:tabular-nums;font-size:var(--fs-xs);color:var(--mut)}
.pm-catgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}.pm-cat{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r2);padding:14px;text-align:left;color:var(--ink);cursor:pointer}
.pm-cat b{display:block}.pm-cat span{font-size:var(--fs-xs);color:var(--mut)}.pm-rankgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.pm-panel{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r3);padding:16px}.pm-panel h3{font-size:var(--fs-sm);margin:0 0 10px}
.pm-rank{display:block;width:100%;border:0;border-top:1px solid var(--line2);background:transparent;text-align:left;padding:9px 0;color:var(--ink);cursor:pointer}.pm-rank:first-child{border-top:0}.pm-rank small{display:block;color:var(--mut)}
.pm-controls{display:grid;grid-template-columns:2fr repeat(5,minmax(125px,1fr));gap:8px;margin:12px 0}.pm-controls input,.pm-controls select,.pm-btn{min-height:var(--ctl);border:1px solid var(--line);border-radius:var(--r1);background:var(--surface-1);color:var(--ink);padding:8px 10px;font:inherit;font-size:var(--fs-sm)}
.pm-btn{cursor:pointer;font-weight:700}.pm-event{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r2);padding:14px;display:grid;grid-template-columns:1fr auto;gap:8px;cursor:pointer;text-align:left;color:var(--ink);font:inherit}.pm-event:hover{border-color:var(--gold-a40)}
.pm-event-title{font-weight:750}.pm-sum{display:block;margin:2px 0 4px;color:var(--ink-soft);font-size:var(--fs-sm)}.pm-meta{display:flex;flex-wrap:wrap;gap:6px 12px;color:var(--mut);font-size:var(--fs-xs)}.pm-prob{font-size:var(--fs-lg);font-weight:800;white-space:nowrap;font-variant-numeric:tabular-nums}
.pm-badge{display:inline-flex;padding:1px 7px;border-radius:99px;border:1px solid var(--line);font-size:var(--fs-2xs);font-weight:700}.pm-badge.ok{color:var(--ok)}.pm-badge.bad{color:var(--warnc)}
.pm-pages{display:flex;justify-content:space-between;align-items:center;margin-top:12px;color:var(--mut);font-size:var(--fs-sm)}.pm-detail{width:min(760px,calc(100% - 24px));max-height:85vh;border:1px solid var(--gold-a25);border-radius:var(--r3);background:var(--surface-3);color:var(--ink);padding:0;box-shadow:var(--elev-3)}
.pm-detail::backdrop{background:rgba(35,32,24,.42)}.pm-detail-in{padding:22px}.pm-detail-head{display:flex;justify-content:space-between;gap:12px;align-items:start}.pm-detail h2{font-size:var(--fs-lg);margin:0}
.pm-outcome{margin-top:10px}.pm-outcome-head{display:flex;justify-content:space-between;gap:10px;font-size:var(--fs-sm)}.pm-prog{height:9px;border-radius:99px;background:var(--fill-2);overflow:hidden}.pm-prog i{display:block;height:100%;background:var(--acc);border-radius:99px}.pm-source{font-size:var(--fs-xs);color:var(--mut);margin-top:14px}.pm-source a{color:var(--acc)}
@media(max-width:900px){.pm-rankgrid{grid-template-columns:1fr}.pm-controls{grid-template-columns:repeat(2,1fr)}}
@media(max-width:520px){.pm-controls{grid-template-columns:1fr}.pm-event{grid-template-columns:1fr}.pm-prob{text-align:left}.pm-bar{grid-template-columns:100px 1fr}.pm-bar-value{grid-column:2;text-align:left}.links{overflow-x:auto}.navin{padding-right:8px}.brand{display:none}}
</style>"""
    + h1(I_BARS, "집단 예측 컨센서스")
    + "<div class='disc'>지금 열려 있는 해외 집단 예측 질문 전체를 분야별로 정리한 현재 시점 기록입니다. "
    "확률은 참여자들의 집단 예측을 모은 값이며 <b>사실 보증이나 매매 신호가 아닙니다.</b> "
    "과거 기록이나 기간별 비교는 보여 주지 않습니다.</div>"
    + "<div id='pm-alert' class='pm-alert'>현재 자료를 불러오는 중…</div>"
    + """
<div class='statstrip' aria-label='현재 범위 지표'>
 <div class='st'><div class='l'>열린 예측 질문</div><div class='v' id='pm-events'>–</div></div>
 <div class='st'><div class='l'>세부 선택지</div><div class='v' id='pm-markets'>–</div></div>
 <div class='st'><div class='l'>24시간 참여 규모</div><div class='v text' id='pm-volume'>–</div></div>
 <div class='st'><div class='l'>현재 참여 잔액</div><div class='v text' id='pm-liquidity'>–</div></div>
 <div class='st'><div class='l'>분야가 정해진 비율</div><div class='v' id='pm-category-cover'>–</div></div>
 <div class='st'><div class='l'>확률을 읽은 비율</div><div class='v' id='pm-price-cover'>–</div></div>
</div>
<section class='histbox' aria-labelledby='pm-trend-title'><div class='histh' id='pm-trend-title'><span class='phico'>"""
    + icon(I_CHART)
    + """</span>오늘의 트렌드 이슈</div>
 <p class='sub2' id='pm-trend-meta'>불러오는 중…</p><div class='pm-trend' id='pm-trend'></div>
 <div class='pm-trend-side'><div class='pm-panel'><h4>오늘 새로 들어온 예측</h4><div id='pm-trend-new' class='pm-ranks'></div></div>
 <div class='pm-panel'><h4>참여가 몰리는 중</h4><div id='pm-trend-volume' class='pm-ranks'></div></div></div></section>
<section class='histbox' aria-labelledby='pm-brief-title'><div class='histh' id='pm-brief-title'><span class='phico'>"""
    + icon(I_SCALE)
    + """</span>경제·금융·지정학 전망 정리</div>
 <p class='sub2' id='pm-brief-meta'>불러오는 중…</p><div id='pm-brief'></div></section>
<section class='histbox' aria-labelledby='pm-activity-title'><div class='histh' id='pm-activity-title'><span class='phico'>"""
    + icon(I_BARS)
    + """</span>분야별 24시간 활동</div><div class='pm-bars' id='pm-bars'></div></section>
<section class='histbox' aria-labelledby='pm-categories-title'><div class='histh' id='pm-categories-title'><span class='phico'>"""
    + icon(I_LAYERS)
    + """</span>분야별 범위</div><div class='pm-catgrid' id='pm-categories'></div></section>
<section class='histbox' aria-labelledby='pm-ranks-title'><div class='histh' id='pm-ranks-title'><span class='phico'>"""
    + icon(I_SCALE)
    + """</span>현재 진단 <button class='pm-btn' id='pm-flag-toggle' type='button'>주의 질문 포함</button></div>
 <div class='pm-rankgrid'><div class='pm-panel'><h3>이지선다 · 한쪽이 우세</h3><div id='pm-strong' class='pm-ranks'></div></div>
 <div class='pm-panel'><h3>이지선다 · 팽팽한 질문</h3><div id='pm-tight' class='pm-ranks'></div></div>
 <div class='pm-panel'><h3>24시간 참여 활발</h3><div id='pm-active' class='pm-ranks'></div></div></div></section>
<section class='histbox' aria-labelledby='pm-explorer-title'><div class='histh' id='pm-explorer-title'><span class='phico'>"""
    + icon(I_SPEC)
    + """</span>전체 예측 질문 찾기</div>
 <form class='pm-controls' id='pm-controls' role='search'>
  <input id='pm-q' name='q' type='search' maxlength='200' placeholder='자연어로 찾기 (예: 연준 금리 인하, 트럼프 관세)' aria-label='예측 질문 검색'>
  <select id='pm-category' name='category' aria-label='분야'><option value=''>모든 분야</option></select>
  <select id='pm-tag' name='tag' aria-label='태그'><option value=''>모든 태그(영문 원문)</option></select>
  <select id='pm-region' name='region' aria-label='지역'><option value=''>모든 지역</option></select>
  <select id='pm-type' name='event_type' aria-label='유형'><option value=''>모든 유형</option></select>
  <select id='pm-status' name='status' aria-label='데이터 상태'><option value=''>모든 상태</option></select>
  <select id='pm-sort' name='sort' aria-label='정렬'><option value=''>기본(검색하면 관련도순)</option><option value='relevance'>관련도</option><option value='volume24hr'>24시간 참여 규모</option><option value='liquidity'>참여 잔액</option><option value='leader_probability'>1위 확률</option><option value='end_date'>종료일</option><option value='title'>제목</option></select>
 </form><div id='pm-result-meta' class='sub2' aria-live='polite'></div><div class='pm-events' id='pm-event-list'></div>
 <div class='pm-pages'><button type='button' class='pm-btn' id='pm-prev'>이전</button><span id='pm-page'>–</span><button type='button' class='pm-btn' id='pm-next'>다음</button></div></section>
<dialog class='pm-detail' id='pm-detail'><div class='pm-detail-in'><div class='pm-detail-head'><h2 id='pm-detail-title'>Event 상세</h2><button type='button' class='pm-btn' id='pm-detail-close' aria-label='상세 닫기'>닫기</button></div><div id='pm-detail-body'></div></div></dialog>
"""
)

_POLYMARKET_SCRIPT = (
    "<script>" + JS_UTIL + """
const PM_STATUS={ok:'정상',low_liquidity:'참여 적음',no_liquidity:'참여 없음',liquidity_missing:'참여 정보 없음',unavailable:'확률 읽기 불가'};
const PM_TYPE={binary:'이지선다',exclusive_multi:'다지선다(하나만 정답)',independent_multi:'여러 질문 묶음',unknown_multi:'유형 미상'};
const PM_REGION={us:'미국',usa:'미국','united-states':'미국',brazil:'브라질',japan:'일본',korea:'한국',china:'중국',iran:'이란',russia:'러시아',rus:'러시아',ukraine:'우크라이나',europe:'유럽','middle-east':'중동',mex:'멕시코'};
const PM_FRESH={normal:'제때 갱신됨',warming_up:'갱신 주기 파악 중',delayed:'갱신 지연',stale:'오래된 자료',missing:'자료 없음'};
const side=v=>({yes:'예',no:'아니오'})[String(v||'').toLowerCase()]||v;
const money=v=>{if(v==null)return '정보 없음';const n=Number(v);if(n>=1e9)return '$'+(n/1e9).toFixed(1)+'B';if(n>=1e6)return '$'+(n/1e6).toFixed(1)+'M';if(n>=1e3)return '$'+(n/1e3).toFixed(1)+'K';return '$'+n.toFixed(0)};
const prob=v=>v==null?'–':(Number(v)*100).toFixed(1)+'%';let pmPage=1,pmPages=0,pmFlagged=false,pmGeneration=null;
function rankRows(items){if(!items||!items.length)return "<p class='empty'>해당 질문이 없습니다.</p>";return items.map(e=>"<button type='button' class='pm-rank' data-event='"+esc(e.id)+"'><b>"+esc(e.title)+"</b><small>"+esc(side(e.leader)||PM_STATUS[e.data_status]||'')+" · "+prob(e.leader_probability)+" · "+money(e.volume24hr)+"</small></button>").join('')}
function bindDetails(root=document){root.querySelectorAll('[data-event]').forEach(b=>b.addEventListener('click',()=>openDetail(b.dataset.event)))}
function renderSummary(d){pmGeneration=d.generation_id;const a=d.accounting||{},x=d.activity||{};document.getElementById('pm-events').textContent=(a.open_event_count||0).toLocaleString();document.getElementById('pm-markets').textContent=(x.market_count||0).toLocaleString();document.getElementById('pm-volume').textContent=money(x.volume24hr);document.getElementById('pm-liquidity').textContent=money(x.liquidity);document.getElementById('pm-category-cover').textContent=prob(d.named_category_ratio);document.getElementById('pm-price-cover').textContent=prob(a.open_event_count?(a.consensus_ready_event_count/a.open_event_count):0);
 const fresh=d.freshness||{},alert=document.getElementById('pm-alert');alert.className='pm-alert '+(['delayed','stale'].includes(fresh.state)?'warn':'');alert.textContent='기준 '+stamp(d.generated_at)+' (한국 시간) · '+(d.coverage_status==='complete'?'전체 수집 완료':'일부만 수집됨')+' · '+(PM_FRESH[fresh.state]||'갱신 상태 미상')+' · 순위는 데이터가 정상인 질문만 셉니다.';document.getElementById('asof-date').textContent='예측 컨센서스 '+stamp(d.generated_at)+' (한국 시간)';
 const cats=(d.category_activity||[]).slice().sort((a,b)=>b.volume24hr-a.volume24hr),max=Math.max(...cats.map(c=>c.volume24hr||0),1);document.getElementById('pm-bars').innerHTML=cats.map(c=>"<div class='pm-bar'><span class='pm-bar-label'>"+esc(c.label)+"</span><span class='pm-bar-track'><i class='pm-bar-fill' style='width:"+((c.volume24hr||0)/max*100).toFixed(1)+"%'></i></span><span class='pm-bar-value'>"+money(c.volume24hr)+"</span></div>").join('');
 document.getElementById('pm-categories').innerHTML=cats.map(c=>"<button type='button' class='pm-cat' data-category='"+esc(c.key)+"'><b>"+esc(c.label)+"</b><span>질문 "+Number(c.event_count||0).toLocaleString()+" · 정상 "+Number(c.ok_count||0).toLocaleString()+" · "+money(c.volume24hr)+"</span></button>").join('');const binary=(d.rankings||{}).binary||{};document.getElementById('pm-strong').innerHTML=rankRows(binary.strong);document.getElementById('pm-tight').innerHTML=rankRows(binary.tight);document.getElementById('pm-active').innerHTML=rankRows(d.most_active);bindDetails();document.querySelectorAll('[data-category]').forEach(b=>b.addEventListener('click',()=>{document.getElementById('pm-category').value=b.dataset.category;pmPage=1;loadEvents();document.getElementById('pm-explorer-title').scrollIntoView({behavior:'smooth'})}))}
async function loadBrief(){const m=document.getElementById('pm-brief-meta'),b=document.getElementById('pm-brief');const r=await fetch('/api/forecast/sector-brief');if(!r.ok){m.textContent='아직 컨센서스 정리가 없습니다.';b.innerHTML='';return}const d=await r.json();
 m.textContent='기준 '+stamp(d.written_at)+' (한국 시간)'+(d.generation_id!==pmGeneration&&pmGeneration?' · 직전 수집분 기준입니다':'')+' · 집계는 전체, 인용은 참여 규모 상위 '+(d.named_limit||0)+'건';
 b.innerHTML=(d.groups||[]).map(g=>{const head="<div class='pm-brief-h'><b>"+esc(g.label)+"</b><span>질문 "+Number(g.event_count||0).toLocaleString()+" · 24시간 "+money(g.volume24hr)+(g.probability&&g.probability.tight?' · 경합 '+g.probability.tight:'')+"</span></div>";
 const body=g.status==='ok'||g.paragraph?"<p>"+esc(g.paragraph)+(g.stale?" (직전 정리)":"")+"</p>":"<p class='empty'>"+(g.status==='insufficient_sample'?'표본이 부족해 정리하지 않았습니다.':'이번 주기에는 정리하지 못했습니다.')+"</p>";
 return "<div class='pm-brief-g'>"+head+body+"</div>"}).join('')||"<p class='empty'>정리된 분야가 없습니다.</p>"}
const pp=v=>v==null?'–':(v>0?'+':'')+(Number(v)*100).toFixed(1)+'%p';const moveClass=v=>v==null?'':(v>0?'pos':(v<0?'neg':''));
function trendMove(r){if(r.leader_changed)return "<span class='pm-trend-move'>1위 교체</span>";return "<span class='pm-trend-move "+moveClass(r.basis_change)+"'>"+esc(pp(r.basis_change))+"</span>"}
function trendRows(items){if(!items||!items.length)return "<p class='empty'>조명할 이동이 없습니다.</p>";return items.map(r=>"<button type='button' class='pm-trend-row' data-event='"+esc(r.id)+"'><span><span class='t'>"+esc(r.title)+"</span><span class='pm-meta'><span>"+esc(r.category_label||'')+"</span><span>24시간 "+money(r.volume24hr)+"</span>"+(r.crossed_half?"<span class='pm-badge bad'>50% 반전</span>":"")+(r.is_new?"<span class='pm-badge'>신규</span>":"")+"</span></span>"+trendMove(r)+"<span class='pm-trend-now'>현재 "+prob(r.probability)+(r.event_type!=='binary'&&r.leader?' · '+esc(r.leader):'')+"</span></button>").join('')}
function trendSide(items,empty){if(!items||!items.length)return "<p class='empty'>"+empty+"</p>";return items.map(r=>"<button type='button' class='pm-rank' data-event='"+esc(r.id)+"'><b>"+esc(r.title)+"</b><small>현재 "+prob(r.probability)+" · 24시간 "+money(r.volume24hr)+(r.volume_change!=null?" · 참여 규모 증감 "+money(r.volume_change):'')+"</small></button>").join('')}
async function loadTrending(){const m=document.getElementById('pm-trend-meta'),b=document.getElementById('pm-trend');const r=await fetch('/api/forecast/trending');if(!r.ok){m.textContent='아직 트렌드 집계가 없습니다.';b.innerHTML='';return}const d=await r.json();
 const basis=d.basis==='day'?('오늘 '+stamp(d.baseline_at)+' 기준선 대비'):('직전 주기 '+stamp(d.basis_at)+' 대비');
 m.textContent=(d.state==='warming_up'?'오늘 기준선을 막 세웠습니다 — 다음 주기부터 이동을 조명합니다':basis+' 이동 · 후보 '+Number(d.candidate_count||0).toLocaleString()+'건(24시간 참여 규모 '+money(d.min_volume)+' 이상)')+(pmGeneration&&d.generation_id!==pmGeneration?' · 직전 수집분 기준입니다':'');
 b.innerHTML=trendRows(d.spotlight);document.getElementById('pm-trend-new').innerHTML=trendSide(d.new_entries,'오늘 새로 들어온 예측이 없습니다.');document.getElementById('pm-trend-volume').innerHTML=trendSide(d.volume_movers,'참여가 늘어난 예측이 없습니다.');bindDetails(m.closest('section'))}
async function loadSummary(){const r=await fetch('/api/forecast/summary?include_flagged='+(pmFlagged?'true':'false'));if(!r.ok)throw new Error('summary '+r.status);renderSummary(await r.json())}
function addOptions(id,items,valueKey,labelKey){const s=document.getElementById(id);items.forEach(item=>{const o=document.createElement('option');o.value=typeof item==='string'?item:item[valueKey];o.textContent=typeof item==='string'?(PM_TYPE[item]||PM_STATUS[item]||PM_REGION[item]||item):item[labelKey];s.appendChild(o)})}
async function loadFilters(){const r=await fetch('/api/forecast/categories');if(!r.ok)return;const d=await r.json();addOptions('pm-category',d.categories||[],'key','label');addOptions('pm-tag',(d.tags||[]).slice(0,200),'tag','tag');addOptions('pm-region',d.regions||[]);addOptions('pm-type',d.event_types||[]);addOptions('pm-status',d.data_statuses||[]);const p=new URLSearchParams(location.search);['q','category','tag','region','event_type','status','sort'].forEach(k=>{const el=document.querySelector('[name="'+k+'"]');if(el&&p.get(k))el.value=p.get(k)});pmPage=Math.max(1,Number(p.get('page')||1))}
function knownParams(){const p=new URLSearchParams();document.querySelectorAll('#pm-controls [name]').forEach(el=>{if(el.value)p.set(el.name,el.value)});if(pmPage>1)p.set('page',pmPage);return p}
async function loadEvents(){const p=knownParams();history.replaceState(null,'',location.pathname+(p.toString()?'?'+p:''));const r=await fetch('/api/forecast/events?'+p);if(!r.ok)throw new Error('events '+r.status);const d=await r.json();pmPages=d.page_count||0;const si=d.search_index||{};document.getElementById('pm-result-meta').textContent='검색 결과 '+Number(d.total||0).toLocaleString()+'건'+(si.total&&si.annotated<si.total?' · 한국어 검색 준비 '+Number(si.annotated||0).toLocaleString()+'/'+Number(si.total).toLocaleString()+'건 (나머지는 영문 제목으로만 찾습니다)':'');const list=document.getElementById('pm-event-list');list.innerHTML=(d.events||[]).map(e=>"<button type='button' class='pm-event' data-event='"+esc(e.id)+"'><span><span class='pm-event-title'>"+esc(e.title)+"</span>"+(e.summary?"<span class='pm-sum'>"+esc(e.summary)+"</span>":"")+"<span class='pm-meta'><span>"+esc(e.category_label)+"</span><span>"+esc(PM_TYPE[e.event_type]||e.event_type)+"</span><span class='pm-badge "+(e.data_status==='ok'?'ok':'bad')+"'>"+esc(PM_STATUS[e.data_status]||e.data_status)+"</span><span>24시간 "+money(e.volume24hr)+"</span><span>잔액 "+money(e.liquidity)+"</span></span></span><span class='pm-prob'>"+(e.leader?esc(side(e.leader))+' '+prob(e.leader_probability):'상세 보기')+"</span></button>").join('')||"<p class='empty'>조건에 맞는 질문이 없습니다.</p>";document.getElementById('pm-page').textContent=(d.page_count?d.page:0)+' / '+d.page_count;document.getElementById('pm-prev').disabled=d.page<=1;document.getElementById('pm-next').disabled=d.page>=d.page_count;bindDetails(list)}
async function openDetail(id){const r=await fetch('/api/forecast/events/'+encodeURIComponent(id));if(!r.ok)return;const d=await r.json();document.getElementById('pm-detail-title').textContent=d.title;const outcomes=(d.markets||[]).map(m=>{const v=m.yes_probability;return "<div class='pm-outcome'><div class='pm-outcome-head'><b>"+esc(m.outcome_label||m.question||'결과')+"</b><span>예 "+prob(v)+" · 아니오 "+prob(m.no_probability)+"</span></div><div class='pm-prog'><i style='width:"+(v==null?0:Math.max(0,Math.min(100,v*100)))+"%'></i></div></div>"}).join('');document.getElementById('pm-detail-body').innerHTML="<p class='pm-meta'><span>"+esc(d.category_label)+"</span><span>"+esc(PM_TYPE[d.event_type]||d.event_type)+"</span><span>"+esc(PM_STATUS[d.data_status]||d.data_status)+"</span></p>"+(d.description?"<p class='body-text'>"+esc(d.description)+"</p>":'')+outcomes+"<p class='pm-source'>종료 "+esc(stamp(d.end_date))+"</p>";document.getElementById('pm-detail').showModal()}
document.getElementById('pm-controls').addEventListener('change',()=>{pmPage=1;loadEvents()});let searchTimer;document.getElementById('pm-q').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{pmPage=1;loadEvents()},250)});document.getElementById('pm-prev').addEventListener('click',()=>{if(pmPage>1){pmPage--;loadEvents()}});document.getElementById('pm-next').addEventListener('click',()=>{if(pmPage<pmPages){pmPage++;loadEvents()}});document.getElementById('pm-detail-close').addEventListener('click',()=>document.getElementById('pm-detail').close());document.getElementById('pm-flag-toggle').addEventListener('click',e=>{pmFlagged=!pmFlagged;e.currentTarget.textContent=pmFlagged?'정상 질문만':'주의 질문 포함';loadSummary()});Promise.all([loadFilters(),loadSummary()]).then(loadEvents).then(()=>loadBrief().catch(()=>{document.getElementById('pm-brief-meta').textContent='컨센서스 정리를 읽지 못했습니다.'})).then(()=>loadTrending().catch(()=>{document.getElementById('pm-trend-meta').textContent='트렌드 집계를 읽지 못했습니다.'})).catch(()=>{const a=document.getElementById('pm-alert');a.className='pm-alert warn';a.textContent='현재 예측 컨센서스 자료를 읽지 못했습니다.'});
</script>"""
)

POLYMARKET_HTML = page("집단 예측 컨센서스", "/forecast", _POLYMARKET_MAIN, _POLYMARKET_SCRIPT)
