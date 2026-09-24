"""공개 웹 화면의 공통 뼈대 — 스타일, 헤더·푸터, `page()` 조립, 공통 스크립트.

여기에는 정적 문자열만 둔다. 페이지는 프로세스가 뜰 때 한 번 조립되고 요청마다
다시 만들지 않으며, 수치는 브라우저가 `/api/*`를 읽어 채운다. 외부 폰트·CDN을
쓰지 않는 것도 같은 이유다 - 공개 웹은 자기 프로세스 밖에서 아무것도 부르지
않는다. 로고와 아이콘은 파일이 아니라 인라인 SVG다.

색은 종이 바탕에 금색 강조를 얹고 **라이트 전용**으로 고정한다(`color-scheme:light`).
따뜻한 바탕색은 다크로 뒤집으면 같은 인상을 주지 못해, 두 벌을 만드는 대신 한 벌을
끝까지 맞춘다. 감성의 부호는 **빨강이 긍정, 파랑이 부정**이다 - 한국 시장 화면의
관례이고, 이 사이트를 읽는 사람이 다른 화면에서 종일 보는 방향이다.
"""

from __future__ import annotations

SITE_NAME = "nunchi"
SITE_BRAND_KO = "눈치"
SITE_TAGLINE = "뉴스에서 읽는 시장 감성"
SITE_HOST = "nunchi.live"

# ── 아이콘 ──────────────────────────────────────────────────────────────────
# 파일을 두지 않으려고 인라인 SVG로 쓴다. stroke 굵기를 1.6으로 맞춰 본문
# 글자 굵기와 같은 무게로 보이게 한다.

def icon(path: str, size: int = 18) -> str:
    return (
        "<svg class='ico' width='" + str(size) + "' height='" + str(size) + "'"
        " viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.6'"
        " stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>"
        + path + "</svg>"
    )


I_DOC = "<path d='M6 3h8l4 4v14H6zM14 3v4h4'/>"
I_CHART = "<path d='M4 19V5M4 19h16M8 15l3-4 3 2 4-6'/>"
I_BARS = "<path d='M5 20V10M12 20V4M19 20v-7'/>"
I_SCALE = "<path d='M4 9h16M4 9l4-4M4 9l4 4M20 15H4M20 15l-4-4M20 15l-4 4'/>"
I_LAYERS = "<path d='M12 3l9 5-9 5-9-5zM3 13l9 5 9-5'/>"
I_SHIELD = "<path d='M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z'/>"
I_SPEC = "<path d='M4 6h16M4 12h16M4 18h9'/>"
I_PLUG = "<path d='M9 3v6M15 3v6M7 9h10v3a5 5 0 01-10 0zM12 17v4'/>"


_STYLE = """<style>
:root{
color-scheme:light;

--fs-2xs:12px;--fs-xs:12px;--fs-sm:13px;--fs-md:15px;--fs-lg:19px;--fs-xl:26px;--fs-2xl:32px;
--sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sp-5:24px;--sp-6:32px;--sp-7:48px;--sp-8:64px;
--w-wide:1200px;--w-text:860px;
--fw-1:400;--fw-2:600;--fw-3:700;--fw-4:800;

--bg:#f1eee6;--ink:#2a2a24;--ink-soft:#45443c;--mut:#57554c;--faint:#6d6b60;
--line:rgba(80,74,56,.15);--line2:rgba(80,74,56,.08);
--fill-1:rgba(150,124,70,.06);--fill-2:rgba(150,124,70,.10);
--surface-1:rgba(251,249,244,.94);--surface-2:rgba(248,245,238,.90);--surface-3:#faf7f0;

--gold:#7f5f20;--gold-deep:#655017;--gold-ink:#fdfaf3;--gold-tint:#7c5d1e;
--gold-a05:rgba(127,95,32,.05);--gold-a10:rgba(127,95,32,.10);
--gold-a25:rgba(127,95,32,.25);--gold-a40:rgba(127,95,32,.40);

--acc:#2b64b8;--acc-tint:#27568f;--acc-a10:rgba(43,100,184,.10);--acc-a40:rgba(43,100,184,.40);

/* 한국 시장 관례: 빨강이 오름·호재, 파랑이 내림·악재 */
--pos:#b8323a;--neg:#2f66c0;--ok:#146b48;--warnc:#8f4511;

--ease:cubic-bezier(.2,0,0,1);--dur-1:100ms;--dur-3:250ms;--dur-4:400ms;
--elev-2:0 4px 12px -4px rgba(90,70,30,.10),0 12px 32px -16px rgba(60,55,40,.14);
--elev-3:0 8px 24px -8px rgba(90,70,30,.13),0 24px 56px -24px rgba(60,55,40,.18);
--r1:8px;--r2:12px;--r3:16px;--r-pill:999px;--ctl:40px;--chip-h:24px;--nav-h:64px;

--font-sans:'Pretendard Variable',Pretendard,'Apple SD Gothic Neo','Malgun Gothic',
  system-ui,-apple-system,sans-serif;
--font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace}
@media(any-pointer:coarse){:root{--ctl:44px}}
*{box-sizing:border-box}

body::before{content:"";position:fixed;top:0;left:0;right:0;height:2px;z-index:60;pointer-events:none;
background:linear-gradient(90deg,transparent,var(--gold) 22%,#c9a24b 50%,var(--gold) 78%,transparent)}
body{margin:0;background:var(--bg);color:var(--ink);font-size:var(--fs-md);line-height:1.7;
font-family:var(--font-sans);text-rendering:optimizeLegibility;letter-spacing:-.01em}
.ico{display:inline-block;vertical-align:-.18em;flex:none}
::selection{background:var(--gold-a25);color:var(--ink)}
a{color:inherit}
:where(a,button,th,input,[tabindex]):focus-visible{outline:2px solid var(--acc);outline-offset:2px}

/* 배경 - 종이 위에 옅은 색 번짐, 격자, 입자를 겹친다. 단색 배경이면 카드와
   본문이 같은 평면에 붙어 보인다. */
.bg{position:fixed;inset:0;z-index:-3;pointer-events:none;background:
radial-gradient(1100px 720px at 12% -8%,rgba(210,190,140,.22),transparent 58%),
radial-gradient(1000px 680px at 92% -4%,rgba(190,205,235,.20),transparent 55%),
radial-gradient(1200px 900px at 50% 118%,rgba(230,215,180,.20),transparent 60%),
linear-gradient(180deg,#f5f2ea,#f1eee6 40%,#ebe7dc)}
.bg-aurora{position:fixed;inset:0;z-index:-2;opacity:.5;pointer-events:none;background:
radial-gradient(60% 45% at 16% 18%,rgba(240,222,180,.30),transparent 70%),
radial-gradient(50% 40% at 84% 10%,rgba(205,220,245,.22),transparent 70%),
radial-gradient(62% 50% at 68% 84%,rgba(238,226,196,.24),transparent 70%)}
.bg-grid{position:fixed;inset:0;z-index:-2;pointer-events:none;opacity:.16;
background-image:linear-gradient(rgba(150,130,90,.06) 1px,transparent 1px),
linear-gradient(90deg,rgba(150,130,90,.06) 1px,transparent 1px);
background-size:52px 52px;
-webkit-mask:radial-gradient(1200px 700px at 50% 0%,#000,transparent 75%);
mask:radial-gradient(1200px 700px at 50% 0%,#000,transparent 75%)}
.bg-grain{position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.04;
background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
background-size:200px}

.wrap{max-width:var(--w-wide);margin:0 auto;padding:0 var(--sp-5)}
.skip{position:absolute;left:-9999px;top:0;z-index:100;background:var(--gold);color:var(--gold-ink);
padding:10px var(--sp-4);border-radius:0 0 8px 0;font-weight:var(--fw-3);text-decoration:none}
.skip:focus{left:0}
main#main:focus{outline:none}

/* 내비 - 종이 위에 떠 있는 알약 */
.topnav{position:sticky;top:10px;z-index:30;background:transparent;margin-top:10px}
.navin{display:flex;align-items:center;gap:20px;height:var(--nav-h);
background:rgba(252,250,244,.82);-webkit-backdrop-filter:blur(14px) saturate(150%);
backdrop-filter:blur(14px) saturate(150%);border:1px solid var(--gold-a25);
border-radius:var(--r-pill);padding-left:22px;padding-right:var(--sp-3);
box-shadow:0 10px 30px -16px rgba(90,70,30,.28),0 2px 8px -4px rgba(90,70,30,.12)}
.brand{display:flex;align-items:center;gap:var(--sp-3);color:var(--ink);text-decoration:none;
font-weight:800;font-size:var(--fs-md);letter-spacing:-.02em}
.brand-t{display:flex;flex-direction:column;line-height:1.12}
.brand-t small{font-size:var(--fs-xs);font-weight:600;color:var(--mut);letter-spacing:.04em;margin-top:1px}
.brand b{background:linear-gradient(100deg,var(--gold-deep),var(--gold) 55%,#a57d2b);
-webkit-background-clip:text;background-clip:text;color:transparent;-webkit-text-fill-color:transparent}
@supports not ((-webkit-background-clip:text) or (background-clip:text)){
.brand b{color:var(--gold)!important;-webkit-text-fill-color:currentColor}}
.navmark{flex:none;color:var(--gold);filter:drop-shadow(0 2px 8px rgba(232,192,122,.35))}
.links{display:flex;gap:var(--sp-1);margin-left:auto}
.links a{color:var(--mut);text-decoration:none;padding:var(--sp-2) 14px;border-radius:var(--r-pill);
font-size:var(--fs-sm);font-weight:600;white-space:nowrap;
transition:color var(--dur-1) var(--ease),background-color var(--dur-1) var(--ease)}
.links a[aria-current]{color:var(--gold)}
.links a[aria-current]::after{content:'';display:block;height:2px;margin-top:var(--sp-1);
border-radius:2px;background:linear-gradient(90deg,var(--gold),transparent)}

/* 발행 정보 띠 - 이 사이트가 실시간이 아니라는 사실을 상시로 둔다 */
.asof{margin-top:var(--sp-5);background:var(--fill-1);border-top:1px solid var(--gold-a25);
border-bottom:1px solid var(--gold-a25);color:var(--mut);font-size:var(--fs-sm)}
.asofin{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;
padding-top:var(--sp-3);padding-bottom:var(--sp-3)}
.mh-id{color:var(--gold-deep);font-weight:var(--fw-4);letter-spacing:.01em}
.mh-date{color:var(--ink);font-weight:var(--fw-2);font-variant-numeric:tabular-nums}
.asof-x{color:var(--mut);font-size:var(--fs-2xs);margin-left:auto}
.pubstat{display:inline-flex;align-items:center;gap:5px;font-size:var(--fs-2xs);font-weight:600;
padding:1px var(--sp-2);border-radius:20px;border:1px solid var(--line2);color:var(--ok)}
.pubstat .pubdot{width:7px;height:7px;border-radius:50%;background:var(--ok);display:inline-block}
.pubstat.none{color:var(--warnc)}.pubstat.none .pubdot{background:var(--warnc)}

/* 본문 */
.page{padding-top:var(--sp-7)}
h1.ph{font-size:var(--fs-xl);font-weight:800;letter-spacing:-.03em;display:flex;
align-items:center;gap:var(--sp-3);margin:0}
.phico{color:var(--gold);display:inline-flex;flex:none}
.disc{background:var(--gold-a05);border:1px solid var(--gold-a25);border-radius:var(--r2);
padding:var(--sp-3) var(--sp-4);color:var(--gold-tint);font-size:var(--fs-sm);line-height:1.7;
margin:var(--sp-4) 0}
.disc b{color:var(--ink)}
.sub2{color:var(--mut);font-size:var(--fs-sm);margin:var(--sp-3) 0 var(--sp-4)}
.sub2 b{color:var(--ink)}

.pgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:var(--sp-3);
margin:var(--sp-4) 0 var(--sp-2)}
.pcard{position:relative;background:var(--surface-2);border:1px solid var(--line);
border-radius:var(--r3);padding:var(--sp-4);box-shadow:var(--elev-2);overflow:hidden;
transition:transform var(--dur-3) var(--ease),border-color var(--dur-1) var(--ease)}
.pcard::after{content:'';position:absolute;left:0;right:0;top:0;height:2px;opacity:.8}
.pc-gold::after{background:linear-gradient(90deg,var(--gold),transparent)}
.pc-acc::after{background:linear-gradient(90deg,var(--acc),transparent)}
.pc-ok::after{background:linear-gradient(90deg,var(--ok),transparent)}
.pc-warn::after{background:linear-gradient(90deg,var(--warnc),transparent)}
.pcard-t{font-weight:800;color:var(--gold-tint);font-size:var(--fs-md);margin-bottom:6px;
letter-spacing:-.01em}
.pcard-s{color:var(--mut);font-size:var(--fs-sm);line-height:1.7}

.histbox{margin-top:40px}
.histh{display:flex;align-items:center;gap:var(--sp-2);font-weight:800;font-size:var(--fs-md);
margin-bottom:var(--sp-3);letter-spacing:-.02em}
.histh .phico{color:var(--gold)}
.docbody{color:var(--ink-soft);font-size:var(--fs-sm);line-height:1.9;max-width:var(--w-text)}
.docbody p{margin:6px 0}.docbody b{color:var(--ink)}
.docbody ul,.docbody ol{margin:6px 0;padding-left:22px}
.docbody li{margin:var(--sp-1) 0}
.docbody code{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--gold-deep);
background:var(--fill-2);border:1px solid var(--line);border-radius:5px;padding:1px 6px}

/* 단계 */
.steps{list-style:none;counter-reset:s;margin:var(--sp-2) 0 0;padding:0;max-width:var(--w-text)}
.steps li{counter-increment:s;position:relative;padding:0 0 var(--sp-4) 44px;margin:0}
.steps li::before{content:counter(s,decimal-leading-zero);position:absolute;left:0;top:2px;
width:28px;height:28px;display:grid;place-items:center;border-radius:var(--r1);
background:var(--gold-a10);color:var(--gold-deep);font-family:var(--font-mono);
font-size:var(--fs-2xs);font-weight:700}
.steps li::after{content:"";position:absolute;left:13px;top:34px;bottom:4px;width:1px;background:var(--line)}
.steps li:last-child{padding-bottom:0}.steps li:last-child::after{display:none}
.steps b{display:block;color:var(--ink);font-weight:800;letter-spacing:-.015em}
.steps p{margin:2px 0 0;color:var(--ink-soft)}

/* 사양 */
.spec{margin:var(--sp-2) 0 0;display:grid;grid-template-columns:minmax(112px,190px) 1fr;
max-width:var(--w-text)}
.spec dt{padding:11px 0;border-top:1px solid var(--line2);color:var(--mut);font-size:var(--fs-sm);
font-weight:var(--fw-2)}
.spec dd{padding:11px 0;border-top:1px solid var(--line2);margin:0;color:var(--ink-soft);
font-size:var(--fs-sm);line-height:1.8}
.spec dt:first-of-type,.spec dd:first-of-type{border-top:0}

/* 지표 */
.statstrip{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
gap:1px;background:var(--line);border:1px solid var(--line);border-radius:var(--r3);
overflow:hidden;box-shadow:var(--elev-2);margin:var(--sp-4) 0}
.st{background:var(--surface-1);padding:16px 18px}
.st .l{color:var(--mut);font-size:var(--fs-2xs);font-weight:var(--fw-2);letter-spacing:.06em;
margin-bottom:5px}
.st .v{font-size:var(--fs-2xl);font-weight:800;letter-spacing:-.03em;color:var(--ink);
font-variant-numeric:tabular-nums;line-height:1.15}
.st .v.ts{font-size:var(--fs-lg)}
.st .v.text{font-size:var(--fs-lg);letter-spacing:-.02em;overflow-wrap:anywhere}
.st .v small{display:block;margin-top:2px;font-size:var(--fs-xs);font-weight:600;color:var(--mut)}

/* 표 */
.tablewrap{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r3);
overflow-x:auto;box-shadow:var(--elev-3);max-width:760px}
table{width:100%;border-collapse:collapse;font-size:var(--fs-sm);min-width:520px}
thead th{background:var(--surface-3);color:var(--mut);font-weight:600;text-align:left;
padding:var(--sp-3) var(--sp-4);border-bottom:1px solid var(--line);white-space:nowrap;
font-size:var(--fs-xs);letter-spacing:.06em}
thead th.r{text-align:right}
tbody td{padding:var(--sp-3) var(--sp-4);border-bottom:1px solid var(--line2);white-space:nowrap;
color:var(--ink-soft)}
tbody tr:last-child td{border-bottom:0}
tbody td.r{text-align:right;font-variant-numeric:tabular-nums}
th.r,td.r{width:104px}
thead th:nth-child(2),tbody td:nth-child(2){width:150px}
.nm{font-weight:700;color:var(--ink)}
.cd{color:var(--faint);font-size:var(--fs-2xs);font-family:var(--font-mono);margin-top:1px}
.pos{color:var(--pos)}.neg{color:var(--neg)}
.bar{position:relative;width:118px;height:7px;border-radius:4px;background:var(--fill-2)}
.bar::before{content:"";position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--gold-a25)}
.bar i{position:absolute;top:0;height:7px;border-radius:4px}

/* 차트 */
.chartcard{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r3);
padding:var(--sp-3);box-shadow:var(--elev-2);overflow:hidden}
.chartcard img{display:block;width:100%;height:auto;border-radius:10px}
.empty{padding:34px var(--sp-5);text-align:center;color:var(--mut);font-size:var(--fs-sm)}

/* 리서치 */
.tag{display:inline-flex;align-items:center;height:var(--chip-h);padding:0 var(--sp-2);
border-radius:var(--r1);font-size:var(--fs-2xs);font-weight:800;letter-spacing:.04em}
.tag-add{background:rgba(184,50,58,.10);color:var(--pos)}
.tag-watch{background:var(--gold-a10);color:var(--gold-tint)}
.tag-remove{background:rgba(47,102,192,.10);color:var(--neg)}
.rc{position:relative;background:var(--surface-2);border:1px solid var(--line);
border-radius:var(--r3);padding:var(--sp-4);box-shadow:var(--elev-2);overflow:hidden}
.rc+.rc{margin-top:var(--sp-2)}
.rc::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;
background:linear-gradient(180deg,var(--gold),transparent)}
.rt{display:flex;gap:var(--sp-2);align-items:center;flex-wrap:wrap}
.rt b{font-size:var(--fs-md);color:var(--ink);letter-spacing:-.015em}
.rt code{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--faint)}
.sc{margin-left:auto;display:flex;gap:var(--sp-3);color:var(--mut);font-size:var(--fs-xs);
font-variant-numeric:tabular-nums;white-space:nowrap}
.rz{color:var(--ink-soft);font-size:var(--fs-sm);margin:var(--sp-2) 0 0;line-height:1.7}
.body-text{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;color:var(--ink-soft);
font-size:var(--fs-sm);line-height:1.9}
.brief{background:var(--surface-1);border:1px solid var(--line);border-left:2px solid var(--gold);
border-radius:var(--r3);padding:var(--sp-5);box-shadow:var(--elev-2)}
.research-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 var(--sp-5)}
.research-meta div{display:flex;align-items:baseline;justify-content:space-between;gap:var(--sp-3);
padding:10px 0;border-bottom:1px solid var(--line2)}
.research-meta div:nth-last-child(-n+2){border-bottom:0}
.research-meta dt{color:var(--mut);font-size:var(--fs-xs);font-weight:var(--fw-2);white-space:nowrap}
.research-meta dd{margin:0;color:var(--ink);font-size:var(--fs-sm);font-weight:var(--fw-3);
text-align:right;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.checks{margin:0;padding:0;list-style:none}
.checks li{position:relative;padding:0 0 10px 20px;color:var(--ink-soft);font-size:var(--fs-sm);
line-height:1.8}
.checks li:last-child{padding-bottom:0}
.checks li::before{content:"—";position:absolute;left:0;color:var(--gold);font-family:var(--font-mono)}

/* 꼬리말 */
.foot{color:var(--faint);font-size:var(--fs-xs);text-align:center;padding:var(--sp-6) 0 var(--sp-7);
line-height:2}
.sitefoot{border-top:1px solid var(--line);padding:20px 0 36px}
.sf-mast{display:grid;gap:var(--sp-3);padding-bottom:var(--sp-4);margin-bottom:14px;
border-bottom:1px solid var(--line2)}
.sf-brand{color:var(--mut);font-weight:var(--fw-3);font-size:var(--fs-xs)}
.sf-cred{display:grid;grid-template-columns:max-content 1fr;gap:6px var(--sp-4);margin:0;
color:var(--faint);font-size:var(--fs-2xs);line-height:1.55}
.sf-cred dt{color:var(--mut);font-weight:var(--fw-2);white-space:nowrap}
.sf-cred dd{margin:0}
.sf-cred code{font-family:var(--font-mono);color:var(--mut)}
.sfin{display:flex;flex-wrap:wrap;gap:var(--sp-2) var(--sp-5);align-items:center;
justify-content:space-between;color:var(--faint);font-size:var(--fs-xs)}
.sf-links{display:flex;flex-wrap:wrap;gap:var(--sp-1) 18px}
.sf-links a{color:var(--mut);text-decoration:none;transition:color var(--dur-1) var(--ease)}

@media(hover:hover) and (pointer:fine){
.links a:hover{color:var(--ink);background:var(--fill-1)}
.pcard:hover,.rc:hover{transform:translateY(-2px);border-color:var(--gold-a25)}
.sf-links a:hover{color:var(--gold)}}

@media(max-width:760px){
.wrap{padding:0 var(--sp-4)}
.navin{gap:var(--sp-2);padding-left:var(--sp-4)}
.brand-t small{display:none}
.links a{padding:var(--sp-2) 10px}
.page{padding-top:var(--sp-6)}
.histbox{margin-top:var(--sp-6)}
.asof-x{margin-left:0;flex-basis:100%}
tbody td,thead th{padding:var(--sp-3)}}
@media(max-width:520px){
.navin{height:auto;flex-wrap:wrap;padding-top:10px;padding-bottom:8px;border-radius:24px}
.brand{flex-shrink:0;white-space:nowrap}
.links{flex:1 1 100%;min-width:0;margin-left:0;gap:0;justify-content:space-between}
.links a{padding:8px 6px;font-size:12px}
.spec{grid-template-columns:1fr}
.spec dd{padding-top:0;border-top:0}
.research-meta{grid-template-columns:1fr}
.research-meta div:nth-last-child(2){border-bottom:1px solid var(--line2)}
.sf-cred{grid-template-columns:1fr;gap:0}
.sf-cred dt{margin-top:10px}
.sc{margin-left:0;flex-basis:100%}
.st .v{font-size:var(--fs-xl)}}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>"""


# ── 뼈대 ────────────────────────────────────────────────────────────────────

_NAV_LINKS = (
    ("/", "시장"),
    ("/search", "뉴스 검색"),
    ("/forecast", "예측 컨센서스"),
    ("/research", "리서치"),
    ("/portfolio", "내 자산"),
    ("/about", "정보"),
)

_MARK = (
    "<svg class='navmark' width='34' height='34' viewBox='0 0 34 34' fill='none'"
    " aria-hidden='true'>"
    "<rect x='1.6' y='1.6' width='30.8' height='30.8' rx='9.5'"
    " fill='currentColor' fill-opacity='.09' stroke='currentColor' stroke-opacity='.4'/>"
    "<path d='M8 22.5l5-6 4 3 4.5-7.5' stroke='currentColor' stroke-width='2'"
    " stroke-linecap='round' stroke-linejoin='round'/>"
    "<circle cx='24.4' cy='11.2' r='2.5' fill='currentColor'/></svg>"
)


def _head(title: str) -> str:
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light'>"
        "<meta name='robots' content='noindex'>"
        "<title>" + title + " · " + SITE_BRAND_KO + "</title>" + _STYLE + "</head><body>"
        "<a class='skip' href='#main'>본문 바로가기</a>"
        "<div class='bg'></div><div class='bg-aurora'></div>"
        "<div class='bg-grid'></div><div class='bg-grain'></div>"
    )


def _header(active: str) -> str:
    nav = "".join(
        "<a href='" + href + "'" + (" aria-current='page'" if href == active else "") + ">"
        + label + "</a>"
        for href, label in _NAV_LINKS
    )
    return (
        "<nav class='topnav'><div class='wrap navin'>"
        "<a class='brand' href='/'>" + _MARK
        + "<span class='brand-t'><span>" + SITE_BRAND_KO + "<b>.live</b></span>"
        "<small>" + SITE_TAGLINE + "</small></span></a>"
        "<div class='links'>" + nav + "</div>"
        "</div></nav>"
    )


# 발행 정보 띠. 이 사이트에서 가장 자주 틀리는 오해가 "지금 값"이라는 것이라,
# 기준 시각과 "실시간 아님"을 화면마다 같은 자리에 둔다.
_ASOF = (
    "<div class='asof' role='note'><div class='wrap asofin'>"
    "<span class='mh-id'>" + SITE_BRAND_KO + " · 뉴스 주기마다 갱신</span>"
    "<span class='mh-date' id='asof-date'>산출물 시각을 확인하는 중…</span>"
    "<span class='pubstat' id='asof-stat' hidden><i class='pubdot'></i><span id='asof-stat-t'></span></span>"
    "<span class='asof-x'>실시간이 아니라 마지막으로 계산된 산출물입니다</span>"
    "</div></div>"
)


_SITE_FOOT = (
    "<footer class='sitefoot'><div class='wrap'>"
    "<div class='sf-mast'>"
    "<span class='sf-brand'>" + SITE_BRAND_KO + " · " + SITE_HOST + "</span>"
    "<dl class='sf-cred'>"
    "<dt>다루는 시장</dt><dd>중국 본토 · 홍콩 · 미국 · 한국 · 일본</dd>"
    "<dt>값의 성격</dt><dd>뉴스 보도의 논조를 집계한 관측치입니다. 시세·수익률·"
    "매매 신호가 아니며, 원시 가격 데이터는 제공하지 않습니다.</dd>"
    "<dt>시각 기준</dt><dd>모든 날짜와 시각은 한국 시간입니다. 해외 뉴스의 시각도 "
    "수집할 때 한국 시간으로 바꿉니다.</dd>"
    "<dt>갱신</dt><dd>뉴스 주기마다 갱신하며, 화면의 값은 마지막 계산 시점에 고정됩니다.</dd>"
    "<dt>원본 자료</dt><dd><code>/api/market</code> · <code>/api/research</code> · "
    "<code>/api/meta</code> · <code>/market_chart.png</code> — 읽기만 할 수 있고 바꾸는 기능은 없습니다. 비밀번호로 잠긴 개인 화면(내 자산)은 공개 자료에 섞이지 않습니다.</dd>"
    "</dl></div>"
    "<div class='sfin'><span class='sf-links'>"
    "<a href='/'>시장</a><a href='/search'>뉴스 검색</a><a href='/forecast'>예측 컨센서스</a>"
    "<a href='/research'>리서치</a><a href='/about'>정보</a>"
    "</span><span>정보 제공 목적이며 투자 권유가 아닙니다.</span></div>"
    "</div></footer></body></html>"
)


def page(title: str, active: str, main: str, script: str = "") -> str:
    return (
        _head(title)
        + _header(active)
        + _ASOF
        + "<main id='main' tabindex='-1' class='wrap page'>" + main + DISCLAIMER + "</main>"
        + script
        + _SITE_FOOT
    )


def h1(glyph: str, text: str) -> str:
    return "<h1 class='ph'><span class='phico'>" + icon(glyph, 24) + "</span>" + text + "</h1>"


def sec(glyph: str, title: str, body: str) -> str:
    return (
        "<div class='histbox'><div class='histh'><span class='phico'>" + icon(glyph)
        + "</span>" + title + "</div><div class='docbody'>" + body + "</div></div>"
    )


# 모든 화면 본문 끝에 한 번 붙는 꼬리말. 예전에는 면책(foot, 화면마다 직접 붙임)과
# 기준 시각 안내(asof-note, 본문 밖)가 따로 있어 같은 말을 두 번 했고, 면책이 빠진
# 화면(검색·자산)도 있었다. 뼈대가 한 곳에서 붙인다 — 화면 파일은 붙이지 않는다.
DISCLAIMER = (
    "<div class='foot'>정보 제공 목적이며 투자 권유가 아닙니다. 모든 수치는 실시간이 "
    "아니라 마지막으로 계산한 값이므로, 인용하기 전에 화면 위쪽의 기준 시각을 확인해 "
    "주세요. 투자 판단과 책임은 이용자 본인에게 있습니다.</div>"
)


# ── 공통 스크립트 ────────────────────────────────────────────────────────────
# 값은 산출물에서 오지만 문자열은 모두 텍스트 노드로 넣거나 escape한 뒤 붙인다.
JS_UTIL = """
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>(
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp=v=>{if(!v)return '';const t=String(v).replace('T',' ');
  return t.length>16?t.slice(0,16):t;};
// 지표 칸은 폭이 좁다. 날짜와 시각을 한 줄에 두면 줄바꿈으로 칸 높이가 튄다.
const stampHtml=v=>{const t=stamp(v);if(!t)return '–';const i=t.indexOf(' ');
  return i<0?esc(t):esc(t.slice(0,i))+'<small>'+esc(t.slice(i+1))+'</small>';};
const pct=v=>Math.round((Number(v)||0)*100)+'%';
// 발행 정보 띠는 화면마다 같으므로 한 곳에서 채운다.
fetch('/api/meta').then(r=>r.json()).then(d=>{
  const parts=[];
  if(d.news_generated_at)parts.push('뉴스 자료 '+stamp(d.news_generated_at));
  if(d.market_generated_at)parts.push('시장 집계 '+stamp(d.market_generated_at));
  if(d.research_generated_at)parts.push('리서치 '+stamp(d.research_generated_at));
  const date=document.getElementById('asof-date');
  const stat=document.getElementById('asof-stat');
  const statT=document.getElementById('asof-stat-t');
  if(parts.length){
    date.textContent=parts.join(' · ')+' (한국 시간)';
    statT.textContent='산출물 있음';
  }else{
    date.textContent='아직 산출물이 없습니다';
    stat.className='pubstat none';
    statT.textContent='대기 중';
  }
  stat.hidden=false;
}).catch(()=>{
  document.getElementById('asof-date').textContent='산출물 시각을 읽지 못했습니다';
});
"""
