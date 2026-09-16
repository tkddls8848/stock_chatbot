# 공유 호스트 편입 기준

> 기준일: 2026-09-12
> 상태: **운영 전환 완료**. 독립 Lightsail `stock-chatbot`의 앱·설정·데이터를
> 도쿄 공유 호스트 `orca-host-tokyo`로 이전했다.

이 프로젝트는 인프라 비용 때문에 `orca-host-tokyo`를 다른 프로젝트와 **공유할 뿐**,
별개 프로젝트다. 호스트 자체(인스턴스·고정 IP·공인 방화벽·스냅샷·Orca·Tailscale)는
`remote_coding/remote-lightsail`이 소유하고, 이 문서를 포함한 **앱 운영 일체는 이
저장소가 소유한다.** 호스트 저장소는 우리 경로·포트·권한을 알지 못한다.

## 현재 배치

| 대상 | 위치·상태 |
|---|---|
| 공유 Lightsail | `orca-host-tokyo`, `ap-northeast-1a`, `medium_3_0` |
| 호스트 소유 | `remote_coding/remote-lightsail/terraform/` (별개 프로젝트) |
| 운영 앱 | `/srv/stock-chatbot`, `stockbot:stockbot` |
| 운영 비밀 | `/srv/stock-chatbot/.env`, `0600`, Git/Orca workspace 밖 |
| 봇 | `stock-chatbot.service`, enabled/active |
| 읽기 웹 | `stock-chatbot-web.service`, `127.0.0.1:8788` |
| 관리 웹 | `127.0.0.1:8787`, 공인 방화벽 비공개 |
| 예약 작업 | `stock-chatbot-polymarket-refresh.timer` |
| 앱 백업 | `/var/backups/stock-chatbot`, 매일 18:00 UTC(03:00 KST/JST), 14일 보관 |
| Lightsail 스냅샷 | 매일 19:00 UTC(04:00 KST/JST) |

유닛과 백업 cron은 `infra/systemd/`, 공유 호스트 설치기는
`infra/scripts/install-shared-host.sh`, 앱 점검은 `infra/scripts/verify-app.sh`,
운영 절차는 `infra/server-ops.md`다. 개발 클론
`/home/orca/workspace/stock_chatbot`과 운영 체크아웃 `/srv/stock-chatbot`은 분리하며,
운영 `.env`와 데이터 원본을 개발 workspace에 복사하지 않는다.

## 네트워크

- Orca는 Tailscale Serve HTTPS로만 접근하고 6768을 공개하지 않는다.
- 8787/8788은 루프백에만 바인딩하며 Lightsail 공인 방화벽에 열지 않는다.
- 공개 읽기 웹이 필요할 때만 유효한 DNS와 Caddy를 준비한 뒤, 호스트 저장소
  Terraform의 `enable_public_web=true`로 80/443을 연다. 이것과 자동 스냅샷이
  두 프로젝트가 만나는 **유일한 두 접점**이다.
- 2026-09-12 현재 `nunchi.live`가 공용 DNS에서 NXDOMAIN이므로 Caddy와 80/443은 꺼 둔다.

## 전환 기록

- 원본 `stock-chatbot`과 대상 `orca-host-tokyo`의 전환 전 수동 스냅샷을 생성했다.
- 원본 서비스를 정지한 뒤 최종 아카이브를 전송하고 SHA-256을 대조했다.
- 서버 로컬의 Git 변경사항, `.env`, `data/`, 고정 의존성 목록을 그대로 보존했다.
- 대상에서 전체 테스트 `459 passed, 6 skipped, 5 xfailed`를 통과했다.
- 봇·웹·예약 작업을 기동했고 Polymarket 갱신 및 섹터 브리프 생성까지 확인했다.
- 대상 전환 후 스냅샷 `orca-host-post-stock-merge-20260912`를 생성했다.

## 기존 인스턴스 폐기 (2026-09-12 완료)

관망 후 기존 `stock-chatbot` 인스턴스, 고정 IP `stock-chatbot-ip`(176.34.3.200),
키페어 `stock-chatbot-key`, 자동 스냅샷 `stock-chatbot-1788603915`를 모두 폐기했다.
도쿄 리전에 남은 Lightsail 인스턴스는 `orca-host-tokyo` 하나다.

롤백 자료로 스냅샷 `stock-chatbot-pre-merge-20260912`(40GB)만 남겼다. **인스턴스가
없으므로 롤백은 "구 호스트에서 서비스 재기동"이 아니라 이 스냅샷으로 새 인스턴스를
만드는 절차다.** 스냅샷을 지우면 전환 이전 상태로 돌아갈 방법이 사라진다.

이 폐기된 인스턴스를 만들던 `infra/terraform/`은 삭제했다(정의는 git 이력에 남아 있다).
공유 호스트는 호스트 저장소가 소유하며, 우리 쪽 계약 값은 `infra/host-contract.md`에 있다.

## 점검

```bash
sudo /srv/stock-chatbot/infra/scripts/verify-app.sh
```

호스트 쪽(Orca·Tailscale·OS)은 호스트 저장소의 `verify-host.sh`가 따로 본다.

## 롤백

1. 공유 호스트의 `stock-chatbot`, 웹, refresh timer를 stop/disable한다.
2. 전환 후 공유 호스트에 쌓인 새 데이터의 보존·병합 여부를 먼저 결정한다.
   스냅샷 복원본은 2026-09-12 전환 시점 데이터이므로 그 뒤 변경분은 여기서만 얻는다.
3. 스냅샷 `stock-chatbot-pre-merge-20260912`로 새 인스턴스를 만든다.
   ```bash
   aws lightsail create-instances-from-snapshot --region ap-northeast-1 \
     --instance-names stock-chatbot-rollback \
     --instance-snapshot-name stock-chatbot-pre-merge-20260912 \
     --availability-zone ap-northeast-1a --bundle-id micro_3_0
   ```
4. 고정 IP와 방화벽을 새로 붙이고, 복원본에서 필요한 서비스 하나만 enable/start한다.
5. Telegram/API 기능을 확인한다. 같은 토큰의 두 롱폴링 프로세스를 동시에 실행하지 않는다.
