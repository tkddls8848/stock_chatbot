# Lightsail 배포 (Terraform)

> **2026-09-12 운영 변경:** 이 디렉터리의 독립 Lightsail은 신규 배포에 사용하지 않는다.
> 앱은 `orca-host-tokyo`의 `/srv/stock-chatbot`으로 이전됐고, 인스턴스·고정 IP·방화벽·자동
> 스냅샷은 `C:/Users/PSI/orca/remote_coding/remote-lightsail/terraform`이 소유한다.
> 기존 `stock-chatbot` 인스턴스·고정 IP·키페어는 2026-09-12에 폐기했고 state도 정리됐으므로
> 이곳에서 `terraform apply` 또는 `destroy`하지 않는다. 아래 절차는 역사적 기록으로만 읽는다.
> 롤백이 필요하면 스냅샷 `stock-chatbot-pre-merge-20260912`로 새 인스턴스를 만든다 (`infra/merge-plan.md`).
> 앱 유닛은 `infra/systemd/`, 공유 호스트 설치는 `infra/scripts/install-shared-host.sh`를 쓴다.

Lightsail 인프라 생성, 초기화, 서비스 전환 절차를 한곳에 정리한 운영 문서다.

## 이 코드가 하는 일 / 하지 않는 일


|                                                       | 담당                                       |
| ----------------------------------------------------- | ---------------------------------------- |
| Lightsail 인스턴스·고정 IP·SSH 키페어·방화벽(22·80·443)            | **Terraform**                            |
| 스왑 2G, 타임존 JST, apt 패키지, git clone, venv, pip install | **user_data 부트스트랩**                      |
| systemd 유닛 설치, 백업 cron, 자동 스냅샷                        | **user_data 부트스트랩**                      |
| `.env` 작성(토큰·자격증명)                                    | 사람 (SSH)                                 |
| `data/` 이관, 로컬 봇 정지, 서비스 기동                           | 사람 (`terraform output cutover_commands`) |
| 도메인 연결, Caddy 설치·인증서, 공개 웹 노출                        | 사람 (`infra/server-ops.md` 11절)           |


**부트스트랩은 봇을 기동하지 않는다.** `data/runtime/bot.lock`은 머신 단위라 다른
호스트의 중복 기동을 막지 못하고, 같은 토큰으로 두 프로세스가 `getUpdates`를 치면
텔레그램이 `Conflict: terminated by other getUpdates request`를 반환해 **양쪽이 번갈아
죽는다.** 전환은 반드시 "로컬 정지 → 서버 기동" 순서로 사람이 한다.

## 사전 준비

1. Terraform >= 1.5, AWS 자격증명 (`aws configure` 또는 `AWS_PROFILE`)
2. **IAM 사용자에게 Lightsail 권한.** `AmazonLightsailFullAccess` 같은 AWS 관리형
 정책은 **존재하지 않으므로** 직접 만들어 붙여야 한다. EC2 권한이 있어도 Lightsail은
 별개 서비스라 거부된다.
  ```powershell
   cd iac\terraform   # file:// 경로는 이 디렉터리 기준이다. 저장소 루트에서는 파일을 못 찾는다
   aws iam create-policy --policy-name StockChatbotLightsail --policy-document file://iam-policy.json
   aws iam attach-user-policy --user-name <내 IAM 사용자> --policy-arn <위 출력의 Arn>
   aws lightsail get-bundles --region ap-northeast-1 --query 'bundles[?bundleId==`micro_3_0`]'   # 확인
  ```

   `iam-policy.json`은 이 디렉터리에 있고, 운영 리전인 도쿄
   (`ap-northeast-1`)만 허용한다.

   **이미 붙어 있는 정책을 고칠 때는 `create-policy`가 아니라 새 버전을 만든다**
   (관리형 정책은 버전이 있고 5개가 차면 오래된 버전부터 지워야 한다):
   ```powershell
   $arn = (aws iam list-policies --query "Policies[?PolicyName=='StockChatbotLightsail'].Arn" --output text)
   aws iam create-policy-version --policy-arn $arn --policy-document file://iam-policy.json --set-as-default
   ```
   기존 정책에도 이 파일을 반영해야 리전 조건이 도쿄 하나로 좁혀진다.

   **root 자격증명으로는 붙일 대상이 없다.** `aws sts get-caller-identity`의 `Arn`이
   `:root`로 끝나면 IAM 사용자가 아니라는 뜻이고, `attach-user-policy`에 계정 이메일을
   넣으면 `NoSuchEntity`가 난다. 콘솔 IAM → 사용자에서 사용자를 만들어 위 정책을 붙이고,
   그 사용자의 액세스 키로 `aws configure`를 다시 한다. root 액세스 키는 유출되면
   결제·계정 폐쇄까지 열리므로 만들지 않는다.

   `InvalidClientTokenId`는 자격증명 자체가 무효라는 신호다(키 삭제·비활성·오타).
   `aws configure list`로 어느 파일의 어떤 키가 먹고 있는지부터 확인한다.
3. **SSH 키.** 없으면 만든다. 개인키는 로컬에만 남고, Terraform은 공개키만 등록한다.
  ```powershell
   ssh-keygen -t ed25519 -C stock-chatbot-lightsail
   Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub"   # ssh-ed25519 AAAA... 로 시작
  ```

   위 이름 그대로 만들었으면 **`ssh_public_key_path`는 손대지 않아도 된다.**
   기본값 `~/.ssh/id_ed25519.pub`의 `~`를 `pathexpand`가 홈 디렉터리로 바꾸므로
   Windows 계정명을 적을 필요가 없다. 키 이름이 다를 때만 고치고, 굳이 절대경로를
   쓴다면 **Windows 경로는 슬래시로 쓴다** (`"C:/Users/이름/.ssh/id_ed25519.pub"`).
4. **저장소가 공개인지 확인.** 비공개면 부트스트랩의 clone이 실패한다 —
 SSH 접속 후 배포 키를 등록하고 4절부터 수동으로 이어서 실행한다.
5. 로컬 작업이 전부 커밋·푸시되어 있을 것. 안 하면 서버가 옛 코드를 받는다.

```powershell
git status --short          # 비어 있어야 한다
git rev-list --count origin/main..HEAD   # 0이어야 한다
```

## 실행

현재 운영 서버는 `default` workspace에 있고 `terraform.tfvars`(gitignore됨)를 사용한다.
리전과 AZ의 기준값은 도쿄(`ap-northeast-1`, `ap-northeast-1a`)다.

```powershell
cd iac\terraform
terraform init
terraform workspace select default
terraform plan
terraform apply
```

`terraform.tfvars`는 옵션을 주지 않아도 자동 로드된다. 새 환경을 처음 만들 때는
`terraform.tfvars.example`을 `terraform.tfvars`로 복사하고 SSH 키 경로와 접근 CIDR을
확인한다.

apply는 1~~2분이면 끝나지만 **부트스트랩은 그 뒤로 5~~10분 더 걸린다**(pip이 pandas를
빌드한다). 진행 상황:

```powershell
terraform output -raw bootstrap_status_command   # 명령을 복사해 실행
```

`/var/lib/stock-chatbot/bootstrap-ok`에 시각이 찍히면 완료다. 안 나오면
`sudo tail -50 /var/log/stock-chatbot-bootstrap.log`에 실패 지점이 있다.

**그 로그 파일조차 없으면** 스크립트가 로그를 열기 전에 죽은 것이다. 그때는
`sudo tail -60 /var/log/cloud-init-output.log`를 본다 — cloud-init이 실행한 모든
출력이 여기 남는다. 실패한 부트스트랩은 인스턴스를 다시 만들지 않고 이렇게 잇는다:

```bash
sudo sed -n '/^#!\/bin\/sh$/,$p' /var/lib/cloud/instance/scripts/part-001 | sudo tee /tmp/bootstrap.sh >/dev/null
sudo sh /tmp/bootstrap.sh
```

Lightsail이 앞에 붙인 초기화 부분(SSH CA 등록·sshd 재시작)을 빼고 우리 스크립트만
돌린다. 그 앞부분을 같이 재실행하면 SSH가 끊길 수 있다.

## 전환

```powershell
terraform output cutover_commands   # 0~6단계 그대로 따라간다
terraform output verify_commands    # 기동 후 검증
```

전환 직전에 수동 스냅샷을 하나 찍어 두면 되돌릴 지점이 생긴다.

## 관리 웹

8787은 **방화벽에서 열지 않는다.** HTTP Basic 인증뿐이라 평문으로 노출된다.

```powershell
terraform output -raw web_admin_tunnel_command   # 실행 후 http://127.0.0.1:8787
```

비밀번호는 부트스트랩이 무작위로 생성해 서버 `.env`에 넣었다:

```bash
grep WEB_ADMIN_PASSWORD ~/stock_chatbot/.env
```

## 알아둘 것

**`user_data` 변경은 무시된다.** Terraform은 `user_data`가 바뀌면 인스턴스를
재생성하는데, 이 디스크에는 재구축 비용이 큰 `data/`가 들어 있다. 그래서
`lifecycle { ignore_changes = [user_data] }`로 막아 두었다. 부트스트랩 스크립트를
실제로 다시 적용하려면 SSH로 해당 단계를 직접 실행하거나, 데이터 유실을 감수하고
`terraform taint aws_lightsail_instance.this` 후 apply 한다.

**이름 변수를 바꾸면 영향 범위가 셋으로 갈린다.** 2026-08-08 리브랜딩
(`china-chatbot` → `stock-chatbot`) 때 실측한 구분이다.


| 바뀐 것                                     | 결과                                                                                                             |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `instance_name`                          | **인스턴스 교체.** 정적 IP·키페어 이름의 접두사라 파생 리소스도 함께 재구성된다. 기존 배포가 있으면 기본값만 바꾸지 말고 `terraform.tfvars`에 옛 값을 명시해 고정한다.    |
| `tags`                                   | in-place 태그 갱신. `main.tf`에는 참조가 없지만 `versions.tf`의 프로바이더 `default_tags`가 소비하므로 **실제로 적용된다** — 참조가 없다고 오해하기 쉽다. |
| `service_name`·`app_dir_name`·`repo_url` | 신규 부팅에만 반영된다. `user_data` 안에서만 쓰이는데 그 변경이 아래처럼 무시되기 때문이다.                                                      |


그래서 **이미 떠 있는 서버의 이름 변경은 Terraform이 해주지 않는다.** SSH로 직접:
스냅샷 → `~/<옛 이름>`을 새 이름으로 이동 → 옛 서비스 중지·비활성화 후 새 이름으로
유닛 설치·`daemon-reload`·`enable --now` → `/var/lib/<이름>` 마커와
`/var/log/<이름>-bootstrap.log`, `/etc/cron.d/<이름>-backup` 교체 → 검증 후 옛 서비스·cron 제거.

**방화벽 리소스는 규칙 전체를 대체한다.** `main.tf`가 선언한 22·80·443만 열리고
나머지는 닫힌다. 80/443은 공개 웹(Caddy) 몫이고, 관리 웹 8787과 공개 웹 프로세스
8788은 어느 쪽도 열지 않는다 — 둘 다 `127.0.0.1` 바인딩이다. **콘솔이나 CLI로 손대면
다음 `apply`가 되돌린다.** 그래서 도쿄 배포는 `manage_firewall = false`로 두고 포트를
CLI로 바꾼다:

```powershell
aws lightsail get-instance-port-states --region ap-northeast-1 --instance-name stock-chatbot
```

`stock-chatbot-deployer` IAM은 `ap-northeast-1`에서 포트 개폐가 허용된다
(2026-08-26 실측). 예전 주석의 `AccessDenied`는 `iam-policy.json`이 리전을
`ap-northeast-2`로 걸어 두었을 때의 이야기다.

**`bundle_id` / `blueprint_id`는 리전마다 다를 수 있다.** 값이 거부되면 확인한다:

```powershell
aws lightsail get-bundles --region ap-northeast-1
aws lightsail get-blueprints --region ap-northeast-1
```

**부트스트랩은 `pytest`를 설치하지 않는다.** `requirements.txt`만 설치하는데
(`user_data.sh.tftpl`), `cutover_commands`의 3단계와 폴리마켓 스모크는 pytest를 쓴다.
`No module named pytest`가 나오면 `./venv/bin/pip install -r requirements-dev.txt`로
먼저 깐다.

**정지해도 과금은 멈추지 않는다.** Lightsail은 running과 stopped 모두 과금한다.
실제로 멈추려면 스냅샷 → `terraform destroy` 순서로 삭제해야 한다. 고정 IP도 함께
사라져야 한다 — 인스턴스에서 분리된 채 1시간이 지나면 $0.005/시간이 붙는다.
`terraform destroy`는 둘 다 지우므로 이 문제는 생기지 않는다.

**`requirements.lock.txt`.** `requirements.txt`에 버전 핀이 거의 없어서, 부트스트랩이
설치 직후 `pip freeze` 결과를 `~/stock_chatbot/requirements.lock.txt`에 남긴다.
서버에서만 재현되는 문제를 쫓을 때 이 파일이 유일한 근거다.

## 갱신과 삭제

서버는 clone한 브랜치(`main`)를 pull한다. **작업 브랜치에만 커밋해 두면 서버는 옛
코드를 받는다** — 먼저 `main`에 병합하고 push한다.

```bash
# 코드 갱신 (서버에서)
cd ~/stock_chatbot && git pull
./venv/bin/pip install -r requirements.txt      # requirements 변경 시에만
sudo systemctl restart stock-chatbot
```

```powershell
# 인프라 삭제 — data/는 함께 사라진다. 먼저 스냅샷을 찍는다.
terraform destroy
```
