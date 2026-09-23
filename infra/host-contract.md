# 공유 호스트 계약 (배포서)

이 앱은 도쿄 공유 Lightsail 인스턴스 `orca-host-tokyo-v2` 안에서 도는 **입주 앱**이다.
**두 저장소의 경계는 한 줄이다 — 호스트 저장소 `remote_coding`
(`C:/Users/PSI/orca/remote_coding`)은 AWS 환경을 만들고, 이 저장소 `infra/`는 그 환경에
서비스를 심는다.** 인스턴스·고정 IP·키페어·공인 방화벽·자동 스냅샷·Orca·Tailscale·OS
계정은 호스트 저장소가 소유하고, **이 저장소는 AWS 자원을 만들지 않는다.** 반대로
체크아웃·venv·systemd 유닛·cron·Caddy 설정·앱 점검은 전부 이 저장소가 소유하고,
호스트 저장소는 우리 경로·유닛·포트를 모른다(2026-09-24, 호스트 저장소 `6eb9d37`).

> 2026-09-23 호스트가 `orca-host-tokyo`에서 `orca-host-tokyo-v2`로 이관됐다. 고정 IP
> `16.76.30.47`은 그대로 새 인스턴스에 붙었고, 옛 인스턴스는 롤백용으로 서비스를 멈춘 채
> 보존한다. 이관 절차·롤백 기준은 호스트 저장소의 `docs/server-migration.md`가 소유한다 —
> 여기 옮겨 적지 않는다. 그 전(2026-09-12)의 독립 인스턴스 폐기 기록은 git 이력에 있다
> (`git log -- infra/merge-plan.md infra/terraform`).

**같은 자원을 두 저장소에서 선언하지 않는다.** 특히 Lightsail의 공개 포트 API는 규칙
전체를 교체하므로(`put-instance-public-ports`), 여기서 방화벽을 따로 선언하면 나중에
apply한 쪽이 상대의 규칙을 통째로 지운다.

## 계약 값

| 계약 값 | 현재 | 소유 | 우리 쪽에서 쓰는 곳 |
|---|---|---|---|
| 리전 / AZ | `ap-northeast-1` / `ap-northeast-1a` | 호스트 | `server-ops.md` 0절 |
| 인스턴스 / 요금제 | `orca-host-tokyo-v2` / `medium_3_0` (4GB) | 호스트 | `server-ops.md` 0절 |
| 고정 IP / Tailscale 이름 | `16.76.30.47` / `orca-host-tokyo-v2.<tailnet>.ts.net` | 호스트 | `server-ops.md` 1절 |
| 호스트 타임존 | `UTC` | 호스트 | 백업 cron 시각, `verify-app.sh` |
| 자동 스냅샷 | 매일 19:00 UTC | 호스트 | 앱 백업을 그 앞(18:00 UTC)에 끝낸다 |
| 공개 웹 80/443 | 호스트의 `enable_public_web` | 호스트 | Caddy(`caddy` 계정) → `127.0.0.1:8788` |
| 실행 계정 | `ubuntu` 단일 계정(sudo 있음) | 호스트 | `scripts/lib.sh`, `systemd/`의 `User=`/`Group=` |
| 앱 경로 | `/srv/stock-chatbot` | 우리 | `scripts/lib.sh`, `systemd/` |
| 앱 내부 포트 | `127.0.0.1:8788`(읽기 웹) | 우리 | 공인 방화벽에 열지 않는다 |
| 앱 데이터 백업 | 매일 18:00 UTC, 14일 보관 | 우리 | `systemd/stock-chatbot-backup.cron.tmpl` |

**실행 계정은 호스트가 정한다.** 예전에는 앱 전용 `stockbot` 계정을 두었으나, 호스트가
이관하면서 봇·웹·Orca를 `ubuntu` 하나로 합쳤다. 그래서 우리 유닛도 `User=ubuntu`다 —
앱 전용 계정으로 되돌리려면 호스트 저장소의 결정부터 바뀌어야 한다. `ubuntu`에 sudo가
있으므로 앱 프로세스와 호스트 관리 권한이 분리돼 있지 않다는 점을 알고 쓴다.

호스트 쪽 현재 값은 호스트 저장소에서 읽는다. 새 인스턴스의 state는
`terraform/migration/`이 소유하고, 운영 고정 IP는 기존 `terraform/` state가 계속 소유한다.

```powershell
terraform -chdir=C:\Users\PSI\orca\remote_coding\terraform output -raw auto_snapshot_time_utc
terraform -chdir=C:\Users\PSI\orca\remote_coding\terraform output -raw public_web_enabled
```

### 시각이 UTC인 이유

`cron.d`는 타임존을 선언할 수 없어 호스트 설정을 그대로 따르고, Lightsail 자동 스냅샷
시각도 UTC 정시다. 그래서 호스트를 UTC로 고정하고(호스트 저장소 `scripts/config.env`의
`HOST_TIMEZONE`), 백업 cron은 18:00 UTC(03:00 KST/JST)로 둔다 — 스냅샷보다 한 시간 앞이다.
**현지 시각이 중요한 스케줄은 cron이 아니라 systemd timer에 타임존을 직접 적는다**
(`stock-chatbot-polymarket-refresh.timer`, `polymarket-shorts.timer`는 `Asia/Seoul`).
앱의 '지금'은 `services/telegram_bot/core/clock.py`가 JST로 고정하므로 하루 경계는 호스트 타임존과
무관하다.

## 호스트 값이 필요할 때

우리가 직접 바꾸지 않고 호스트 저장소에서 바꾼다. 경계에서 만나는 지점은 셋이다.

- **공개 웹**: 유효한 DNS와 Caddy가 준비된 뒤 호스트의 `enable_public_web = true`로
  80/443을 연다. 앱 내부 포트(8788)는 어떤 경우에도 공인 방화벽에 열지 않는다.
  절차는 `server-ops.md` 11절.
- **자동 스냅샷**: 영속 데이터(`/srv/stock-chatbot/data`)가 스냅샷에 함께 담긴다.
  시각을 옮기면 우리 백업 cron 시각도 함께 옮긴다.
- **실행 계정**: 호스트가 만든 `ubuntu`를 쓴다. 계정을 바꾸려면 호스트 쪽 결정이 먼저다.

AWS 자격증명과 Lightsail IAM 정책도 호스트 저장소가 소유한다
(`remote_coding/terraform/iam-policy.json`). 앱 배포에는 SSH만 있으면 된다.

## 앱 설치와 점검

서비스 설치는 이 저장소의 스크립트가 전부 한다. 처음 한 번만 체크아웃과 `.env`를
사람이 놓는다.

```bash
sudo install -d -o ubuntu -g ubuntu -m 0750 /srv/stock-chatbot
sudo -u ubuntu git clone --branch main https://github.com/tkddls8848/stock_chatbot.git /srv/stock-chatbot
sudo install -o ubuntu -g ubuntu -m 0600 <로컬에서 옮긴 .env> /srv/stock-chatbot/.env
sudo /srv/stock-chatbot/infra/scripts/install-shared-host.sh   # venv·의존성, 유닛 전부, 백업 cron
sudo /srv/stock-chatbot/infra/scripts/verify-app.sh            # 앱 점검
```

| 스크립트 | 하는 일 |
|---|---|
| `install-shared-host.sh` | venv·의존성(`requirements.lock.txt`가 있으면 그것), 봇·읽기 웹·폴리마켓 timer와 one-shot 넷(refresh·brief·trending·annotate) 유닛, 앱 데이터 백업 cron |
| `deploy.sh` | 떠 있는 서버의 갱신: `main` fast-forward → 위 설치 → 켜져 있던 봇·웹 재시작 → 점검(`server-ops.md` 3절) |
| `verify-app.sh` | 앱 점검(서비스 상태·실행 계정·`.env` 권한·웹 응답·백업·호스트 타임존) |

쇼츠(`polymarket-shorts.{service,timer}`)는 자기 venv를 가진 별개 패키지라 설치
스크립트가 깔지 않는다 — `shorts/docs/`의 절차를 따른다.

설치 스크립트는 **처음 설치할 때 서비스를 기동하지 않는다.** 같은 텔레그램 토큰으로 두
프로세스가 `getUpdates`를 치면 `Conflict: terminated by other getUpdates request`로 양쪽이
번갈아 죽는다. 기동은 "다른 곳의 봇 정지 → 여기서 `systemctl enable --now`" 순서로 사람이
한다. 재설치는 켜 둔 서비스를 끄지 않는다.

호스트 자체(Orca·Tailscale·OS·방화벽) 점검은 호스트 저장소의 `scripts/util/verify-host.sh`가
맡는다. 서로의 내부는 검사하지 않는다.

일상 운영(접속·배포 갱신·설정 변경·실측·백업 복구·장애 대응)은
[`server-ops.md`](server-ops.md)다.
