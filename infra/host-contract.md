# 공유 호스트 계약 (배포서)

이 앱은 도쿄 공유 Lightsail 인스턴스 `orca-host-tokyo` 안에서 도는 **입주 앱**이다.
인스턴스·고정 IP·키페어·공인 방화벽·자동 스냅샷·OS 기본 설정은 호스트 저장소
`remote_coding`(`terraform/`)이 소유하고, **이 저장소는 AWS 자원을 만들지 않는다.**

> 2026-09-12까지는 이 저장소에도 독립 Lightsail을 만드는 `infra/terraform/`이 있었다.
> 그 인스턴스·고정 IP·키페어는 폐기했고 state도 정리했으므로, 정의를 지우고 이 문서로
> 대체했다. 옛 정의와 `user_data` 부트스트랩은 git 이력에 남아 있다
> (`git log -- infra/terraform`). 폐기 경위와 스냅샷 롤백 절차는
> [`merge-plan.md`](merge-plan.md)에 있다.

**같은 자원을 두 저장소에서 선언하지 않는다.** 특히 Lightsail의 공개 포트 API는 규칙
전체를 교체하므로(`put-instance-public-ports`), 여기서 방화벽을 따로 선언하면 나중에
apply한 쪽이 상대의 규칙을 통째로 지운다.

## 계약 값

| 계약 값 | 현재 | 소유 | 우리 쪽에서 쓰는 곳 |
|---|---|---|---|
| 리전 / AZ | `ap-northeast-1` / `ap-northeast-1a` | 호스트 | `server-ops.md` 0절 |
| 인스턴스 / 요금제 | `orca-host-tokyo` / `medium_3_0` | 호스트 | `server-ops.md` 0절 |
| 호스트 타임존 | `UTC` | 호스트 | 백업 cron 시각, `verify-app.sh` |
| 자동 스냅샷 | 매일 19:00 UTC | 호스트 | 앱 백업을 그 앞(18:00 UTC)에 끝낸다 |
| 공개 웹 80/443 | 호스트의 `enable_public_web` | 호스트 | Caddy → `127.0.0.1:8788` |
| 앱 계정 / 경로 | `stockbot` / `/srv/stock-chatbot` | 우리 | `scripts/lib.sh`, `systemd/` |
| 앱 내부 포트 | `127.0.0.1:8787`(관리), `:8788`(읽기 웹) | 우리 | 공인 방화벽에 열지 않는다 |
| 앱 데이터 백업 | 매일 18:00 UTC, 14일 보관 | 우리 | `systemd/stock-chatbot-backup.cron.tmpl` |

호스트 쪽 현재 값은 호스트 저장소에서 읽는다.

```powershell
terraform -chdir=<remote_coding>\terraform output -raw auto_snapshot_time_utc
terraform -chdir=<remote_coding>\terraform output -raw public_web_enabled
```

### 시각이 UTC인 이유

`cron.d`는 타임존을 선언할 수 없어 호스트 설정을 그대로 따르고, Lightsail 자동 스냅샷
시각도 UTC 정시다. 그래서 호스트를 UTC로 고정하고(호스트 저장소 `scripts/config.env`의
`HOST_TIMEZONE`), 백업 cron은 18:00 UTC(03:00 KST/JST)로 둔다 — 스냅샷보다 한 시간 앞이다.
**현지 시각이 중요한 스케줄은 cron이 아니라 systemd timer에 타임존을 직접 적는다**
(`stock-chatbot-polymarket-refresh.timer`, `polymarket-shorts.timer`는 `Asia/Seoul`).
앱의 '지금'은 `telegram_bot/core/clock.py`가 JST로 고정하므로 하루 경계는 호스트 타임존과
무관하다.

## 호스트 값이 필요할 때

우리가 직접 바꾸지 않고 호스트 저장소에서 바꾼다. 경계에서 만나는 지점은 둘뿐이다.

- **공개 웹**: 유효한 DNS와 Caddy가 준비된 뒤 호스트의 `enable_public_web = true`로
  80/443을 연다. 앱 내부 포트(8787·8788)는 어떤 경우에도 공인 방화벽에 열지 않는다.
  절차는 `server-ops.md` 11절.
- **자동 스냅샷**: 영속 데이터(`/srv/stock-chatbot/data`)가 스냅샷에 함께 담긴다.
  시각을 옮기면 우리 백업 cron 시각도 함께 옮긴다.

AWS 자격증명과 Lightsail IAM 정책도 호스트 저장소가 소유한다
(`remote_coding/terraform/iam-policy.json`). 앱 배포에는 SSH만 있으면 된다.

## 앱 설치와 점검

```bash
sudo /srv/stock-chatbot/infra/scripts/install-shared-host.sh   # 유닛·cron·venv 설치 (서비스는 기동하지 않는다)
sudo /srv/stock-chatbot/infra/scripts/verify-app.sh            # 앱 점검
```

설치 스크립트는 **서비스를 기동하지 않는다.** 같은 텔레그램 토큰으로 두 프로세스가
`getUpdates`를 치면 `Conflict: terminated by other getUpdates request`로 양쪽이 번갈아
죽는다. 기동은 "다른 곳의 봇 정지 → 여기서 `systemctl enable --now`" 순서로 사람이 한다.

호스트 자체(Orca·Tailscale·OS·방화벽) 점검은 호스트 저장소의 `scripts/util/verify-host.sh`가
맡는다. 서로의 내부는 검사하지 않는다.

일상 운영(접속·배포 갱신·설정 변경·실측·백업 복구·장애 대응)은
[`server-ops.md`](server-ops.md)다.
