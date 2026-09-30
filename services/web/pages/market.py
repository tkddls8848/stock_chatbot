"""시장 논조 화면(`/`).

리서치 리포트 지면처럼 짠다(2026-09-29): 데이터로 쓴 표제와 리드문, 시장별 작은 추세
차트, "한눈에 보기" 표, 시장별 최신 요약. 차트는 `/api/market`의 일별 값으로 브라우저가
SVG를 그린다 — 추세선은 봇 차트(`market_sentiment/chart.py`)와 같은 가우시안 커널 국소
선형 회귀(표준편차 4일, 기사 수의 제곱근 가중)다. 도메인끼리 import하지 않으므로 같은
계산을 여기 JS로 한 번 더 적는다.
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
@media(max-width:900px){.mk-grid{grid-template-columns:1fr;gap:28px}.mk-small{grid-template-columns:repeat(2,minmax(0,1fr))}.mk-notes{columns:1}}
@media(max-width:520px){.mk-small{gap:16px 14px}.mk-small figcaption{font-size:14px}}
</style>"""

_MARKET_MAIN = (
    _MARKET_STYLE
    + "<p class='kicker' id='mk-kicker'>시장 논조 · 최근 30일</p>"
    + h1(I_CHART, "<span id='headline'>시장별 뉴스 논조</span>")
    + "<div class='disc' id='deck'>중국·홍콩·미국·한국·일본·유럽 뉴스를 시장별로 묶어 하루치 요약을 만들고, "
    "그 요약에 매긴 <b>−1 ~ +1</b> 감성 점수의 추세를 그립니다. 감성은 보도 논조의 방향이지 "
    "시세나 수익률이 아닙니다.</div>"
    + """
<div class='mk-grid'>
 <section class='mk-fig' aria-labelledby='mk-fig-title'>
  <h2 id='mk-fig-title'>시장별 논조 추세 (커널 회귀, 점은 일별 값)</h2>
  <div class='mk-small' id='small'><p class='empty'>산출물을 불러오는 중…</p></div>
  <p class='mk-src' id='mk-src'>자료: 시장별 뉴스 헤드라인 일일 요약</p>
 </section>
 <aside class='mk-side' aria-labelledby='mk-side-title'>
  <h2 id='mk-side-title'>한눈에 보기</h2>
  <div class='tablewrap' tabindex='0' role='region' aria-label='시장별 논조 수치 표'>
   <table>
    <thead><tr><th>시장</th><th class='r'>현재</th><th class='r'>7일 변화</th><th class='r'>30일 평균</th></tr></thead>
    <tbody id='rows'><tr><td colspan='4' class='empty'>산출물을 불러오는 중…</td></tr></tbody>
   </table>
  </div>
  <p class='mk-note'><b>읽는 법.</b> 값은 보도 논조의 방향이며 가격·수익률이 아닙니다.
  <b class='pos'>빨강이 긍정</b>, <b class='neg'>파랑이 부정</b>입니다. '현재'는 추세선의 마지막 값이고,
  기사가 적은 날은 추세에 덜 반영됩니다.</p>
 </aside>
</div>
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
// 봇 차트와 같은 가우시안 커널 국소 선형 회귀. 점이 4개 미만이면 추세라 부르지 않는다.
function trend(daily){
  const pts=daily.map(p=>({t:Date.parse(p.date),v:Number(p.avg_sentiment)||0,w:Math.sqrt(Math.max(Number(p.count)||0,1))}))
    .filter(p=>!isNaN(p.t)).sort((a,b)=>a.t-b.t);
  if(pts.length<4)return {pts,curve:pts.map(p=>[p.t,p.v])};
  const t0=pts[0].t,t1=pts[pts.length-1].t,bw=4,curve=[];
  for(let t=t0;t<=t1;t+=DAY/4){
    let s0=0,s1=0,s2=0,y0=0,y1=0;
    for(const p of pts){const x=(p.t-t)/DAY,k=Math.exp(-.5*(x/bw)**2)*p.w;s0+=k;s1+=k*x;s2+=k*x*x;y0+=k*p.v;y1+=k*x*p.v;}
    const det=s0*s2-s1*s1;
    curve.push([t,Math.max(-1,Math.min(1,Math.abs(det)<1e-9?y0/s0:(s2*y0-s1*y1)/det))]);
  }
  return {pts,curve};
}
function svg(tr,color,t0,t1){
  const W=260,H=150,L=28,T=6,B=18,R=6,iw=W-L-R,ih=H-T-B;
  const X=t=>L+(t-t0)/Math.max(t1-t0,1)*iw,Y=v=>T+(1-(v+1)/2)*ih;
  let s="<svg viewBox='0 0 "+W+" "+H+"' role='img' aria-hidden='true'>";
  for(const v of [-1,-.5,0,.5,1]){
    s+="<line x1='"+L+"' x2='"+(W-R)+"' y1='"+Y(v).toFixed(1)+"' y2='"+Y(v).toFixed(1)+"' stroke='"+(v===0?'#8a857b':'#e7e2d9')+"' stroke-width='"+(v===0?1:.7)+"'/>";
    s+="<text x='"+(L-5)+"' y='"+(Y(v)+3.5).toFixed(1)+"' text-anchor='end' font-size='9.5' fill='#8a857b'>"+(v>0?'+':'')+v.toFixed(1)+"</text>";
  }
  for(let t=t0;t<=t1;t+=7*DAY){const d=new Date(t);
    s+="<text x='"+X(t).toFixed(1)+"' y='"+(H-4)+"' text-anchor='middle' font-size='9.5' fill='#8a857b'>"+String(d.getMonth()+1).padStart(2,'0')+"."+String(d.getDate()).padStart(2,'0')+"</text>";}
  for(const p of tr.pts)s+="<circle cx='"+X(p.t).toFixed(1)+"' cy='"+Y(p.v).toFixed(1)+"' r='1.9' fill='#8a857b' opacity='.45'/>";
  s+="<polyline points='"+tr.curve.map(([t,v])=>X(t).toFixed(1)+","+Y(v).toFixed(1)).join(' ')+"' fill='none' stroke='"+color+"' stroke-width='2.2' stroke-linejoin='round'/>";
  return s+"</svg>";
}
fetch('/api/market').then(r=>r.json()).then(d=>{
  const markets=d.markets||{};
  const rows=[...new Set([...MARKETS,...Object.keys(markets)])].map(code=>{
    const v=markets[code];
    if(!v||!Array.isArray(v.daily)||!v.daily.length)return {code,name:LABELS[code]||code,missing:true};
    const tr=trend(v.daily),c=tr.curve,now=c[c.length-1][1];
    const back=c.filter(([t])=>c[c.length-1][0]-t>=7*DAY);
    const daily=[...v.daily].sort((a,b)=>String(a.date).localeCompare(String(b.date)));
    const last=daily[daily.length-1];
    return {code,name:LABELS[code]||code,tr,now,chg:now-(back.length?back[back.length-1][1]:c[0][1]),
      avg:Number(v.avg_sentiment)||0,count:Number(v.count)||0,last};
  });
  const ready=rows.filter(r=>!r.missing).sort((a,b)=>b.now-a.now);
  const all=[...ready,...rows.filter(r=>r.missing)];
  if(!ready.length){document.getElementById('small').innerHTML="<p class='empty'>아직 그릴 자료가 없습니다.</p>";}
  else{
    const hi=ready[0],lo=ready[ready.length-1],n=ready.length,total=ready.reduce((s,r)=>s+r.count,0);
    const cooled=ready.filter(r=>r.chg<0).length;
    document.getElementById('headline').textContent=
      lo.now>=0?n+'개 시장 모두 긍정 쪽이다. '+josa(hi.name,'이','가')+' 가장 높다'
      :hi.now<0?n+'개 시장의 뉴스 논조가 모두 부정 쪽으로 기울었다. '+josa(lo.name,'이','가')+' 가장 낮다'
      :hi.name+' 보도가 가장 긍정적이고, '+josa(lo.name,'은','는')+' 가장 부정적이다';
    document.getElementById('deck').textContent=n+'개 시장의 뉴스 '+total.toLocaleString('ko-KR')
      +'건을 하루 단위로 요약해 −1(부정)~+1(긍정)로 매긴 값의 추세입니다. 최근 일주일 사이 '
      +(cooled>=n/2?cooled+'개 시장의 논조가 식었습니다.':(n-cooled)+'개 시장의 논조가 올랐습니다.')
      +' 시세나 수익률이 아닙니다.';
    if(d.lookback_days)document.getElementById('mk-kicker').textContent='시장 논조 · 최근 '+d.lookback_days+'일';
    const t0=Math.min(...ready.map(r=>r.tr.pts[0].t)),t1=Math.max(...ready.map(r=>r.tr.pts[r.tr.pts.length-1].t));
    document.getElementById('small').innerHTML=ready.map(r=>"<figure><figcaption><b>"+esc(r.name)+"</b><span class='"+tone(r.now)+"'>"+sgn(r.now)+"</span></figcaption>"
      +svg(r.tr,COLORS[r.code]||'#1b2f4b',t0,t1)
      +"<p class='d'>7일 변화 "+sgn(r.chg)+" · 기사 "+r.count.toLocaleString('ko-KR')+"건</p></figure>").join('');
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
