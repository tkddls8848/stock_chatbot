# 서버 운영 (AWS Lightsail)

봇이 서버에서 도는 동안 반복해서 하는 일을 모은 **실행 문서**다. 할 일 목록이
아니라 절차서이므로 항목이 끝나도 지우지 않는다.

**다른 문서와의 경계.** 공유 호스트와의 경계·계약 값과 앱 런타임 설치는
`infra/host-contract.md`가 유일한 문서다 — 여기에 옮겨 적지 않는다.
이 문서는 그 뒤, **이미 떠 있는 서버를 상대로** 하는 일만 다룬다.

| 문서 | 다루는 것 |
|---|---|
| `infra/host-contract.md` | 호스트 소유 경계, 계약 값(리전·타임존·스냅샷·공개 웹), 앱 설치 |
| **이 문서** | 접속, 상태 확인, 배포 갱신, 설정 변경, 실측, 판정, 백업·복구, 장애 대응 |
| `services/telegram_bot/docs/actor-potus.md` | `market_actor`·`potus_feed` 계획 (앞으로 만들 것) |

---

## 0. 운영 리전

운영 서버는 도쿄의 공유 `medium_3_0` 인스턴스 `orca-host-tokyo`
(`ap-northeast-1a`, 고정 IP `16.76.30.47`)다. 앱은 전용 `stockbot` 계정의
`/srv/stock-chatbot`에서 실행한다.

**인프라 비용 때문에 인스턴스를 공유할 뿐 별개 프로젝트다.** 호스트 자체
(인스턴스·고정 IP·공인 방화벽·스냅샷·Orca·Tailscale·OS 계정)는 별개 저장소
`C:/Users/PSI/orca/remote_coding/remote-lightsail`이 소유하고, 앱 운영은 전부
이 저장소가 소유한다. 호스트 저장소는 우리 경로·포트·권한을 알지 못하므로
앱 점검은 `infra/scripts/verify-app.sh`로 우리가 직접 한다. 편입 경위와 롤백
기준은 `infra/merge-plan.md`에 있다.

**이 저장소는 AWS 자원을 만들지 않는다.** 2026-09-12 이전 독립 인스턴스를 만들던
`infra/terraform/`은 삭제했고(정의는 git 이력에 있다), 그 인스턴스·고정 IP·키페어도
그날 폐기했다. 호스트 값이 필요하면 호스트 저장소에서 바꾼다 — 계약 값은
`infra/host-contract.md`, 롤백(스냅샷 `stock-chatbot-pre-merge-20260912`로 새 인스턴스)
기준은 `infra/merge-plan.md`다.

## 1. 접속

개발 작업은 Tailscale의 `orca` 별칭, 설치·복구 작업은 `ubuntu` 관리자 키를 쓴다.

```powershell
ssh orca
ssh -i $env:USERPROFILE\.ssh\orca-lightsail-tokyo ubuntu@16.76.30.47
ssh -L 8787:127.0.0.1:8787 -i $env:USERPROFILE\.ssh\orca-lightsail-tokyo ubuntu@16.76.30.47
```

관리 터널을 연 뒤 `http://127.0.0.1:8787`로 접속한다. 기본 공인 방화벽은 관리자
IP `/32`의 22만 허용한다. 6768과 8787/8788은 공개하지 않는다. `nunchi.live`가
NXDOMAIN인 동안 Caddy와 80/443도 꺼 둔다. DNS와 Caddy를 복구할 때만 공유 인프라의
`enable_public_web=true`를 적용한다.

```powershell
terraform -chdir=C:\Users\PSI\orca\remote_coding\remote-lightsail\terraform plan
aws lightsail get-instance-port-states --region ap-northeast-1 --instance-name orca-host-tokyo
```

관리 웹 비밀번호는 부트스트랩이 무작위로 만들어 서버 `.env`에 넣었다.

```bash
sudo grep WEB_ADMIN_PASSWORD /srv/stock-chatbot/.env
```

## 2. 상태 확인

핵심 유닛은 봇(`stock-chatbot`), 읽기 웹(`stock-chatbot-web`), Polymarket timer다.
Caddy는 DNS를 복구해 공개 웹을 다시 열 때만 사용한다. 봇과 웹은 서로 독립이라
봇을 재기동해도 웹은 마지막 산출물을 계속 보여 준다.

```bash
systemctl status stock-chatbot --no-pager
systemctl is-enabled stock-chatbot          # enabled 여야 재부팅 후 자동 기동된다
systemctl is-active stock-chatbot-web stock-chatbot-polymarket-refresh.timer
sudo -u stockbot git -C /srv/stock-chatbot log -1 --oneline
journalctl -u stock-chatbot -n 50 --no-pager
```

기동 로그에 `봇 시작됨. 활성 기능: ...`이 뜨고 목록이 기대와 같아야 한다.

`is-enabled`가 `disabled`면 **재부팅 후 봇이 올라오지 않는다.** 부트스트랩은
유닛 설치만 하고 `enable`은 전환 명령(`terraform output cutover_commands`)이
`systemctl enable --now`로 한다 — `start`만 했다면 지금 켠다.

```bash
sudo systemctl enable stock-chatbot
```

자주 보는 로그 축:

```bash
journalctl -u stock-chatbot | grep PREFILTER | tail -20   # 사전선별 CPU·보정
journalctl -u stock-chatbot | grep -i error | tail -20
journalctl -u stock-chatbot -f                            # 실시간
```

## 3. 코드 갱신 (배포)

서버는 clone한 `main`을 pull한다. **작업 브랜치에만 커밋해 두면 서버는 옛 코드를
받는다** — 먼저 `main`에 병합하고 push한다.

```bash
sudo -u stockbot git -C /srv/stock-chatbot log -1 --oneline
sudo -u stockbot git -C /srv/stock-chatbot pull
sudo -u stockbot /srv/stock-chatbot/venv/bin/pip install -r /srv/stock-chatbot/requirements.txt
sudo systemctl restart stock-chatbot
journalctl -u stock-chatbot -f
```

**디렉터리 구조나 실행 모듈이 바뀐 커밋을 받을 때는 pull만으로 부족하다.**
`/etc/systemd/system/`의 유닛이 옛 경로를 가리킨 채 남아 재기동이 실패한다.
`sudo /srv/stock-chatbot/infra/scripts/install-shared-host.sh`로 유닛을 다시 설치하고
`sudo systemctl daemon-reload` 뒤에 재기동한다.

**`.env`를 함께 고쳐야 하는 변경이면 `.env`를 먼저 고치고 마지막에 한 번만
재기동한다.** pull부터 하고 재기동하면 새 코드가 옛 설정을 거부해 봇이 뜨지
않는 경우가 있다(대표적으로 `ALLOWED_CHAT_IDS`, 5절).

같은 토큰으로 두 프로세스가 폴링하면 텔레그램이 `Conflict: terminated by other
getUpdates request`를 돌려주고 **양쪽이 번갈아 죽는다.** 로컬 봇을 켜 둔 채로
서버를 재기동하지 않는다.

## 4. 재기동을 피할 시간대

봇이 꺼져 있는 동안 지나간 스케줄은 **따라잡지 않는다.** APScheduler jobstore가
메모리라 놓친 실행은 그냥 사라진다. 아래 창을 피해서 재기동한다.

| JST | 무엇 | 놓치면 |
|---|---|---|
| **08:35 ~ 10:35** | Polymarket 스냅숏(08:35, 재시도 09:35·10:35) | 그날 스냅숏이 통째로 빈다. 가동률 게이트(7일 중 6일)에서 하루를 깎는다 |
| **07:00** | 야간 다이제스트 발송 | 큐가 다음 주간 주기로 넘어간다(첫 주간 주기가 큐를 확인하므로 유실은 아니다) |
| 매시 정각 부근 | 뉴스 주기(60분) | 그 주기 한 번을 건너뛴다. 다음 주기가 같은 후보를 다시 본다 |

가장 안전한 창은 **11:00 ~ 23:00 사이의 정각 직후**다. 재부팅도 같은 기준으로
잡는다 — `data/`는 영구 루트 디스크에 있어 재부팅으로 사라지지 않지만,
스냅숏 창을 덮으면 그날 하루치를 잃는다.

## 5. 설정 변경

**`.env`는 비밀값·자격증명만 갖는다**(토큰, 비밀번호, 프록시 URL, chat id).
그 외 모든 설정 — 기능 켜기/끄기, 수량·주기 같은 튜닝값 — 은
각 모듈 `core/config.py`(`services/telegram_bot/core/config.py`·`services/web/core/config.py`)의
리터럴 상수다(2026-08-23 정리, 2026-09-13 모듈별 분리). 값을 바꾸려면 코드를
고치고 git에 커밋한다 — 서버 `.env`를 직접 고치던 예전 방식은 무엇을 언제
왜 바꿨는지가 서버에만 남고 git 이력에는 없었다.

```bash
sudo -u stockbot git -C /srv/stock-chatbot log -1 --oneline
sudo -u stockbot git -C /srv/stock-chatbot pull
sudo systemctl restart stock-chatbot
```

지금 서버 `.env`에 남아 있어야 하는 키는 `.env.example`에 적힌 것뿐이다:
`TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID`·`ALLOWED_CHAT_IDS`·
`CLOUDFLARE_ACCOUNT_ID`·`CLOUDFLARE_API_TOKEN`·`CLOUDFLARE_MODEL`(모델
폐기·개명에 코드 배포 없이 대응하는 유일한 예외)·`WEB_ADMIN_USER`·
`WEB_ADMIN_PASSWORD`·`POLYMARKET_PROXY_URL`. 이 목록 밖의 키가 `.env`에
남아 있다면(예: 옛 `NEWS_GLOBAL_LIMIT=4`) 지운다 — `load_dotenv`가 이걸
`os.environ`에 얹어도 더 이상 아무도 읽지 않으니 무해하지만, 다음에 값을
바꿀 때 "여기 있는 값이 진짜인가" 혼란을 남긴다.

**`ALLOWED_CHAT_IDS`가 비면 `ConfigurationError`로 기동하지 않는다.** 빈 값을
전체 허용으로 읽던 분기를 없앴기 때문이다(상태가 채팅별로 나뉘어 있지 않아
설정 누락이 곧바로 공개 봇이 된다). 숫자가 아닌 자리표시자만 남은 경우도 같게
막힌다. `TELEGRAM_CHAT_ID`와 같은 값을 넣으면 되고, 쉼표로 여러 개도 된다.

```bash
sudo grep -n '^ALLOWED_CHAT_IDS=' /srv/stock-chatbot/.env
```

## 6. Neurons 실사용 측정

| 구분 | 값 |
|---|---|
| 무료 한도 | 10,000 Neurons/일 (**UTC 00시 = JST 09시** 리셋) |
| 60분 주기 상한 | 하루 408건 번역 (주간 17주기 × 소스 6곳 × 4건) + 야간 요약 4회 |
| 20분 주기 시절 실측 | 하루 272~323건 ≈ 3,000~3,600 |

**상한이 실측보다 큰 것이 요점이 아니다.** 20분 주기에서는 상한이 발행량보다
훨씬 커서 사실상 발행되는 대로 다 번역했고 실제 소비는 소스 발행량이 정했다.
60분 주기에서는 상한이 먼저 걸리므로 실제 소비가 표의 값에 가까워진다. 반대로
야간 7시간분이 기사별 번역에서 통째로 빠지고 사전선별의 재탕 차단이 소스 간
중복을 걷어낸다 — 순증인지 순감인지는 재 봐야 안다.

**서버에서 잰다.** 같은 명령이 `terraform output verify_commands`에 들어 있다.
최근 7일을 UTC 일자별로 한 줄씩(`YYYY-MM-DD <합계>`) 뱉는다.

```bash
journalctl -u stock-chatbot --since "$(date -u -d '6 days ago' '+%Y-%m-%d 00:00:00 UTC')" --until "$(date -u -d 'tomorrow' '+%Y-%m-%d 00:00:00 UTC')" -o short-iso-precise --utc --no-pager | awk 'match($0, /neurons=[0-9]+([.][0-9]+)?/) { day = substr($1, 1, 10); total[day] += substr($0, RSTART + 8, RLENGTH - 8) } END { for (day in total) printf "%s %.2f\n", day, total[day] }' | sort
```

**로컬 `bot.log`로는 이 측정을 할 수 없다.** 로그 포맷(`%(asctime)s`)이 호스트
로컬 시각을 오프셋 없이 찍어 한 줄만 보고 UTC 일자를 복원할 수 없다. JST
호스트라는 외부 가정을 얹어야 환산되므로 판정 근거로는 서버 수치만 쓴다.

**판정과 조치**

- 한도를 넘기면 `config.py`의 `NEWS_SOURCE_ARTICLE_LIMIT`부터 내리고 커밋한다.
  하루 기사량을 정하는 상한은 송출 상한(`NEWS_DIGEST_SEND_LIMIT`)이 아니라
  이 값이다.
- 소진되면 그날 남은 시간 동안 `/research`와 `/market`이 **막힌다.** 뉴스
  다이제스트만 비는 게 아니다.
- 여유가 크게 남으면 깊이(본문 길이·후보 수)를 먼저 올린다. **깊이는 싸고
  수량은 비싸다** — 기사 1건이 호출 1회이므로 수량은 선형으로 늘어난다.

## 7. 뉴스 사전선별 운영 확인

`news_prefilter`는 원문 후보를 사건별로 묶고 보고서 입력을 선별한다.
기준은 `code_guide.md`의 사전선별 항목이다. 기본 모드는 `active`이고 소스당
12건 중 중요도순 10건·무작위 탐색 2건을 큐로 보낸다. 기사별 번역 경로는 없다.

배포·재기동 후 `/system prefilter`에서 active, 소스당 12건, 탐색 2건을 확인한다.
구버전의 “번역 순서는 최신순”, “하루 4.32h 예산”이 보이면 실행 중인 체크아웃과
프로세스의 시작 시각을 확인한다. 개발 workspace 변경만으로 운영 봇은 바뀌지 않는다.

```bash
systemctl show stock-chatbot -p WorkingDirectory -p ExecStart -p ActiveEnterTimestamp
journalctl -u stock-chatbot --since '30 minutes ago' --no-pager | rg PREFILTER
```

모델이 아직 없으면 라벨 120건·3일과 시간 분할 양쪽의 양성·음성이 모였는지 본다.
`invalid_time_split`은 무작위 평가 표본에서 낮은 중요도 라벨이 충분히 들어오는지도
확인한다. CPU 사용량을 늘리려고 미평가 기사를 음성으로 채우지 않는다.

`search_complete`는 현재 자료의 32회 검증 완료로 정상 대기다. 새 보고서 평가가
생기면 다시 학습한다. `burst`·`urgent`·`load`는 다른 작업에 양보한 상태다.
일일 CPU 상한은 없으며, 실제 사용량과 학습 완료 trial을 함께 확인한다.

효과는 최근 7일의 반복 차단, 최신순 밖 발견 후보의 후속 평가, 점수 AUC,
검증 AP로 점검한다. 관측 라벨 500건·음성 50건·7일 전에는 개선을 단정하지 않는다.
active에서도 미평가 기사는 음성이 아니며 불일치율은 품질 향상률이 아니다.

점수 효과가 나쁘면 `config.py`의 `NEWS_PREFILTER_MODE`를 `"shadow"`로 변경해
배포·재기동한다. 반복 차단과 관측은 유지하고 보고서 후보 순서만 최신순으로 돌아간다.

## 8. Polymarket 현재 대시보드

**봇과 완전히 분리된 경로다.** 텔레그램 `/polymarket`, 08:35 스냅숏 job, 거시
위험선호 컨센서스, 90일 이력, 승격 게이트, CLOB 백필은 2026-09-01에 모두
철수했다. 지금 있는 것은 systemd timer가 3시간마다 굽는 **현재 스냅숏 하나**와
그것을 내보내는 공개 웹 화면뿐이다. 봇을 재기동해도, 봇이 죽어 있어도 이
화면은 마지막 generation을 계속 보여 준다.

| 자리 | 파일 |
|---|---|
| 순회·정규화·저장 | `services/web/polymarket/dashboard/` |
| one-shot 진입점 | `services/web/polymarket/refresh.py` |
| 읽기 repository | `services/web/polymarket/repository.py` |
| 화면·API | `services/web/server.py`(`/polymarket`, `/api/polymarket/*`) |
| systemd 유닛 | `infra/systemd/stock-chatbot-polymarket-refresh.{service,timer}` |
| 산출물 | `data/webpub/polymarket/`의 `current.json`·`status.json`·`generations/` |
| 크기 실측 도구 | `services/web/tests/polymarket_manifest_size_probe.py` |

### 8-1. 설치

**부트스트랩도 3절의 코드 갱신도 이 유닛을 설치하지 않는다.** 인스턴스를 새로
만들거나 이 기능을 처음 켤 때 한 번 직접 설치한다(11-1의 웹 유닛과 같다).

```bash
sudo cp /srv/stock-chatbot/infra/systemd/stock-chatbot-polymarket-refresh.{service,timer} /etc/systemd/system/
sudo cp /srv/stock-chatbot/infra/systemd/stock-chatbot-polymarket-brief.service /etc/systemd/system/
sudo cp /srv/stock-chatbot/infra/systemd/stock-chatbot-polymarket-trending.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-chatbot-polymarket-refresh.timer
systemctl list-timers | grep polymarket
```

timer는 UTC +9 `00·03·06·09·12·15·18·21시` 고정 캘린더다(`OnCalendar`).
`Persistent=true`라 서버가 꺼져 있어 지나친 슬롯이 있으면 기동 직후 한 번
따라잡는다.

**refresh가 성공하면 섹터 줄글과 트렌드 조명이 이어서 돈다**(`OnSuccess=`). 두
유닛 다 `enable`하지 않는다 — timer가 아니라 refresh가 부른다. 03시에는 줄글만
건너뛰고(`POLYMARKET_BRIEF_QUIET_HOURS`) refresh와 트렌드는 그대로 돈다. 트렌드는
LLM을 부르지 않아 야간에도 멈출 이유가 없고, 여기서 한 주기를 건너뛰면 그 구간의
이동이 영영 사라진다(스냅숏이 그 주기에만 남는다).

트렌드 상태는 `data/webpub/polymarket/trending.json` 하나이고
`curl -s localhost:8788/api/polymarket/trending`으로 확인한다. `state`가
`warming_up`이면 비교할 직전 스냅숏이 아직 없다는 뜻이라 **다음 주기에 저절로
풀린다**. 이 파일을 지우면 그날 기준선도 함께 사라져 다음 주기가 기준선을 다시
세운다 — 확률 숫자와 화면 나머지는 영향받지 않는다.

기다리지 않고 지금 굽고 싶으면:

```bash
sudo systemctl start stock-chatbot-polymarket-refresh.service
journalctl -u stock-chatbot-polymarket-refresh -n 40 --no-pager
```

`Type=oneshot`이라 `systemctl start`는 끝날 때까지 블록한다(219 page, 실측 20~60초).

### 8-2. 상태 확인

```bash
curl -s localhost:8788/api/polymarket/health
sudo -u stockbot cat /srv/stock-chatbot/data/webpub/polymarket/status.json
sudo -u stockbot ls /srv/stock-chatbot/data/webpub/polymarket/generations/
```

| 보이는 것 | 뜻 |
|---|---|
| `status.json`이 아예 없다 | job이 한 번도 안 돌았다 — 8-1을 안 했다 |
| `"available": false` | `current.json`이 없다. 한 번도 승격에 성공하지 못했다 |
| `"last_result": "failed"` | `error` 필드가 원인을 말한다 → 8-4 |
| `freshness.state`가 `delayed`·`stale` | timer가 멈췄거나 순회가 계속 실패한다 |
| `generations/`가 3개 이상 | 정리가 실패하고 있다. 디스크를 본다 |
| `"last_result": "skipped_budget"` | 24시간 CPU·요청 예산을 넘겨 그 주기를 건너뛰었다 → 8-4(5) |

화면이 "현재 Polymarket generation을 읽지 못했습니다"만 띄우면 `current.json`이
없다는 뜻이고, **웹 프로세스를 재기동해도 고쳐지지 않는다** — webpub은 그 파일을
읽기만 한다. 만드는 것은 이 job이다.

### 8-3. 읽기 스모크 (새 인스턴스에서 한 번)

출구 IP가 Gamma에 막히는지, 커서가 실제로 전진하는지를 서버에서 직접 본다.
한국 PC에서 열렸다는 사실은 서버 접근을 보장하지 않는다.

```bash
sudo -u stockbot env RUN_POLYMARKET_SMOKE=1 /srv/stock-chatbot/venv/bin/python -m pytest -q -m polymarket_smoke
```

### 8-4. 장애 분기

먼저 `status.json`의 `error`를 읽는다. 아래 넷이 실제로 겪은 전부다.

**(1) `PolymarketError: bad_request` + 로그에 `status=451`** — Gamma가 이 서버
출구 IP의 지역을 막았다. 프록시를 문다.

```env
# 비어 있으면(기본) 직접 호출한다. 지역 차단(451)이 뜰 때만 채운다.
POLYMARKET_PROXY_URL=http://user:pass@proxy-host:port
```

`.env`에 넣고 job을 다시 돌린다. 봇 재기동은 필요 없다 — 이 경로는 봇과 무관하다.
socks5를 쓰려면 서버에 `pip install pysocks`가 먼저 있어야 한다(이 프로젝트가
기본으로 깔지 않는다). 프록시 서버 자체가 연결 불능이거나 timeout이면 그
프로세스는 직접 연결로 한 번 failover한다. **Gamma가 응답한 451은 프록시 장애가
아니므로 이 전환을 일으키지 않는다** — 우회하면 막힌 IP로 되돌아가기 때문이다.

떼어낼 때는 `.env`에서 지운다. **인스턴스 삭제와 `.env` 정리는 같은 날 한다** —
2026-08-25에 인스턴스는 이미 없는데 URL이 남아 재시도가 전부 `ConnectTimeout`으로
죽은 적이 있다. failover가 있어도 timeout을 한 번 문 뒤의 일이라 지연은 그대로다.

> 현황(2026-08-26 실측): 한국(AWS 서울·KT 회선)은 451, 도쿄(`ap-northeast-1`)는
> 200이다. 이것이 서버를 도쿄에 둔 이유이고, 그래서 지금 `.env`에
> `POLYMARKET_PROXY_URL`은 비어 있다. 도쿄 출구까지 막히면 위 절차로 켠다.

**(2) `manifest exceeds 16777216 bytes: N bytes / M events`** — 열린 event가
늘어 compact manifest가 16 MiB 상한을 넘었다. **상한을 올려서 넘기지 않는다.**
webpub이 이 파일을 통째로 파이썬 객체로 올리고 `MemoryMax=192M`이 걸려 있어,
올리면 화면이 비는 대신 웹 프로세스가 OOM으로 죽는다.

무엇이 자리를 먹는지부터 잰다. 실패한 실행이 남긴 shard를 읽으므로 API를 다시
부르지 않는다(다음 실행이 그 디렉토리를 지우기 전에 돌린다).

```bash
sudo -u stockbot /srv/stock-chatbot/venv/bin/python /srv/stock-chatbot/web/tests/polymarket_manifest_size_probe.py
```

고치는 방향은 **compact에서 목록·순위·필터·정렬이 읽지 않는 필드를 detail로
내리는 것**이다(`services/web/polymarket/dashboard/models.py`의 `normalize_event`). detail은
byte-addressed라 옮기는 비용이 사실상 없다. 2026-09-01에 7개(`slug`·
`category_reason`·`system_tags`·`liquidity_source`·`volume`·`market_count`·
`runner_up_probability`)를 내려 826 → 638 B/event로 줄였다. 더 내릴 것이
없으면 계획서 7-4가 정한 대로 카테고리 shard 설계로 간다.

**(3) 디스크가 찬다** — generation 하나가 detail shard만 116 MiB다. 정상이면
`generations/`에 **두 개만** 있다(current와 직전). 세 개 이상 쌓여 있으면
정리가 실패하는 것이므로 `journalctl`에서 `generation 정리 실패`를 찾는다.
남는 디렉토리는 손으로 지워도 안전하다 — `current.json`이 가리키는 것과 그
직전만 아니면 참조하는 곳이 없다.

**(5) `last_result: "skipped_budget"`** — 최근 24시간 사용량이
`POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS`(900 CPU-second) 또는
`POLYMARKET_WEB_MAX_DAILY_REQUESTS`(3,000회)를 넘어 순회를 시작하지 않았다.
`status.json`의 `rolling_cpu_seconds`·`rolling_requests`가 현재 합계다.

의도된 정지이므로 systemd는 성공으로 본다. **한 번 뜨는 것은 정상이 아니다** —
하루 8회 순회가 이 예산 안에 들어오도록 잡아 놓았으므로, 뜬다면 한 번이 예상보다
오래 돌았거나(정규화가 느려졌거나 event가 늘었거나) 실패가 반복되며 CPU를
태우고 있다는 뜻이다. `cpu_samples`에서 어느 실행이 컸는지 본다.

주기를 줄이는 것은 마지막 수단이다. 먼저 `journalctl`에서 실패가 반복되는지,
`walk_seconds`가 평소(20~60초)보다 긴지 확인한다.

**(4) 웹 프로세스 메모리** — `current.json`을 처음 요청받을 때 통째로 올린다.

```bash
systemctl status stock-chatbot-web | grep -i memory
```

**페이지를 한 번 연 뒤에 재야 한다** — 열기 전에는 아직 안 읽어 낮게 나온다.
계획서 7-4의 예산은 96 MiB이고 유닛 상한은 192 MiB다. 2026-09-01 실측은
event 21,872건에 89.8 MiB(swap 22.5 MiB)로 예산에 턱걸이였다. 여기가 먼저
아프면 event 목록을 카테고리 shard로 나눈다.

### 8-5. 철수

이 화면이 쓸모없다고 판단하면 timer를 끄고 산출물을 지운다. 봇은 영향받지 않는다.

```bash
sudo systemctl disable --now stock-chatbot-polymarket-refresh.timer
sudo rm /etc/systemd/system/stock-chatbot-polymarket-refresh.{service,timer}
sudo systemctl daemon-reload
sudo rm -rf /srv/stock-chatbot/data/webpub/polymarket
```

화면까지 걷어내려면 `services/web/server.py`의 `/polymarket`·`/api/polymarket/*` 라우트와
`services/web/pages.py`의 `POLYMARKET_HTML`, `services/web/polymarket/dashboard/`,
`services/web/polymarket/repository.py`를 지우고 웹을 재기동한다.

## 9. 백업·복구·재부팅

상태는 전부 `/srv/stock-chatbot/data/` 하위 JSON/JSONL이고 DB가 없다. 보호 장치는 셋이다.

| 장치 | 주기 | 자리 |
|---|---|---|
| tar 백업 cron | 매일 03:00 JST, 14일 보관 | `/var/backups/stock-chatbot/backup-YYYY-MM-DD.tgz` |
| Lightsail 자동 스냅샷 | 매일 04:00 JST (19:00 UTC) | 콘솔 |
| 원자적 쓰기 | 매 저장 | 각 모듈 `core/storage.py` (임시파일 → fsync → `os.replace`) |

```bash
ls -la ~/backup-*.tgz | tail -5                      # 백업이 실제로 도는지
tar tzf ~/backup-$(date +%F).tgz | head              # 내용 확인
tar xzf ~/backup-YYYY-MM-DD.tgz -C /tmp/restore      # 복구는 다른 경로에 풀고 골라 덮는다
```

**재부팅으로 `data/`는 사라지지 않는다.** 영구 루트 디스크에 있어 reboot도
stop/start도 보존한다. 사라지는 것은 인스턴스를 **삭제·재생성**할 때뿐이고,
그때는 `data/`가 별도 볼륨이 아니라 루트 디스크에 있으므로 스냅샷/백업 tar에
없는 것은 전부 잃는다.

재부팅으로 실제 손해가 나는 것은 셋뿐이다.

- 4절의 스케줄 창을 덮으면 그 실행분(특히 Polymarket 하루치)이 빈다.
- `event_memory.json`은 60초에 한 번 저장하므로 직전 1분의 `translated_at`
  표시를 잃을 수 있다(사건 한둘이 재번역될 수 있다).
- 사전선별 보정이 `observations.jsonl`을 처음부터 한 번 다시 읽는다(읽기 offset이
  메모리 전용이다). 손실이 아니라 CPU 비용이고 그날 예산에서 나간다.

공유 호스트의 앱 런타임은 `cloud-init`이 아니라 `infra/scripts/install-shared-host.sh`가
설치한다. 재부팅으로 다시 돌지 않으며, 유닛이나 백업 cron을 다시 깔아야 하면 그
스크립트를 직접 실행한다(`infra/host-contract.md`).

## 10. 장애 대응

**봇이 뜨지 않는다.**

```bash
journalctl -u stock-chatbot -n 80 --no-pager
```

| 로그 | 원인 | 조치 |
|---|---|---|
| `ConfigurationError` + 허용 목록 | `ALLOWED_CHAT_IDS`가 비었다 | 5절 |
| `Conflict: terminated by other getUpdates` | 로컬 봇이 같이 켜져 있다 | 한쪽을 끈다 |
| `ModuleNotFoundError: telegram_bot`·`web` | `WorkingDirectory`가 저장소 루트가 아니다 | 유닛 파일 확인 |
| `No module named pytest` | 부트스트랩은 실행 의존성만 깐다 | `pip install -r requirements-dev.txt` |
| 차트 관련 실패 | `MPLBACKEND=Agg`가 없다 | 유닛 파일 확인 |

**직전 배포로 되돌린다.** 서버에서 커밋을 되돌리고 재기동한다.

```bash
sudo -u stockbot git -C /srv/stock-chatbot log --oneline -5
sudo -u stockbot git -C /srv/stock-chatbot checkout <직전 커밋>
sudo systemctl restart stock-chatbot
```

**서버를 세운다.** `terraform output -raw rollback_command`. 로컬 봇을 다시 켤
때는 서버가 확실히 멈춘 뒤에 켠다.

**메모리·CPU를 본다.** 공유 호스트는 4GB RAM과 4GB swap이다.

```bash
free -h; uptime; systemctl status stock-chatbot --no-pager | grep Memory
```

load average가 상시 1.5 이상이면 사전선별은 학습을 양보한다.
`/system prefilter`와 로그에서 최근 대기 이유가 `load`인지 확인하고, 호스트의
다른 프로세스·스케줄 중첩을 점검한다. 고정 CPU 비율·일일 학습 상한은 없다.

학습 CPU와 봇 foreground CPU는 별도로 집계한다. 단일 worker에서 1분마다
최대 30 CPU초를 수행하되, 리서치·3시간 보고서·시장 컨센서스 작업 중에는
`버스트 우선 작업 진행 중 · 보정 양보`가 찍힌다. 같은 자료의 32 trial이
완료되면 새 라벨까지 쉬므로 CPU가 낮다는 사실만으로 장애로 판단하지 않는다.

## 11. 공개 웹 (읽기 전용)

봇이 구워 둔 산출물만 보여 주는 **별도 프로세스**다. 웹에는 실행 트리거가 없다 —
`/research run`과 `/market` 재계산은 텔레그램에만 둔다. 누구나 누를 수 있으면
Neurons가 링크를 받은 사람 수만큼 나가고, 리서치 상태는 단일 사용자 형식이라
동시 실행이 서로를 덮는다.

공개 주소는 **`https://nunchi.live`**(Route 53 등록, A 레코드가 고정 IP를 가리킨다).

**면을 나눠 둔다.** 잠금은 시스템이 아니라 **내용**을 지킨다 — 웹은 미리 구운 파일만
내보내므로 공개돼도 Neurons가 나가지 않고, 쓰기 라우트가 없어 인증이 새도 잃는 것은
열람뿐이다. 그런데 리서치 산출물에는 종목명·`add`/`watch`·confidence가 들어간다.
색인되면 면책 문구와 무관하게 밖에서는 종목 추천으로 읽히고 되돌릴 수 없다.

| 경로 | 내용 | 인증 |
|---|---|---|
| `/`, `/about`, `/market_chart.png`, `/api/market`, `/api/meta` | 국가별 감성 집계 | 없음 |
| `/research`, `/api/research` | `sight`, 종목별 액션·confidence | `friend` 계정 |

두 면 모두 `X-Robots-Tag: noindex, nofollow`를 받는다. **검색 크롤러 자체는 막지
않는다** — 막으면 크롤러가 noindex를 읽지 못해 URL만 색인에 남을 수 있다.
공개면의 nav에는 리서치 링크가 그대로 있어서, 익명 방문자가 누르면 브라우저
인증창이 뜬다(의도된 동작이다).

**대신 크롤·AI 트래픽은 두 겹으로 막는다**(11-6).

```text
브라우저 ── https://nunchi.live:443 ── Caddy (TLS + Basic 인증) ── http://127.0.0.1:8788 ── webpub
```

내부 구간이 HTTP인 것은 loopback이라 인터넷으로 평문이 나가지 않기 때문이다.
Basic 인증도 TLS가 성립한 뒤에 처리된다.

| 자리 | 파일 |
|---|---|
| 굽기(봇 안에서만 호출) | `services/web/export.py` |
| 읽기 전용 웹(`GET`만) | `services/web/server.py` |
| systemd 유닛 | `infra/systemd/stock-chatbot-web.service` |
| 프록시 설정 견본 | `infra/Caddyfile.example` |
| 산출물 | `data/webpub/`의 `market.json`·`market_chart.png`·`research.json`·`meta.json` |

### 11-1. 웹 프로세스

부트스트랩은 이 유닛을 설치하지 않는다(봇 유닛만 만든다). 인스턴스를 새로 만들면
직접 설치한다.

```bash
sudo cp /srv/stock-chatbot/infra/systemd/stock-chatbot-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-chatbot-web
curl -s localhost:8788/api/meta        # 산출물 시각. 봇이 아직 굽기 전이면 {}
```

봇과 독립이라 봇을 재기동해도 웹은 마지막 산출물을 계속 보여 준다. 코드 갱신
(3절)으로 `services/web/server.py`가 바뀌었으면 이 유닛도 함께 재기동한다.

```bash
sudo systemctl restart stock-chatbot-web
```

### 11-2. 도메인과 인증서

Caddy는 apt 저장소에서 받는다.

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
```

설치 직후의 Caddy는 기본 환영 페이지를 80번에 띄운다. `/etc/caddy/Caddyfile`을
`infra/Caddyfile.example` 형태로 **먼저 바꾼 뒤** 기동한다.

**손으로 쓰지 않는다.** 스크립트가 견본을 렌더링한다 — 바꿀 것은 도메인·사설
IP·비밀번호 해시 셋뿐이고, 나머지(스킴 없는 첫 줄, `@research` matcher, `bind`)는
견본이 이미 맞춰 두었다. 빠뜨리면 자동 HTTPS가 꺼지거나, 사이트 전체가 잠기거나,
443을 Tailscale과 다투다 기동하지 못한다.

```bash
ip -4 -o addr show | awk '{print $2, $4}'    # 사설 NIC 주소를 확인한다
hash="$(caddy hash-password)"                # 프롬프트로 입력받는다. 히스토리에 평문이 남지 않는다
sudo /srv/stock-chatbot/infra/scripts/install-caddyfile.sh \
    --domain nunchi.live --bind <사설 IP> --hash "$hash"
journalctl -u caddy -n 30 --no-pager | grep -i certificate
```

스크립트가 백업·`caddy validate`·reload까지 하고, validate가 실패하면 되돌린다.
`--dry-run`으로 만들어질 내용을 먼저 볼 수 있다(해시는 가린다).

도메인의 A 레코드는 고정 IP를 가리키고 있어야 한다. 80번이 닫혀 있으면 HTTP-01
검증이 실패한다(1절).

인증서는 Let's Encrypt에서 자동 발급·갱신된다. **AWS가 주는 호스트네임
(`*.compute.amazonaws.com`)으로는 발급되지 않는다** — CA가 정책으로 거부한다.
도메인이 반드시 필요한 이유가 이것이다.

갱신은 만료 30일 전에 자동으로 일어난다. 확인만 한다.

```bash
sudo ls -l /var/lib/caddy/.local/share/caddy/certificates/*/*/          # 발급 시각
echo | openssl s_client -connect nunchi.live:443 -servername nunchi.live 2>/dev/null | openssl x509 -noout -dates
```

### 11-3. 비밀번호 교체

접근 로그의 방문 수가 지인 수와 맞지 않으면 먼저 바꾼다.

```bash
sudo /srv/stock-chatbot/infra/scripts/set-caddy-password.sh --password-stdin
journalctl -u caddy --since today --no-pager | grep -c '"status":200'
```

프롬프트로 새 비밀번호를 두 번 받아 해시로 바꿔 넣는다. **평문은 인자로 받지
않는다** — 셸 히스토리와 `ps`에 그대로 남는다. 이미 해시를 들고 있으면
`--hash "$(caddy hash-password)"`를 쓴다. `basicauth` 블록의 그 사용자 줄 하나만
바꾸고, 백업·validate·reload는 스크립트가 한다. 끝나면 **옛 해시가 담긴 백업
파일을 지운다** — 경로는 스크립트가 알려 준다.

그래도 계속되면 443을 닫고(1절) SSH 터널로 되돌린다 —
`ssh -L 8788:127.0.0.1:8788 ubuntu@<고정 IP>`.

### 11-4. 장애

| 증상 | 원인 | 조치 |
|---|---|---|
| 502 Bad Gateway | `stock-chatbot-web`이 죽었다 | `systemctl status stock-chatbot-web`, 로그 확인 후 재기동 |
| 인증서 발급 실패 | A 레코드가 안 맞거나 80이 닫혔다 | `dig +short nunchi.live`과 1절의 포트 상태를 함께 본다 |
| 화면은 뜨는데 "산출물이 아직 없습니다" | 봇이 아직 굽지 않았다 | `/market`이나 `/research run`을 한 번 돌린다 |
| 기준 시각이 하루 이상 밀렸다 | 봇의 굽기 경로가 실패하고 있다 | `journalctl -u stock-chatbot`에서 `[WEBPUB]` 줄을 본다 |

```bash
systemctl status stock-chatbot-web caddy --no-pager
journalctl -u stock-chatbot-web -n 50 --no-pager
journalctl -u stock-chatbot --no-pager | grep WEBPUB | tail -10   # 굽기 실패 여부
sudo -u stockbot ls -la /srv/stock-chatbot/data/webpub/
```

### 11-5. 중단 기준

공개를 되돌릴 조건을 미리 정해 둔다. "조금만 더 보자"로 넘기지 않는다.

| 위험 | 신호 | 조치 |
|---|---|---|
| 웹 부하로 학습이 지연된다 | 사전선별 `중단=load`가 지속된다(7절) | 프로세스별 CPU와 굽는 주기를 점검한다 |
| 메모리 압박 | 스왑 사용이 상시, OOM kill | 웹 프로세스를 이 인스턴스에서 뺀다 |
| 리서치면 남용 | 인증 성공 로그가 지인 수와 맞지 않는다 | 11-3의 비밀번호 교체 |
| 공개면 남용 | 익명 트래픽이 스크레이퍼 수준으로 는다 | `basic_auth`의 `@research` matcher를 빼 전면 잠금. 계속되면 443을 닫고 터널로 되돌린다 |
| 산출물이 낡는다 | `meta.json` 시각이 하루 이상 밀린다 | 봇 쪽 굽기 경로가 실패하고 있다. 11-4를 본다 |

443을 닫아도 잃는 것은 열람뿐이다 — 봇도 `stock-chatbot-web`도 그대로 돌고,
`ssh -L 8788:127.0.0.1:8788`로 계속 볼 수 있다.

### 11-6. 크롤·AI 봇 트래픽

부하의 실체는 화면이 아니라 `/api/`다. `/api/polymarket/events`는 필터·정렬·페이지
조합이 사실상 무한한 URL 공간이고, `/api/polymarket/events/{event_id}`는 열린 event
22,000건이 각각 detail shard를 seek한다. 크롤러가 이 둘을 훑으면 1 GiB 인스턴스에서
순회 one-shot·봇과 CPU를 다툰다.

두 겹으로 막는다. **역할이 다르므로 둘 중 하나만 두지 않는다.**

| 겹 | 자리 | 무엇을 하나 | 한계 |
|---|---|---|---|
| 권고 | 앱의 `/robots.txt` (`services/web/pages/robots.py`) | `/api/` 전체와 AI 수집 봇에 Disallow | 봇이 스스로 지킬 때만 듣는다 |
| 강제 | Caddy `@aibots` matcher (`infra/Caddyfile.example`) | 같은 UA 목록을 프록시 전에 `abort` | UA를 위장하면 잡지 못한다 |

**두 목록은 같이 고친다.** 갈라지면 한쪽만 막힌 채로 돈다.

검색 크롤러(Googlebot 등)는 그대로 들여보낸다 — 위의 noindex를 읽어야 하기
때문이다. AI 수집 봇은 검색 색인과 **다른 UA**를 쓰므로(`Google-Extended`가 정확히
그 분리를 위한 UA다) 통째로 막아도 noindex 전달에 영향이 없다.

앱 쪽은 코드 갱신(3절)과 웹 재기동으로 끝난다. Caddy 쪽은 저장소에 견본만 있고
실제 파일은 호스트의 `/etc/caddy/Caddyfile`이라 스크립트가 옮긴다.

```bash
infra/scripts/apply-caddy-bots.sh --dry-run    # 무엇이 바뀌는지 먼저 본다(root 불필요)
sudo infra/scripts/apply-caddy-bots.sh         # 백업 → 반영 → validate → reload
```

**손으로 편집하지 않는다.** UA 목록은 견본(`infra/Caddyfile.example`)과 앱의
robots.txt(`services/web/pages/robots.py`) 둘이 같아야 하는데, 편집기로 옮기면 목록을 고칠
때마다 어긋날 자리가 하나 더 생기고 어긋난 것을 알아챌 방법이 없다. 스크립트는
견본의 `# BEGIN aibots` ~ `# END aibots` 사이를 그대로 복사한다.

스크립트가 하는 일과 안 하는 일:

- **여러 번 돌려도 같다.** 표식이 있으면 그 사이만 바꾸고, 바뀔 것이 없으면 파일도
  reload도 건드리지 않는다.
- **되돌릴 수 있다.** 쓰기 전에 `Caddyfile.bak.<UTC시각>`으로 백업하고,
  `caddy validate`나 `reload`가 실패하면 백업을 되돌린다. 깨진 설정으로 reload하지
  않는다.
- **구조가 다르면 멈춘다.** `header X-Robots-Tag` 줄도 표식도 없으면 아무 데나
  넣지 않고 종료한다. 그때는 견본을 보고 표식 두 줄을 먼저 넣는다.

`verify-app.sh`가 실제 설정에 블록이 남아 있는지 함께 본다 — 갈라지면 robots.txt만
남고 강제는 사라지는데, 화면은 멀쩡해 보여 그냥은 알아챌 수 없다.

확인한다. 첫 줄은 끊기고(exit 52 또는 연결 종료), 나머지 셋은 200이어야 한다.

```bash
curl -sS -A "GPTBot" -o /dev/null -w '%{http_code}\n' https://nunchi.live/
curl -sS -o /dev/null -w '%{http_code}\n' https://nunchi.live/
curl -s https://nunchi.live/robots.txt | head -8
curl -sS -o /dev/null -w '%{http_code}\n' https://nunchi.live/polymarket
```

IP 단위 rate limit은 두지 않는다. 우분투 저장소판 Caddy 2.6.2에 없는 기능이라
플러그인을 넣어 다시 빌드해야 하는데, 그러면 배포가 파일 복사로 끝나지 않는다.
UA를 위장한 봇이 실제로 문제가 되면 그때 다시 판단한다.
