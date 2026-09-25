"""리서치 화면(`/research`). 다른 면과 같이 공개다(2026-09-24 인증을 없앴다)."""

from services.web.pages.shell import (
    I_CHART,
    I_DOC,
    I_LAYERS,
    I_SCALE,
    I_SHIELD,
    JS_UTIL,
    h1,
    icon,
    page,
)

_RESEARCH_MAIN = (
    h1(I_LAYERS, "리서치")
    + "<div class='disc'>관심 주제를 놓고 후보 종목의 <b>추가·주목·제외</b>와 리스크, "
    "그리고 그 주제를 약화시키는 반론을 정리합니다. 아래는 마지막으로 저장된 분석 "
    "한 건입니다.</div>"
    + "<div class='sub2'>리서치 실행은 텔레그램에만 있습니다. 이 페이지는 결과를 "
    "<b>보여 주기만</b> 하며 분석을 시작시키지 않습니다.</div>"
    + """
<div class='histbox'>
  <div class='histh'><span class='phico'>"""
    + icon(I_CHART)
    + """</span>연구 결과</div>
  <dl class='brief research-meta'>
    <div><dt>관심 주제</dt><dd id='r-sight'>–</dd></div>
    <div><dt>기준 시각</dt><dd id='r-time'>–</dd></div>
    <div><dt>분석 뉴스</dt><dd id='r-news'>–</dd></div>
    <div><dt>후보 종목</dt><dd id='r-cand'>–</dd></div>
  </dl>
</div>

<div class='histbox'>
  <div class='histh'><span class='phico'>"""
    + icon(I_DOC)
    + """</span>요약</div>
  <div class='brief'><p class='body-text' id='r-summary'>산출물을 불러오는 중…</p></div>
</div>

<div class='histbox' id='sec-actions' hidden>
  <div class='histh'><span class='phico'>"""
    + icon(I_LAYERS)
    + """</span>종목 판단</div>
  <div id='r-actions'></div>
</div>

<div class='histbox' id='sec-risks' hidden>
  <div class='histh'><span class='phico'>"""
    + icon(I_SHIELD)
    + """</span>리스크</div>
  <div class='brief'><ul class='checks' id='r-risks'></ul></div>
</div>

<div class='histbox' id='sec-critique' hidden>
  <div class='histh'><span class='phico'>"""
    + icon(I_SCALE)
    + """</span>내 뷰 반론</div>
  <div id='r-critique'></div>
</div>

<div class='histbox'><div class='docbody'>
  <p>종목 판단은 관심종목을 정리하기 위한 메모이며 매매 권유가 아닙니다.
  관련도와 판단 수치는 모델이 매긴 값이라 사실 확인을 대신하지 않습니다.</p>
</div></div>
"""
)

_RESEARCH_SCRIPT = (
    "<script>" + JS_UTIL + """
const TAGS={add:['tag-add','추가'],watch:['tag-watch','주목'],remove:['tag-remove','제외']};
const ORDER={add:0,watch:1,remove:2};
function actionCard(a){
  const t=TAGS[a.action];
  const scores=[];
  if(a.relevance!=null)scores.push('관련도 '+pct(a.relevance));
  if(a.confidence!=null)scores.push('판단 '+pct(a.confidence));
  return "<div class='rc'><div class='rt'>"
    +"<span class='tag "+t[0]+"'>"+esc(t[1])+"</span>"
    +"<b>"+esc(a.name||a.ticker||'')+"</b>"
    +"<code>"+esc(a.ticker||'')+"</code>"
    +(scores.length?"<span class='sc'>"+scores.map(s=>"<span>"+esc(s)+"</span>").join('')+"</span>":"")
    +"</div>"+(a.reason?"<p class='rz'>"+esc(a.reason)+"</p>":"")+"</div>";
}
fetch('/api/research').then(r=>r.json()).then(d=>{
  const res=d.last_result||{};
  document.getElementById('r-sight').textContent=d.sight||'없음';
  document.getElementById('r-time').innerHTML=stampHtml(d.generated_at);
  document.getElementById('r-news').textContent=res.news_count!=null?res.news_count:'–';
  document.getElementById('r-cand').textContent=res.candidate_count!=null?res.candidate_count:'–';
  document.getElementById('r-summary').textContent=res.summary||'저장된 분석이 없습니다.';

  const actions=(res.actions||[]).filter(a=>a&&TAGS[a.action])
    .sort((a,b)=>ORDER[a.action]-ORDER[b.action]||(b.relevance||0)-(a.relevance||0));
  if(actions.length){
    document.getElementById('r-actions').innerHTML=actions.map(actionCard).join('');
    document.getElementById('sec-actions').hidden=false;
  }
  const risks=(res.risks||[]).filter(Boolean);
  if(risks.length){
    document.getElementById('r-risks').innerHTML=risks.map(r=>"<li>"+esc(r)+"</li>").join('');
    document.getElementById('sec-risks').hidden=false;
  }
  const critique=(res.view_critique||[]).filter(c=>c&&c.point);
  if(critique.length){
    document.getElementById('r-critique').innerHTML=critique.map(c=>
      "<div class='rc'><div class='rt'><b>"+esc(c.point)+"</b>"
      +(c.severity!=null?"<span class='sc'><span>강도 "+pct(c.severity)+"</span></span>":"")
      +"</div></div>").join('');
    document.getElementById('sec-critique').hidden=false;
  }
}).catch(()=>{
  document.getElementById('r-summary').textContent='산출물을 읽지 못했습니다.';
});
</script>"""
)

RESEARCH_HTML = page(
    "리서치",
    "/research",
    _RESEARCH_MAIN,
    _RESEARCH_SCRIPT,
    description="관심 주제를 놓고 후보 종목의 추가·주목·제외와 리스크, 반론을 정리한 마지막 분석 "
    "한 건입니다. 투자 권유가 아닙니다.",
)
