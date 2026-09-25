"""시장 컨센서스 화면(`/`)."""

from services.web.pages.shell import (
    I_BARS,
    I_CHART,
    JS_UTIL,
    h1,
    icon,
    page,
)

_MARKET_MAIN = (
    h1(I_CHART, "시장 컨센서스")
    + "<div class='disc'>중국·홍콩·미국·한국·일본 뉴스를 시장별로 묶어 하루치 요약을 만들고, "
    "그 요약에 매긴 <b>−1 ~ +1</b> 감성 점수의 추이를 그립니다. 아래 값은 마지막으로 "
    "계산된 산출물이며, 페이지를 열 때 다시 계산하지 않습니다.</div>"
    + "<div class='sub2'>감성은 보도 논조의 방향이지 시세나 수익률이 아닙니다. "
    "<b>빨강이 긍정, 파랑이 부정</b>입니다.</div>"
    + """
<div class='statstrip'>
  <div class='st'><div class='l'>기준 시각</div><div class='v ts' id='s-time'>–</div></div>
  <div class='st'><div class='l'>조회 기간</div><div class='v' id='s-days'>–</div></div>
  <div class='st'><div class='l'>관측 시장</div><div class='v' id='s-markets'>–</div></div>
  <div class='st'><div class='l'>집계 기사</div><div class='v' id='s-count'>–</div></div>
</div>

<div class='histbox'>
  <div class='histh'><span class='phico'>"""
    + icon(I_CHART)
    + """</span>감성 추이</div>
  <div class='chartcard' id='chart'>
    <img src='/market_chart.png' alt='국가별 시장 감성 추이 차트'
      onerror="this.parentNode.innerHTML=&quot;<p class='empty'>차트가 아직 생성되지 않았습니다.</p>&quot;">
  </div>
</div>

<div class='histbox'>
  <div class='histh'><span class='phico'>"""
    + icon(I_BARS)
    + """</span>국가별 수치</div>
  <div class='tablewrap' tabindex='0' role='region' aria-label='국가별 감성 수치 표'>
    <table>
      <thead><tr><th>시장</th><th>분포</th><th class='r'>감성</th><th class='r'>기사</th><th class='r'>관측일</th></tr></thead>
      <tbody id='rows'><tr><td colspan='5' class='empty'>산출물을 불러오는 중…</td></tr></tbody>
    </table>
  </div>
</div>
"""
)

_MARKET_SCRIPT = (
    "<script>" + JS_UTIL + """
const LABELS={CN:'중국 본토',HK:'홍콩',US:'미국',KR:'한국',JP:'일본',EU:'유럽',OTHER:'기타'};
const MARKETS=['CN','HK','US','KR','JP'];
function bar(v){const w=Math.min(Math.abs(v),1)*50;const side=v>=0?'left:50%':'right:50%';
  const color=v>=0?'var(--pos)':'var(--neg)';
  return "<div class='bar'><i style='"+side+";width:"+w.toFixed(1)+"%;background:"+color+"'></i></div>";}
fetch('/api/market').then(r=>r.json()).then(d=>{
  const markets=d.markets||{};
  const observed=Object.entries(markets);
  const entries=[...new Set([...MARKETS,...Object.keys(markets)])].map(code=>[code,markets[code]])
    .sort((a,b)=>(b[1]?.avg_sentiment??-Infinity)-(a[1]?.avg_sentiment??-Infinity));
  document.getElementById('s-time').innerHTML=stampHtml(d.generated_at);
  document.getElementById('s-days').textContent=d.lookback_days?d.lookback_days+'일':'–';
  document.getElementById('s-markets').textContent=observed.length||'–';
  document.getElementById('s-count').textContent=observed.reduce((n,[,v])=>n+(v.count||0),0)||'–';
  const body=document.getElementById('rows');
  body.innerHTML=entries.map(([code,v])=>{
    if(!v)return "<tr><td><div class='nm'>"+esc(LABELS[code]||code)+"</div><div class='cd'>"+esc(code)+"</div></td>"
      +"<td colspan='4' class='empty'>자료 수집·분석 대기</td></tr>";
    const s=Number(v.avg_sentiment||0);
    const days=Array.isArray(v.daily)?v.daily.length:0;
    return "<tr><td><div class='nm'>"+esc(LABELS[code]||code)+"</div><div class='cd'>"+esc(code)+"</div></td>"
      +"<td>"+bar(s)+"</td>"
      +"<td class='r "+(s>=0?'pos':'neg')+"'>"+(s>=0?'+':'')+s.toFixed(2)+"</td>"
      +"<td class='r'>"+(v.count||0)+"</td>"
      +"<td class='r'>"+days+"</td></tr>";
  }).join('');
}).catch(()=>{
  document.getElementById('rows').innerHTML="<tr><td colspan='5' class='empty'>산출물을 읽지 못했습니다.</td></tr>";
});
</script>"""
)

INDEX_HTML = page(
    "시장 컨센서스",
    "/",
    _MARKET_MAIN,
    _MARKET_SCRIPT,
    description="중국·홍콩·미국·한국·일본 뉴스를 시장별로 묶어 매긴 하루치 감성 점수와 그 추이입니다. "
    "정보 제공 목적이며 투자 권유가 아닙니다.",
)
