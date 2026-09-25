"""뉴스·시장 자연어 검색 화면."""

from services.web.pages.shell import JS_UTIL, I_DOC, h1, page

_MAIN = h1(I_DOC, "뉴스·시장 검색") + """
<p class='sub2'>궁금한 내용을 문장으로 입력하세요. 국가·기간·주제를 읽어 발행된 뉴스와 시장 요약을 찾습니다.</p>
<style>
.ns-form{display:flex;flex-wrap:wrap;gap:10px;margin:24px 0 12px}
.ns-form input{flex:1 1 320px;min-width:0}
.ns-form input,.ns-form select,.ns-form button,.ns-example,.ns-pages button{
font:inherit;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--surface-1);color:var(--ink)}
.ns-form button{background:var(--ink);color:var(--bg);cursor:pointer}
.ns-examples{display:flex;flex-wrap:wrap;gap:8px}.ns-example{font-size:13px;cursor:pointer;padding:8px 10px}
.ns-note{font-size:13px;color:var(--mut);line-height:1.8}.ns-results{display:grid;gap:12px;margin-top:20px}
.ns-result{padding:20px;background:var(--surface-1);border:1px solid var(--line);border-radius:12px;scroll-margin-top:110px}
.ns-result h2{font-size:17px;margin:8px 0;overflow-wrap:anywhere}.ns-result p{margin:8px 0;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere}
.ns-meta{font-size:12px;color:var(--mut)}.ns-result a{color:var(--ink);text-underline-offset:3px}
.ns-pages{display:flex;justify-content:center;align-items:center;gap:14px;margin:24px 0}
.ns-pages button:disabled{opacity:.4}.ns-result:target{outline:2px solid var(--gold)}
</style>
<form id='ns-form' class='ns-form' role='search'>
 <input id='ns-q' name='q' type='search' maxlength='200' aria-label='뉴스와 시장 검색어'
 placeholder='최근 일주일 일본 금리 뉴스' autocomplete='off'>
 <select id='ns-market' name='market' aria-label='시장 선택'>
 <option value=''>국가 자동 인식</option><option value='CN'>중국</option><option value='HK'>홍콩</option>
 <option value='US'>미국</option><option value='KR'>한국</option><option value='JP'>일본</option></select>
 <select id='ns-days' name='days' aria-label='검색 기간'>
 <option value=''>기간 자동 인식</option><option value='1'>오늘</option><option value='7'>최근 7일</option>
 <option value='30'>최근 30일</option></select><button type='submit'>검색</button>
</form>
<div class='ns-examples' role='group' aria-label='검색 예시'>
 <button class='ns-example' type='button'>최근 일주일 일본 금리 뉴스</button>
 <button class='ns-example' type='button'>미국 반도체 악재</button>
 <button class='ns-example' type='button'>어제 한국 시장</button>
</div>
<p class='ns-note'>최근 30일의 저장된 자료를 검색합니다. 국가·기간을 직접 선택하면 문장 속 조건보다 우선합니다.
호재·악재는 보도 논조 점수로 구분합니다. 원인 설명이나 미래 예측을 새로 생성하지 않습니다.</p>
<p id='ns-status' class='ns-note' role='status' aria-live='polite'></p>
<p id='ns-source' class='ns-meta'></p>
<div id='ns-results' class='ns-results' aria-busy='false'></div>
<div class='ns-pages'><button id='ns-prev' type='button' disabled>이전</button>
<span id='ns-page'></span><button id='ns-next' type='button' disabled>다음</button></div>
"""

_SCRIPT = "<script>" + JS_UTIL + """
const nsLabels={CN:'중국',HK:'홍콩',US:'미국',KR:'한국',JP:'일본'};
const nsKind={news:'주요 기사',report:'시장상황 보고서',market:'일일 시장 요약'};
const nsForm=document.getElementById('ns-form'),nsList=document.getElementById('ns-results');
const nsStatus=document.getElementById('ns-status');
let nsPage=1,nsPages=0,nsController;
function nsParams(){const p=new URLSearchParams();for(const key of ['q','market','days']){
 const value=document.getElementById('ns-'+key).value.trim();if(value)p.set(key,value);}
 if(nsPage>1)p.set('page',nsPage);return p;}
function nsCard(row){const a=document.createElement('article');a.className='ns-result';a.id=row.id;
 const m=document.createElement('div');m.className='ns-meta';
 m.textContent=[row.market_label,nsKind[row.kind]||'',row.date].filter(Boolean).join(' · ');a.append(m);
 const title=document.createElement('h2');title.textContent=row.title;a.append(title);
 if(row.text&&row.text!==row.title){const body=document.createElement('p');body.textContent=row.text;a.append(body);}
 const source=document.createElement('div');source.className='ns-meta';
 source.textContent='출처: '+row.source+(row.published_at?' · '+stamp(row.published_at)+' (한국 시간)':' · 날짜 기준');
 if(typeof row.sentiment==='number')source.textContent+=' · 보도 감성 '+(row.sentiment>=0?'+':'')+row.sentiment.toFixed(2);
 a.append(source);
 if(row.url){try{const url=new URL(row.url);if(['https:','http:'].includes(url.protocol)){
 const link=document.createElement('a');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';
 link.textContent='원문 보기';a.append(link);}}catch(e){}}
 return a;}
async function nsSearch(){if(nsController)nsController.abort();const controller=new AbortController();nsController=controller;
 const p=nsParams();history.replaceState(null,'','/search'+(p.size?'?'+p:''));
 nsStatus.textContent='자료를 찾고 있습니다…';nsList.setAttribute('aria-busy','true');
 document.getElementById('ns-prev').disabled=true;document.getElementById('ns-next').disabled=true;
 try{const response=await fetch('/api/search?'+p,{signal:controller.signal});
 if(!response.ok){const error=await response.json();throw new Error(typeof error.detail==='string'?error.detail:'검색 조건을 확인해 주세요.');}
 const d=await response.json();if(controller!==nsController)return;
 nsPages=d.page_count;const f=d.filters;
 const interpreted=[f.markets.length?f.markets.map(c=>nsLabels[c]||c).join('·'):'전체 국가',
 f.start_date+' ~ '+f.end_date,...f.topics,...f.keywords];
 if(f.sentiment)interpreted.push(f.sentiment==='positive'?'긍정 보도':'부정 보도');
 nsStatus.textContent=interpreted.join(' · ')+' — '+d.total+'건';
 nsList.replaceChildren(...d.results.map(nsCard));
 if(!d.results.length){const empty=document.createElement('p');empty.className='empty';
 empty.textContent=d.available_documents?'조건에 맞는 자료가 없습니다. 기간을 넓히거나 주제를 짧게 입력해 보세요.':'아직 검색할 자료가 없습니다. 뉴스 보고서와 시장 요약이 발행되면 검색할 수 있습니다.';nsList.append(empty);}
 const times=d.sources_updated_at;document.getElementById('ns-source').textContent=
 ['뉴스 자료 '+(stamp(times.news)||'수집 대기'),'시장 요약 '+(stamp(times.market)||'수집 대기')].join(' · ')+' (한국 시간)';
 document.getElementById('ns-page').textContent=nsPages?nsPage+' / '+nsPages:'';
 document.getElementById('ns-prev').disabled=nsPage<=1;document.getElementById('ns-next').disabled=nsPage>=nsPages;
 }catch(e){if(e.name!=='AbortError'){nsList.replaceChildren();nsStatus.textContent=e.message||'검색 자료를 읽지 못했습니다.';}}
 finally{if(controller===nsController)nsList.setAttribute('aria-busy','false');}}
nsForm.addEventListener('submit',e=>{e.preventDefault();nsPage=1;nsSearch();});
document.querySelectorAll('.ns-example').forEach(b=>b.addEventListener('click',()=>{
 document.getElementById('ns-q').value=b.textContent;document.getElementById('ns-market').value='';
 document.getElementById('ns-days').value='';nsPage=1;nsSearch();}));
document.getElementById('ns-prev').addEventListener('click',()=>{if(nsPage>1){nsPage--;nsSearch();}});
document.getElementById('ns-next').addEventListener('click',()=>{if(nsPage<nsPages){nsPage++;nsSearch();}});
const nsInitial=new URLSearchParams(location.search);for(const key of ['q','market','days']){
 if(nsInitial.has(key))document.getElementById('ns-'+key).value=nsInitial.get(key);}
nsPage=Math.max(1,Math.min(1000,Number(nsInitial.get('page'))||1));nsSearch();
</script>"""

SEARCH_HTML = page(
    "뉴스·시장 검색",
    "/search",
    _MAIN,
    _SCRIPT,
    description="국가·기간·주제를 문장으로 적으면 발행된 뉴스 보고서와 시장 요약에서 찾아 줍니다. "
    "최근 30일의 공개 자료만 검색합니다.",
)
