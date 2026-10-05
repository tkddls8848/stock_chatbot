"""공개 웹 화면의 공통 뼈대 — 스타일, 헤더·푸터, `page()` 조립, 공통 스크립트.

여기에는 정적 문자열만 둔다. 페이지는 프로세스가 뜰 때 한 번 조립되고 요청마다
다시 만들지 않으며, 수치는 브라우저가 `/api/*`를 읽어 채운다. 외부 폰트·CDN을
쓰지 않는 것도 같은 이유다 - 공개 웹은 자기 프로세스 밖에서 아무것도 부르지
않는다. 로고와 아이콘은 파일이 아니라 인라인 SVG다.

화면은 **리서치 리포트 지면**이다(2026-09-29 운영자 결정, 시안 A 터미널·B 리포트·C 핀테크 중 B).
흰 종이에 먹색 글자, 명조 제호·표제, 겹괘선 마스트헤드, 따뜻한 회색 괘선. 카드·그림자·
둥근 모서리 대신 괘선으로 구획하고, **라이트 전용**이다(`color-scheme:light`). 글꼴 두 벌
(Pretendard·Noto Serif KR, 둘 다 OFL)은 KS X 1001 한글 2,350자로 줄여 `static/fonts/`에 두고
이 프로세스가 직접 내려준다 — 외부 CDN은 여전히 부르지 않는다. 감성의 부호는 **빨강이 긍정, 파랑이 부정**이다 - 한국 시장 화면의
관례이고, 이 사이트를 읽는 사람이 다른 화면에서 종일 보는 방향이다.
"""

from __future__ import annotations

# 화면에 보이는 사이트 이름은 영문 소문자 "nunchi"다(2026-09-29 운영자 결정 — "눈치"로 쓰지 않는다).
SITE_NAME = "nunchi"
SITE_BRAND = SITE_NAME
SITE_TAGLINE = "시장정보 · 리서치 · 자산관리"
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
@font-face{font-family:'nunchi sans';src:url('/fonts/pretendard-sub.woff2') format('woff2');
font-weight:400 800;font-display:swap}
@font-face{font-family:'nunchi serif';src:url('/fonts/noto-serif-kr-bold-sub.woff2') format('woff2');
font-weight:700;font-display:swap}
:root{
color-scheme:light;

--fs-2xs:12px;--fs-xs:12.5px;--fs-sm:14px;--fs-md:16px;--fs-lg:19px;--fs-xl:26px;--fs-2xl:32px;
--sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sp-5:24px;--sp-6:32px;--sp-7:48px;--sp-8:64px;
--w-wide:1180px;--w-text:780px;
--fw-1:400;--fw-2:600;--fw-3:700;--fw-4:800;

/* 지면: 흰 종이, 먹색 글자, 따뜻한 회색 괘선. 구조색은 짙은 남색 하나다. */
--bg:#ffffff;--ink:#16181d;--ink-soft:#3d4149;--mut:#5b5750;--faint:#625e57;
--line:#d6d0c4;--line2:#e7e2d9;--rule:#16181d;
--fill-1:#f6f4ef;--fill-2:#ebe7df;
--surface-1:#ffffff;--surface-2:#ffffff;--surface-3:#f8f7f3;

--gold:#1b2f4b;--gold-deep:#13233a;--gold-ink:#ffffff;--gold-tint:#34445c;
--gold-a05:#f6f7f9;--gold-a10:#eceff3;
--gold-a25:#c5ccd6;--gold-a40:#8e9aab;

--acc:#1f4fa0;--acc-tint:#1b4588;--acc-a10:rgba(31,79,160,.10);--acc-a40:rgba(31,79,160,.40);

/* 한국 시장 관례: 빨강이 오름·호재, 파랑이 내림·악재. 흰 바탕에서 모두 4.5:1을 넘긴다. */
--pos:#a3202c;--neg:#1d4a9c;--ok:#146b48;--warnc:#8f4511;

--ease:cubic-bezier(.2,0,0,1);--dur-1:100ms;--dur-3:250ms;--dur-4:400ms;
--elev-2:none;--elev-3:none;
--r1:0;--r2:0;--r3:0;--r-pill:0;--ctl:40px;--chip-h:24px;--nav-h:64px;

/* 글꼴은 저장소의 서브셋 파일을 이 서버가 내려준다(KS X 1001 한글 2,350자·라틴·기호).
   목록 밖 글자는 시스템 글꼴로 이어 그린다. */
--font-sans:'nunchi sans','Pretendard Variable',Pretendard,'Apple SD Gothic Neo','Malgun Gothic',
  system-ui,-apple-system,sans-serif;
--font-serif:'nunchi serif','Noto Serif KR','Nanum Myeongjo','AppleMyungjo',Batang,serif;
--font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace}
@media(any-pointer:coarse){:root{--ctl:44px}}
*{box-sizing:border-box}

body{margin:0;background:var(--bg);color:var(--ink);font-size:var(--fs-md);line-height:1.75;
font-family:var(--font-sans);text-rendering:optimizeLegibility;letter-spacing:-.01em;
font-variant-numeric:tabular-nums}
.ico{display:inline-block;vertical-align:-.18em;flex:none}
::selection{background:var(--gold-a25);color:var(--ink)}
a{color:inherit}
/* 키보드로 도는 사람이 지금 어디에 있는지 보이게 한다. 바깥으로 2px 띄워
   어두운 버튼 위에서도 테두리가 페이지 바탕과 맞닿아 대비를 잃지 않는다. */
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,
textarea:focus-visible,summary:focus-visible,dialog:focus-visible,[tabindex]:focus-visible{
outline:2px solid var(--acc);outline-offset:2px}

.wrap{max-width:var(--w-wide);margin:0 auto;padding:0 var(--sp-6)}
.skip{position:absolute;left:-9999px;top:0;z-index:100;background:var(--gold);color:var(--gold-ink);
padding:10px var(--sp-4);font-weight:var(--fw-3);text-decoration:none}
.skip:focus{left:0}
/* 눈에는 보이지 않고 화면 낭독기만 읽는 글. 표 머리처럼 이름이 꼭 필요한데
   화면에는 둘 자리가 없는 곳에만 쓴다. */
.sr{display:inline-block;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);
white-space:nowrap}
main#main:focus{outline:none}

/* 마스트헤드: 리서치 지면의 제호처럼. 명조 제호 + 오른쪽 메뉴, 아래는 겹괘선.
   공개 정보(시장·검색·컨센서스)와 개인 작업(리서치·자산)은 세로 괘선으로 가른다. */
.topnav{background:var(--bg)}
/* 괘선이 본문 폭과 같은 자리에서 끝나도록 여백 대신 폭을 줄인다(.wrap의 안쪽 여백까지 긋지 않는다). */
.navin.wrap,.asofin.wrap{width:calc(100% - 2*var(--sp-6));max-width:calc(var(--w-wide) - 2*var(--sp-6));
padding-left:0;padding-right:0}
.navin{display:flex;align-items:flex-end;gap:28px;padding-top:26px;padding-bottom:12px;
border-bottom:3px double var(--rule)}
.brand{display:flex;align-items:baseline;gap:12px;color:var(--ink);text-decoration:none;flex:none}
.brand-t{display:flex;align-items:baseline;gap:12px}
.brand-t>span{font-family:var(--font-serif);font-weight:700;font-size:32px;letter-spacing:-.03em;line-height:1}
.brand b{font-weight:700;color:var(--mut);font-size:20px;letter-spacing:-.02em}
.brand-t small{font-size:12.5px;color:var(--mut);font-weight:500}
.links{display:flex;gap:2px;margin-left:auto;align-items:baseline}
.links a{color:var(--ink-soft);text-decoration:none;padding:4px 10px;font-size:14.5px;font-weight:500;white-space:nowrap;
border-bottom:2px solid transparent}
.links a[aria-current]{color:var(--ink);font-weight:700;border-bottom-color:var(--ink)}
.links a[href='/research']{margin-left:12px;padding-left:22px;border-left:1px solid var(--line)}

/* 날짜 줄 — 이 사이트가 실시간이 아니라는 사실을 모든 화면의 같은 자리에 둔다. */
.asof{background:var(--bg);color:var(--mut);font-size:13px}
.asofin{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
padding-top:9px;padding-bottom:9px;border-bottom:1px solid var(--line2)}
.mh-id{color:var(--mut)}
.mh-date{color:var(--ink-soft);font-weight:600;font-variant-numeric:tabular-nums}
.asof-x{color:var(--mut);margin-left:auto}
.pubstat{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:700;color:var(--ok);order:-1}
.pubstat .pubdot{width:7px;height:7px;border-radius:50%;background:var(--ok);display:inline-block}
.pubstat.none{color:var(--warnc)}.pubstat.none .pubdot{background:var(--warnc)}

/* 본문 — 표제는 명조로 크게, 아이콘은 두지 않는다. 보고서 첫 장처럼 제목·리드문·본문. */
.page{padding-top:40px}
h1.ph{font-family:var(--font-serif);font-size:40px;font-weight:700;line-height:1.3;letter-spacing:-.035em;
margin:0 0 14px;max-width:880px}
.phico{display:none}
.kicker{margin:0 0 8px;color:var(--gold);font-weight:700;font-size:14px}
.disc{background:none;border:0;padding:0;color:var(--ink-soft);font-size:18.5px;line-height:1.7;
margin:0 0 var(--sp-5);max-width:var(--w-text)}
.disc b{color:var(--ink)}
.sub2{color:var(--mut);font-size:var(--fs-sm);margin:var(--sp-3) 0 var(--sp-4)}
.sub2 b{color:var(--ink)}

.pgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:var(--sp-3);
margin:var(--sp-4) 0 var(--sp-2)}
.pcard{position:relative;background:var(--surface-2);border:0;border-top:1px solid var(--rule);
border-radius:var(--r3);padding:var(--sp-4);box-shadow:var(--elev-2);overflow:hidden;
transition:transform var(--dur-3) var(--ease),border-color var(--dur-1) var(--ease)}
.pcard::after{content:'';position:absolute;left:0;right:0;top:0;height:2px;opacity:.8}
.pc-gold::after{background:var(--gold)}
.pc-acc::after{background:var(--acc)}
.pc-ok::after{background:var(--ok)}
.pc-warn::after{background:var(--warnc)}
.pcard-t{font-weight:800;color:var(--gold-tint);font-size:var(--fs-md);margin-bottom:6px;
letter-spacing:-.01em}
.pcard-s{color:var(--mut);font-size:var(--fs-sm);line-height:1.7}

/* 섹션 머리는 지면의 소제목처럼 — 먹색 괘선 위에 명조 제목. */
.histbox{margin-top:44px}
.histh{display:flex;align-items:center;gap:var(--sp-2);font-family:var(--font-serif);font-weight:700;font-size:21px;
margin-bottom:var(--sp-3);letter-spacing:-.03em;padding-top:10px;border-top:1px solid var(--rule)}
.docbody{color:var(--ink-soft);font-size:var(--fs-md);line-height:1.85;max-width:var(--w-text)}
.docbody p{margin:6px 0}.docbody b{color:var(--ink)}
.docbody ul,.docbody ol{margin:6px 0;padding-left:22px}
.docbody li{margin:var(--sp-1) 0}
/* 주소·코드 줄은 띄어쓰기가 없어 좁은 화면에서 칸 밖으로 그대로 뻗는다. */
.docbody code{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--gold-deep);
background:var(--fill-2);border:1px solid var(--line);border-radius:5px;padding:1px 6px;
overflow-wrap:anywhere}

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
/* 지표 띠는 보고서의 핵심 수치 줄처럼 — 위아래 괘선 사이에 칸을 세로 괘선으로 가른다. */
.statstrip{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
gap:0;border-top:1px solid var(--rule);border-bottom:1px solid var(--line);margin:var(--sp-5) 0}
.st{background:var(--surface-1);padding:14px 18px 16px;border-left:1px solid var(--line2)}
.st:first-child{border-left:0;padding-left:0}
.st .l{color:var(--mut);font-size:12px;font-weight:var(--fw-2);letter-spacing:0;
margin-bottom:6px}
.st .v{font-size:30px;font-weight:700;letter-spacing:-.04em;color:var(--ink);
font-variant-numeric:tabular-nums;line-height:1.1}
.st .v.ts{font-size:var(--fs-lg)}
.st .v.text{font-size:var(--fs-lg);letter-spacing:-.02em;overflow-wrap:anywhere}
.st .v small{display:block;margin-top:2px;font-size:var(--fs-xs);font-weight:600;color:var(--mut)}

/* 표. 규칙을 `.tablewrap` 안으로 묶는다 — 고정 폭·nowrap은 국가별 수치 표
   하나를 위한 값인데, 맨 `table`·`thead th`·`tbody td`로 적혀 있던 동안
   개인 화면의 표까지 끌고 가 360px에서 페이지를 193px 밀어냈다. */
.tablewrap{background:var(--surface-2);border:0;border-top:1px solid var(--rule);
overflow-x:auto;max-width:100%}
.tablewrap table{width:100%;border-collapse:collapse;font-size:var(--fs-sm);min-width:520px}
.tablewrap thead th{background:none;color:var(--mut);font-weight:600;text-align:left;
padding:9px var(--sp-3);border-bottom:1px solid var(--rule);white-space:nowrap;
font-size:12.5px;letter-spacing:0}
.tablewrap thead th.r{text-align:right}
.tablewrap tbody td{padding:var(--sp-3);border-bottom:1px solid var(--line2);
white-space:nowrap;color:var(--ink-soft)}
.tablewrap tbody tr:last-child td{border-bottom:0}
.tablewrap tbody td.r{text-align:right;font-variant-numeric:tabular-nums;font-weight:600;color:var(--ink)}
.tablewrap th.r,.tablewrap td.r{width:104px}
.tablewrap thead th:nth-child(2),.tablewrap tbody td:nth-child(2){width:150px}
.nm{font-weight:700;color:var(--ink)}
.cd{color:var(--faint);font-size:var(--fs-2xs);font-weight:600;margin-top:1px}
.pos{color:var(--pos)}.neg{color:var(--neg)}
.bar{position:relative;width:118px;height:6px;border-radius:0;background:var(--fill-2)}
.bar::before{content:"";position:absolute;left:50%;top:-4px;bottom:-4px;width:1px;background:var(--mut)}
.bar i{position:absolute;top:0;height:6px;border-radius:0}

/* 차트 */
.chartcard{background:var(--surface-2);border:0;padding:0;overflow:hidden}
.chartcard img{display:block;width:100%;height:auto;border-radius:0}
.empty{padding:34px var(--sp-5);text-align:center;color:var(--mut);font-size:var(--fs-sm)}

/* 리서치 */
.tag{display:inline-flex;align-items:center;height:var(--chip-h);padding:0 var(--sp-2);
border-radius:var(--r1);font-size:var(--fs-2xs);font-weight:800;letter-spacing:.04em}
.tag-add{background:rgba(184,50,58,.10);color:var(--pos)}
.tag-watch{background:var(--gold-a10);color:var(--gold-tint)}
.tag-remove{background:rgba(47,102,192,.10);color:var(--neg)}
.rc{position:relative;background:var(--surface-2);border:0;border-bottom:1px solid var(--line2);
padding:var(--sp-4) 0;overflow:hidden}
.rt{display:flex;gap:var(--sp-2);align-items:center;flex-wrap:wrap}
.rt b{font-size:var(--fs-md);color:var(--ink);letter-spacing:-.015em;overflow-wrap:anywhere}
.rt code{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--faint);
overflow-wrap:anywhere}
.sc{margin-left:auto;display:flex;gap:var(--sp-3);color:var(--mut);font-size:var(--fs-xs);
font-variant-numeric:tabular-nums;white-space:nowrap}
.rz{color:var(--ink-soft);font-size:var(--fs-sm);margin:var(--sp-2) 0 0;line-height:1.7}
.body-text{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;color:var(--ink-soft);
font-size:var(--fs-sm);line-height:1.9}
.brief{background:var(--surface-1);border:0;border-top:1px solid var(--rule);padding:var(--sp-4) 0}
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
.foot{color:var(--faint);font-size:var(--fs-xs);padding:var(--sp-7) 0 var(--sp-5);line-height:1.9;max-width:var(--w-text)}
.sitefoot{padding:0 0 40px}
.sitefoot>.wrap>.sf-mast{border-top:3px double var(--rule);padding-top:18px}
.sf-mast{display:grid;gap:var(--sp-3);padding-bottom:var(--sp-4);margin-bottom:14px;
border-bottom:1px solid var(--line2)}
.sf-brand{color:var(--ink);font-family:var(--font-serif);font-weight:700;font-size:18px}
.sf-cred{display:grid;grid-template-columns:max-content 1fr;gap:6px var(--sp-4);margin:0;
color:var(--faint);font-size:var(--fs-2xs);line-height:1.55}
.sf-cred dt{color:var(--mut);font-weight:var(--fw-2);white-space:nowrap}
.sf-cred dd{margin:0}
.sf-cred code{font-family:var(--font-mono);color:var(--mut);overflow-wrap:anywhere}
.sfin{display:flex;flex-wrap:wrap;gap:var(--sp-2) var(--sp-5);align-items:center;
justify-content:space-between;color:var(--faint);font-size:var(--fs-xs)}
.sf-links{display:flex;flex-wrap:wrap;gap:var(--sp-1) 18px}
.sf-links a{color:var(--mut);text-decoration:none;transition:color var(--dur-1) var(--ease)}

@media(hover:hover) and (pointer:fine){
.links a:hover{color:var(--ink);border-bottom-color:var(--line)}
.tablewrap tbody tr:hover td{background:var(--fill-1)}
.sf-links a:hover{color:var(--gold)}}

@media(max-width:760px){
.wrap{padding:0 var(--sp-4)}
.navin.wrap,.asofin.wrap{width:calc(100% - 2*var(--sp-4))}
.navin{gap:var(--sp-2);padding-top:18px}
.brand-t small{display:none}
.links a{padding:var(--sp-2) 10px}
.page{padding-top:var(--sp-5)}
h1.ph{font-size:30px}
.disc{font-size:17px}
.histbox{margin-top:var(--sp-6)}
.asof-x{margin-left:0;flex-basis:100%}
.tablewrap tbody td,.tablewrap thead th{padding:var(--sp-3)}}
@media(max-width:520px){
.navin{height:auto;flex-wrap:wrap;padding-top:14px;padding-bottom:0;gap:12px}
.brand{flex-shrink:0;white-space:nowrap}
.links{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));flex:1 1 100%;min-width:0;margin-left:0;gap:0}.links a{text-align:center}
.links a[href='/research']{margin-left:0;padding-left:9px;border-left:0}
.links a{padding:12px 9px;font-size:12px}
.spec{grid-template-columns:1fr}
.spec dd{padding-top:0;border-top:0}
.research-meta{grid-template-columns:1fr}
.research-meta div:nth-last-child(2){border-bottom:1px solid var(--line2)}
.sf-cred{grid-template-columns:1fr;gap:0}
.sf-cred dt{margin-top:10px}
.sc{margin-left:0;flex-basis:100%}
.st .v{font-size:var(--fs-xl)}
.st:first-child{padding-left:18px}
h1.ph{font-size:27px}}

/* 개인 서비스 작업 공간 */
[hidden]{display:none!important}
.action{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:8px 16px;border:1px solid var(--gold-a40);
border-radius:var(--r1);background:#fff;color:var(--gold);text-decoration:none;font:inherit;font-size:13px;font-weight:600;cursor:pointer}
.action.primary{background:var(--gold);border-color:var(--gold);color:#fff}
.action:disabled{opacity:.5;cursor:wait}
.login-panel{max-width:720px;margin:24px 0 56px;padding:28px 0;border:0;border-top:1px solid var(--rule);border-bottom:1px solid var(--line);background:var(--surface-1)}
.login-panel h2{font-family:var(--font-serif);font-size:28px;margin:10px 0;font-weight:700;letter-spacing:-.03em}
.login-panel p{color:var(--mut);font-size:14px;margin-bottom:24px}
.eyebrow{font-size:12px;font-weight:700;color:var(--mut)}
.workspace-toolbar{display:flex;justify-content:space-between;gap:16px;align-items:center;border-bottom:1px solid var(--line);padding:16px 0;margin:24px 0}
.workspace-toolbar a{font-size:13px;margin-right:16px}
.research-form{display:flex;align-items:end;gap:12px;flex-wrap:wrap;padding:20px;background:var(--fill-1);border:0;border-top:1px solid var(--rule)}
.research-form label{display:flex;flex-direction:column;gap:6px;color:var(--mut);font-size:12px}
.research-form label:first-child{flex:1;min-width:180px}
.research-form textarea{min-height:76px;border:1px solid var(--line);border-radius:0;background:#fff;padding:8px;font:inherit;color:var(--ink);resize:vertical;width:100%}
.linklike{border:0;background:none;padding:0;color:var(--gold);text-decoration:underline;font:inherit;cursor:pointer}
.research-form input,.research-form select{height:40px;min-width:0;border:1px solid var(--line);border-radius:0;background:#fff;padding:8px;font:inherit;color:var(--ink)}
.evidence-section{margin-top:28px;border-top:2px solid var(--ink)}
.evidence-section h3{font-family:var(--font-serif);font-size:20px;margin:0;padding:16px 0;border-bottom:1px solid var(--line)}
.evidence-section h3 small{font-size:12px;font-weight:400;margin-left:12px;color:var(--mut)}
.news-row{padding:20px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
.news-meta{font-size:12px;color:var(--mut)}
.news-row h4{margin:6px 0;font-size:16px}.news-row p{font-size:14px;line-height:1.8;white-space:pre-wrap;margin:8px 0}
.news-row a{font-size:12px;color:var(--acc)}
.docbody h2{font-family:var(--font-serif);font-size:21px;margin-top:32px;padding-bottom:8px;border-bottom:1px solid var(--line)}
@media(max-width:760px){.navin{flex-wrap:wrap;gap:8px}.links{flex-basis:100%;margin-left:-10px;flex-wrap:wrap}.links a[href='/research']{margin-left:0;padding-left:10px;border-left:0}}
@media(max-width:520px){.login-panel{padding:24px 18px}.login-panel h2{font-size:22px}.workspace-toolbar{align-items:start;flex-direction:column}.research-form{padding:14px}.research-form label:first-child{flex-basis:100%}.research-form .action{flex:1}.asofin{gap:6px}.mh-date{font-size:11px}.mh-id{display:none}}

@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>"""


# ── 뼈대 ────────────────────────────────────────────────────────────────────

_NAV_LINKS = (
    ("/", "시장"),
    ("/search", "뉴스 검색"),
    ("/forecast", "예측 컨센서스"),
    ("/research", "내 리서치"),
    ("/portfolio", "내 자산"),
    ("/about", "정보"),
)



def _head(title: str, description: str, path: str) -> str:
    """제목·설명·주소를 한 번에 적는다.

    `description`과 Open Graph는 같은 한 문장을 쓴다 — 카카오톡·슬랙이 붙이는
    미리보기 카드와 검색 결과 요약이 서로 다른 말을 하면 안 된다. `og:image`는
    두지 않는다: 이 프로세스는 바깥에서 파일을 부르지 않고 이미지 파일도 없어,
    없는 주소를 적으면 미리보기가 깨진 그림 자리를 만든다.
    """
    full_title = title + " · " + SITE_BRAND
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light'>"
        "<meta name='robots' content='noindex'>"
        "<meta name='description' content='" + description + "'>"
        "<meta property='og:title' content='" + full_title + "'>"
        "<meta property='og:description' content='" + description + "'>"
        "<meta property='og:type' content='website'>"
        "<meta property='og:locale' content='ko_KR'>"
        "<meta property='og:site_name' content='" + SITE_BRAND + "'>"
        "<meta property='og:url' content='https://" + SITE_HOST + path + "'>"
        "<title>" + full_title + "</title>" + _STYLE + "</head><body>"
        "<a class='skip' href='#main'>본문 바로가기</a>"
    )


def _header(active: str) -> str:
    nav = "".join(
        "<a href='" + href + "'" + (" aria-current='page'" if href == active else "") + ">"
        + label + "</a>"
        for href, label in _NAV_LINKS
    )
    return (
        "<nav class='topnav'><div class='wrap navin'>"
        "<a class='brand' href='/'>"
        + "<span class='brand-t'><span>" + SITE_BRAND + "</span>"
        "<small>" + SITE_TAGLINE + "</small></span></a>"
        "<div class='links'>" + nav + "</div>"
        "</div></nav>"
    )


# 발행 정보 띠. 이 사이트에서 가장 자주 틀리는 오해가 "지금 값"이라는 것이라,
# 기준 시각과 "실시간 아님"을 화면마다 같은 자리에 둔다.
_ASOF = (
    "<div class='asof' role='note'><div class='wrap asofin'>"
    "<span class='mh-id'>뉴스 주기마다 갱신</span>"
    "<span class='mh-date' id='asof-date'>자료 시각 확인 중…</span>"
    "<span class='pubstat' id='asof-stat' hidden><i class='pubdot'></i><span id='asof-stat-t'></span></span>"
    "<span class='asof-x'>마지막 집계 기준 · 실시간 시세 아님</span>"
    "</div></div>"
)


_SITE_FOOT = (
    "<footer class='sitefoot'><div class='wrap'>"
    "<div class='sf-mast'>"
    "<span class='sf-brand'>" + SITE_BRAND + "</span>"
    "<dl class='sf-cred'>"
    "<dt>다루는 시장</dt><dd>중국 본토 · 홍콩 · 미국 · 한국 · 일본 · 유럽</dd>"
    "<dt>값의 성격</dt><dd>뉴스 보도의 논조를 집계한 관측치입니다. 시세·수익률·"
    "매매 신호가 아니며, 원시 가격 데이터는 제공하지 않습니다.</dd>"
    "<dt>시각 기준</dt><dd>모든 날짜와 시각은 한국 시간입니다. 해외 뉴스의 시각도 "
    "수집할 때 한국 시간으로 바꿉니다.</dd>"
    "<dt>갱신</dt><dd>뉴스 주기마다 갱신하며, 화면의 값은 마지막 계산 시점에 고정됩니다.</dd>"
    "<dt>개인 서비스</dt><dd>내 리서치와 내 자산은 Google 로그인 후 계정별로 이용합니다.</dd>"
    "</dl></div>"
    "<div class='sfin'><span class='sf-links'>"
    "<a href='/'>시장</a><a href='/search'>뉴스 검색</a><a href='/forecast'>예측 컨센서스</a>"
    "<a href='/research'>리서치</a><a href='/about'>정보</a>"
    # 이용 조건은 상단 메뉴에 두지 않는다 — 매번 읽는 화면이 아니라 필요할 때
    # 찾는 화면이라, 모든 화면의 꼬리말에서만 닿게 한다.
    "<a href='/terms'>이용 조건</a> · <a href='/privacy'>개인정보 처리방침</a> · <a href='/portfolio'>Google 로그인 · 내 계정</a>"
    "</span><span>정보 제공 목적이며 투자 권유가 아닙니다.</span></div>"
    "</div></footer></body></html>"
)


def page(title: str, active: str, main: str, script: str = "", *, description: str) -> str:
    return (
        _head(title, description, active)
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
  const date=document.getElementById('asof-date');
  const stat=document.getElementById('asof-stat');
  const statT=document.getElementById('asof-stat-t');
  if(parts.length){
    date.textContent=parts.join(' · ')+' (한국 시간)';
    statT.textContent='자료 제공 중';
  }else{
    date.textContent='아직 제공할 자료가 없습니다';
    stat.className='pubstat none';
    statT.textContent='대기 중';
  }
  stat.hidden=false;
}).catch(()=>{
  document.getElementById('asof-date').textContent='자료 시각을 읽지 못했습니다';
});
"""
