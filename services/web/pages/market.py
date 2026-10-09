"""시장 논조 화면(`/`).

리서치 리포트 지면처럼 짠다(2026-09-29): 데이터로 쓴 표제와 리드문, 시장별 작은 추세
차트, "한눈에 보기" 표, 시장별 최신 요약. 차트는 `/api/market`의 일별 값으로 브라우저가
SVG를 그린다 — 선은 봇 차트(`market_sentiment/chart.py`)와 같은 누적 논조선(날마다 (논조 − 전 시장
공통 평균)을 그 시장의 하루 변동폭 σ로 나누고, 이어지는 움직임을 증폭해 쌓는다, 2026-10-04)이다. 예전 커널 회귀는 선이 평균에
붙어 경향이 보이지 않았다. 도메인끼리 import하지 않으므로 같은 계산을 여기 JS로 한 번 더 적는다.

"오늘의 영상"(운영자 요청 2026-10-09)은 쇼츠가 게시 뒤 `storage/public/shorts/`에 쓴 언어별 최신 영상을
`/api/shorts`로 읽어 YouTube 플레이어(youtube-nocookie)로 붙인다. 이 화면의 CSP만 그 프레임을 허용한다.
"""

from services.web.pages.shell import (
    I_CHART,
    JS_UTIL,
    h1,
    page,
)

_MARKET_STYLE = """<style>
.mk-grid{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:48px;margin-top:8px}
.mk-fig h2,.mk-side h2{font-size:13.5px;font-weight:700;margin:0 0 12px;padding:10px 0 6px;
border-top:1px solid var(--rule);border-bottom:1px solid var(--line2)}
.mk-small{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px 24px}
.mk-small figure{margin:0}
.mk-small figcaption{display:flex;justify-content:space-between;align-items:baseline;font-size:15px}
.mk-small figcaption b{font-weight:700}.mk-small figcaption span{font-weight:700;font-size:15px}
.mk-small svg{display:block;width:100%;height:auto}
.mk-small .d{margin:2px 0 0;font-size:12.5px;color:var(--mut)}
.mk-src{font-size:12.5px;color:var(--mut);margin:16px 0 0}
.mk-side .tablewrap{border-top:0}
.mk-side .tablewrap table{min-width:0;font-size:14px}
.mk-side .tablewrap th.r,.mk-side .tablewrap td.r{width:auto}
.mk-side .tablewrap thead th:nth-child(2),.mk-side .tablewrap tbody td:nth-child(2){width:auto}
.mk-side .tablewrap tbody td,.mk-side .tablewrap thead th{padding:8px 0 8px 10px}
.mk-side .tablewrap tbody td:first-child,.mk-side .tablewrap thead th:first-child{padding-left:0}
.mk-note{font-size:13.5px;color:var(--ink-soft);line-height:1.75;margin:20px 0 0;padding-top:12px;border-top:1px solid var(--line2)}
.mk-notes{margin-top:44px;border-top:1px solid var(--rule);padding-top:4px;columns:2;column-gap:48px}
.mk-notes section{break-inside:avoid;padding:16px 0;border-bottom:1px solid var(--line2)}
.mk-notes h3{font-family:var(--font-serif);font-size:21px;margin:0;letter-spacing:-.03em}
.mk-notes h3 small{font-family:var(--font-sans);font-size:12.5px;color:var(--mut);font-weight:500;margin-left:8px}
.mk-notes .k{margin:2px 0 6px;font-size:13px;color:var(--mut)}.mk-notes .k b{font-weight:700}
.mk-notes p{margin:0;color:var(--ink-soft)}
.mk-shorts{margin-top:44px}
.mk-shorts h2{font-size:13.5px;font-weight:700;margin:0;padding:10px 0 6px;border-top:1px solid var(--rule);border-bottom:1px solid var(--line2)}
.mk-shorts .d{margin:12px 0 16px;font-size:13.5px;color:var(--ink-soft)}
.mk-vids{display:grid;grid-template-columns:repeat(2,minmax(0,260px));gap:24px}
.mk-vids figure{margin:0}
.mk-vids iframe{display:block;width:100%;aspect-ratio:9/16;border:0;background:#111}
.mk-vids figcaption{margin-top:8px;font-size:13px;color:var(--mut)}
.mk-vids figcaption b{color:var(--ink);font-weight:700;margin-right:6px}
@media(max-width:900px){.mk-grid{grid-template-columns:1fr;gap:28px}.mk-small{grid-template-columns:repeat(2,minmax(0,1fr))}.mk-notes{columns:1}}
@media(max-width:520px){.mk-vids{gap:12px}.mk-small{gap:16px 14px}.mk-small figcaption{font-size:14px}}
</style>"""

_MARKET_MAIN = (
    _MARKET_STYLE
    + "<p class='kicker' id='mk-kicker'>시장 논조 · 최근 30일</p>"
    + h1(I_CHART, "<span id='headline'>시장별 뉴스 논조</span>")
    + "<div class='disc' id='deck'>중국·홍콩·미국·한국·일본·유럽 뉴스를 시장별로 묶어 하루치 요약을 만들고, "
    "그 요약에 매긴 <b>−1 ~ +1</b> 감성 점수가 시간에 따라 어느 쪽으로 기우는지 그립니다. 감성은 보도 논조의 방향이지 "
    "시세나 수익률이 아닙니다.</div>"
    + """
<div class='mk-grid'>
 <section class='mk-fig' aria-labelledby='mk-fig-title'>
  <h2 id='mk-fig-title'>시장별 논조 경향 (전 시장 평균 대비, 증폭)</h2>
  <div class='mk-small' id='small'><p class='empty'>산출물을 불러오는 중…</p></div>
  <p class='mk-src' id='mk-src'>자료: 시장별 뉴스 헤드라인 일일 요약</p>
 </section>
 <aside class='mk-side' aria-labelledby='mk-side-title'>
  <h2 id='mk-side-title'>한눈에 보기</h2>
  <div class='tablewrap' tabindex='0' role='region' aria-label='시장별 논조 수치 표'>
   <table>
    <thead><tr><th>시장</th><th class='r'>최근 7일</th><th class='r'>7일 경향</th><th class='r'>30일 평균</th></tr></thead>
    <tbody id='rows'><tr><td colspan='4' class='empty'>산출물을 불러오는 중…</td></tr></tbody>
   </table>
  </div>
  <p class='mk-note'><b>읽는 법.</b> 값은 보도 논조의 방향이며 가격·수익률이 아닙니다.
  <b class='pos'>빨강이 긍정</b>, <b class='neg'>파랑이 부정</b>입니다. 차트의 선은 날마다 그 시장의 논조가
  전 시장 평균보다 얼마나 긍정적이었는지를 그 시장의 하루 변동폭으로 나눠 쌓은 값입니다. 같은 방향으로 이어지는
  움직임은 증폭하고 하루씩 엇갈리는 잡음은 줄여, 선의 높이는 상대값입니다. 선이 오르면 평균보다 긍정적인 날이
  이어지는 중이고, 꺾이는 곳이 국면 전환(하루쯤 늦게 보입니다), 기울기가 경향의 세기입니다. '최근 7일'은 기사 수로
  가중한 최근 일주일 평균 논조, '7일 경향'은 그 기간 선이 오르내린 폭입니다.</p>
 </aside>
</div>
<section class='mk-shorts' id='shorts' aria-labelledby='mk-shorts-title' hidden>
 <h2 id='mk-shorts-title'>오늘의 영상</h2>
 <p class='d'>집단 예측 컨센서스에서 그날 눈여겨볼 질문을 짧은 세로 영상으로 정리해 매일 저녁 한국어판과 영어판으로 올립니다.
 재생하면 YouTube 플레이어가 열립니다.</p>
 <div class='mk-vids' id='vids'></div>
</section>
<div class='mk-notes' id='notes' aria-label='시장별 최신 요약'></div>
"""
)

_MARKET_SCRIPT = (
    "<script>" + JS_UTIL + """
const LABELS={CN:'중국 본토',HK:'홍콩',US:'미국',KR:'한국',JP:'일본',EU:'유럽',OTHER:'기타'};
const EN={CN:'China',HK:'Hong Kong',US:'United States',KR:'Korea',JP:'Japan',EU:'Europe'};
const COLORS={US:'#1b2f4b',KR:'#9a6b2f',JP:'#2f6b5a',CN:'#8c2f3c',HK:'#56627a',EU:'#5f4a7d'};
const MARKETS=['CN','HK','US','KR','JP','EU'];
const DAY=86400000;
// 받침이 있으면 앞 조사, 없으면 뒤 조사.
const josa=(w,a,b)=>{const c=w.charCodeAt(w.length-1)-0xAC00;return w+(c>=0&&c<11172&&c%28?a:b);};
const sgn=v=>(v>0?'+':v<0?'−':'')+Math.abs(v).toFixed(2);
const tone=v=>v>0?'pos':v<0?'neg':'';
// 봇 차트와 같은 누적 논조선. 기준선은 전 시장·전 기간의 기사 수 가중 평균(감성 모델의 공통 쏠림을 뺀다),
// 눈금은 시장마다 하루 변동폭 σ(최소 0.05)로 나눠 변동이 큰 시장과 작은 시장을 같은 눈금에서 본다.
function baseline(markets){let s=0,w=0;for(const v of Object.values(markets))for(const p of (v&&v.daily)||[]){const c=Math.max(Number(p.count)||0,1);s+=(Number(p.avg_sentiment)||0)*c;w+=c;}return w?s/w:0;}
function trend(daily,base){
  const pts=daily.map(p=>({t:Date.parse(p.date),v:Number(p.avg_sentiment)||0,c:Math.max(Number(p.count)||0,1)}))
    .filter(p=>!isNaN(p.t)).sort((a,b)=>a.t-b.t);
  const m=pts.reduce((a,p)=>a+p.v,0)/Math.max(pts.length,1);
  const sd=Math.max(Math.sqrt(pts.reduce((a,p)=>a+(p.v-m)**2,0)/Math.max(pts.length,1)),.05);
  // 경향 증폭: 하루 편차를 반감 1일 지수 평활로 모아 그 크기에 비례해 키운다(봇과 같은 MOMENTUM·GAIN).
  const MOMENTUM=.5,GAIN=1;let acc=0,mo=0;
  const curve=pts.map(p=>{mo=MOMENTUM*mo+(1-MOMENTUM)*(p.v-base)/sd;return [p.t,acc+=mo*(1+GAIN*Math.abs(mo))];});
  return {pts,curve};
}
function svg(tr,color,t0,t1,lim){
  const W=260,H=150,L=28,T=6,B=18,R=6,iw=W-L-R,ih=H-T-B;
  const X=t=>L+(t-t0)/Math.max(t1-t0,1)*iw,Y=v=>T+(1-(v+lim)/(2*lim))*ih;
  let s="<svg viewBox='0 0 "+W+" "+H+"' role='img' aria-hidden='true'>";
  for(const v of [-lim,-lim/2,0,lim/2,lim]){
    s+="<line x1='"+L+"' x2='"+(W-R)+"' y1='"+Y(v).toFixed(1)+"' y2='"+Y(v).toFixed(1)+"' stroke='"+(v===0?'#8a857b':'#e7e2d9')+"' stroke-width='"+(v===0?1:.7)+"'/>";
    s+="<text x='"+(L-5)+"' y='"+(Y(v)+3.5).toFixed(1)+"' text-anchor='end' font-size='9.5' fill='#8a857b'>"+(v>0?'+':'')+(Number.isInteger(v)?v:v.toFixed(1))+"</text>";
  }
  for(let t=t0;t<=t1;t+=7*DAY){const d=new Date(t);
    s+="<text x='"+X(t).toFixed(1)+"' y='"+(H-4)+"' text-anchor='middle' font-size='9.5' fill='#8a857b'>"+String(d.getMonth()+1).padStart(2,'0')+"."+String(d.getDate()).padStart(2,'0')+"</text>";}
  s+="<polyline points='"+tr.curve.map(([t,v])=>X(t).toFixed(1)+","+Y(v).toFixed(1)).join(' ')+"' fill='none' stroke='"+color+"' stroke-width='2.2' stroke-linejoin='round'/>";
  for(const [t,v] of tr.curve)s+="<circle cx='"+X(t).toFixed(1)+"' cy='"+Y(v).toFixed(1)+"' r='1.5' fill='"+color+"'/>";
  return s+"</svg>";
}
// 쇼츠가 게시한 언어별 최신 영상. 아직 오늘 영상이 없으면(매일 저녁 게시) 가장 최근 영상을 날짜와 함께 보인다.
fetch('/api/shorts').then(r=>r.json()).then(d=>{
  const today=new Date(Date.now()+9*3600000).toISOString().slice(0,10);
  const day=v=>v===today?'오늘':Number(v.slice(5,7))+'월 '+Number(v.slice(8,10))+'일';
  const vids=[['ko','한국어'],['en','English']].filter(([k])=>d[k]&&d[k].video_id).map(([k,name])=>{const v=d[k];
    return "<figure><iframe src='https://www.youtube-nocookie.com/embed/"+encodeURIComponent(v.video_id)+"?rel=0&amp;playsinline=1'"
      +" title='"+esc(name+' 영상 · '+(v.title||v.date))+"' loading='lazy' referrerpolicy='strict-origin-when-cross-origin'"
      +" allow='encrypted-media; picture-in-picture; fullscreen' allowfullscreen></iframe>"
      +"<figcaption><b>"+esc(name)+"</b>"+esc(day(v.date))+"</figcaption></figure>";});
  if(!vids.length)return;
  document.getElementById('vids').innerHTML=vids.join('');
  document.getElementById('shorts').hidden=false;
}).catch(()=>{});
fetch('/api/market').then(r=>r.json()).then(d=>{
  const markets=d.markets||{},base=baseline(markets);
  const rows=[...new Set([...MARKETS,...Object.keys(markets)])].map(code=>{
    const v=markets[code];
    if(!v||!Array.isArray(v.daily)||!v.daily.length)return {code,name:LABELS[code]||code,missing:true};
    const tr=trend(v.daily,base),c=tr.curve,end=c[c.length-1];
    const back=c.filter(([t])=>end[0]-t>=7*DAY);
    // 최근 7일: 기사 수 가중 평균 논조(수준). 7일 경향: 그 기간 누적선이 오르내린 폭.
    const recent=tr.pts.filter(p=>end[0]-p.t<7*DAY),rw=recent.reduce((a,p)=>a+p.c,0);
    const now=rw?recent.reduce((a,p)=>a+p.v*p.c,0)/rw:0;
    const daily=[...v.daily].sort((a,b)=>String(a.date).localeCompare(String(b.date)));
    const last=daily[daily.length-1];
    return {code,name:LABELS[code]||code,tr,now,chg:end[1]-(back.length?back[back.length-1][1]:0),
      avg:Number(v.avg_sentiment)||0,count:Number(v.count)||0,last};
  });
  const ready=rows.filter(r=>!r.missing).sort((a,b)=>b.now-a.now);
  const all=[...ready,...rows.filter(r=>r.missing)];
  if(!ready.length){document.getElementById('small').innerHTML="<p class='empty'>아직 그릴 자료가 없습니다.</p>";}
  else{
    const hi=ready[0],lo=ready[ready.length-1],n=ready.length,total=ready.reduce((s,r)=>s+r.count,0);
    const cooled=ready.filter(r=>r.chg<0).length;
    const lim=Math.max(2,Math.ceil(Math.max(...ready.flatMap(r=>r.tr.curve.map(([,v])=>Math.abs(v))))/2)*2);
    document.getElementById('headline').textContent=
      lo.now>=0?n+'개 시장 모두 긍정 쪽이다. '+josa(hi.name,'이','가')+' 가장 높다'
      :hi.now<0?n+'개 시장의 뉴스 논조가 모두 부정 쪽으로 기울었다. '+josa(lo.name,'이','가')+' 가장 낮다'
      :hi.name+' 보도가 가장 긍정적이고, '+josa(lo.name,'은','는')+' 가장 부정적이다';
    document.getElementById('deck').textContent=n+'개 시장의 뉴스 '+total.toLocaleString('ko-KR')
      +'건을 하루 단위로 요약해 −1(부정)~+1(긍정)로 매긴 값이 전 시장 평균보다 어느 쪽으로 기우는지 쌓아 그렸습니다. 최근 일주일 사이 '
      +(cooled>=n/2?cooled+'개 시장의 논조가 평균보다 식는 쪽으로 기울었습니다.':(n-cooled)+'개 시장의 논조가 평균보다 긍정적인 쪽으로 기울었습니다.')
      +' 시세나 수익률이 아닙니다.';
    if(d.lookback_days)document.getElementById('mk-kicker').textContent='시장 논조 · 최근 '+d.lookback_days+'일';
    const t0=Math.min(...ready.map(r=>r.tr.pts[0].t)),t1=Math.max(...ready.map(r=>r.tr.pts[r.tr.pts.length-1].t));
    document.getElementById('small').innerHTML=ready.map(r=>"<figure><figcaption><b>"+esc(r.name)+"</b><span class='"+tone(r.now)+"'>"+sgn(r.now)+"</span></figcaption>"
      +svg(r.tr,COLORS[r.code]||'#1b2f4b',t0,t1,lim)
      +"<p class='d'>7일 경향 "+sgn(r.chg)+" · 기사 "+r.count.toLocaleString('ko-KR')+"건</p></figure>").join('');
    document.getElementById('mk-src').textContent='자료: 시장별 뉴스 헤드라인 일일 요약 · 집계 '+stamp(d.generated_at)+' (한국 시간)';
    document.getElementById('notes').innerHTML=ready.map(r=>"<section><h3>"+esc(r.name)+"<small>"+esc(EN[r.code]||r.code)+"</small></h3>"
      +"<p class='k'>최근 "+esc(String(r.last.date||'').slice(5).replace('-','월 '))+"일 논조 <b class='"+tone(Number(r.last.avg_sentiment))+"'>"+sgn(Number(r.last.avg_sentiment)||0)+"</b></p>"
      +"<p>"+esc(r.last.summary||'요약이 없습니다.')+"</p></section>").join('');
  }
  document.getElementById('rows').innerHTML=all.map(r=>r.missing
    ?"<tr><td><div class='nm'>"+esc(r.name)+"</div></td><td colspan='3' class='empty'>자료 수집·분석 대기</td></tr>"
    :"<tr><td><div class='nm'>"+esc(r.name)+"</div></td><td class='r "+tone(r.now)+"'>"+sgn(r.now)+"</td>"
      +"<td class='r'>"+sgn(r.chg)+"</td><td class='r'>"+sgn(r.avg)+"</td></tr>").join('');
}).catch(()=>{
  document.getElementById('rows').innerHTML="<tr><td colspan='4' class='empty'>산출물을 읽지 못했습니다.</td></tr>";
  document.getElementById('small').innerHTML="<p class='empty'>산출물을 읽지 못했습니다.</p>";
});
</script>"""
)

INDEX_HTML = page(
    "시장 논조",
    "/",
    _MARKET_MAIN,
    _MARKET_SCRIPT,
    description="중국·홍콩·미국·한국·일본·유럽 뉴스를 시장별로 묶어 매긴 하루치 논조 점수와 그 추세입니다. "
    "정보 제공 목적이며 투자 권유가 아닙니다.",
)
