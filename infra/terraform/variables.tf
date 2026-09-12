# ---------------------------------------------------------------------------
# 위치와 사양
# ---------------------------------------------------------------------------

variable "aws_region" {
  description = "Lightsail 리전. Polymarket의 한국 IP 지역 차단(451)을 피해 도쿄로 이전했다."
  type        = string
  default     = "ap-northeast-1"
}

variable "availability_zone" {
  description = "인스턴스 AZ. aws_region 하위여야 한다."
  type        = string
  default     = "ap-northeast-1a"
}

variable "bundle_id" {
  description = <<-EOT
    Lightsail 플랜. micro_3_0 = $7/월 (1GB / 2vCPU / 40GB SSD / 2TB 전송).
    nano_3_0($5, 512MB)은 pandas 임포트 피크를 못 버틴다.
    사용 가능한 목록: aws lightsail get-bundles --region <리전>
  EOT
  type        = string
  default     = "micro_3_0"
}

variable "blueprint_id" {
  description = <<-EOT
    OS 이미지. Ubuntu 24.04 LTS는 Python 3.12가 기본이며 코드에 3.13 전용 문법이 없다.
    사용 가능한 목록: aws lightsail get-blueprints --region <리전>
  EOT
  type        = string
  default     = "ubuntu_24_04"
}

# ---------------------------------------------------------------------------
# 이름
# ---------------------------------------------------------------------------

variable "instance_name" {
  description = "Lightsail 인스턴스 이름. 정적 IP·키페어 이름의 접두사로도 쓰인다."
  type        = string
  default     = "stock-chatbot"
}

variable "service_name" {
  description = "systemd 유닛 이름 (journalctl -u <이름>)."
  type        = string
  default     = "stock-chatbot"
}

variable "app_user" {
  description = "봇을 실행할 리눅스 계정. Ubuntu 블루프린트의 기본 계정은 ubuntu다."
  type        = string
  default     = "ubuntu"
}

variable "app_dir_name" {
  description = "홈 디렉터리 아래 체크아웃 경로 (/home/<app_user>/<app_dir_name>)."
  type        = string
  default     = "stock_chatbot"
}

# ---------------------------------------------------------------------------
# 소스 코드
# ---------------------------------------------------------------------------

variable "repo_url" {
  description = <<-EOT
    부트스트랩이 clone 할 저장소. 공개 저장소여야 한다.
    비공개면 clone이 실패하므로(부트스트랩은 자격증명을 갖지 않는다) SSH 접속 후
    배포 키를 등록하고 수동으로 clone 한다.
  EOT
  type        = string
  default     = "https://github.com/tkddls8848/stock_chatbot.git"
}

variable "repo_ref" {
  description = "체크아웃할 브랜치 또는 태그."
  type        = string
  default     = "main"
}

# ---------------------------------------------------------------------------
# 접근 제어
# ---------------------------------------------------------------------------

variable "ssh_public_key_path" {
  description = <<-EOT
    Lightsail에 등록할 SSH 공개키 파일 경로. 대응하는 개인키로 접속한다.
    `~`는 `pathexpand`가 홈 디렉터리로 바꾼다(Windows에서도 동작한다). 그래서
    사용자 계정명을 적을 필요가 없고, 기본값 그대로 두면 대부분 맞는다.
    README가 안내하는 키 종류(ed25519)와 같은 이름을 기본값으로 둔다.
  EOT
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "manage_firewall" {
  description = <<-EOT
    방화벽 규칙을 Terraform이 소유할지 여부.

    true  — Terraform이 규칙 전체를 소유한다. main.tf가 선언한 22·80·443만 열린다.
            **콘솔에서 손으로 바꾼 규칙은 다음 apply가 되돌린다.**
    false — Terraform이 방화벽을 건드리지 않고 콘솔에서 관리한다(기본값). 공개 웹의
            80/443은 이미 열려 있어야 하고, 관리 웹(8787)과 공개 웹 프로세스(8788)는
            **어떤 경우에도 열지 않는다** — 둘 다 127.0.0.1 바인딩이다.
            `aws lightsail get-instance-port-states`로 현재 상태를 확인한다.
  EOT
  type        = bool
  default     = false
}

variable "allowed_ssh_cidrs" {
  description = <<-EOT
    SSH(22)를 허용할 IPv4 CIDR 목록. manage_firewall = true 일 때만 적용된다.
    기본값 0.0.0.0/0은 전 세계 개방이므로 고정 회선이면 ["<내 IP>/32"]로 좁힌다.
    공개 웹의 80/443은 이 값과 무관하게 전 세계 개방이다(main.tf에 고정) — 열람
    제한은 Caddy의 Basic 인증이 맡는다. 관리 웹(8787)은 어떤 값으로도 열지 않는다
    — SSH 터널로만 접근한다.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = length(var.allowed_ssh_cidrs) > 0
    error_message = "allowed_ssh_cidrs가 비면 SSH로 접속할 수 없어 인스턴스가 잠긴다."
  }
}

# ---------------------------------------------------------------------------
# 부트스트랩 동작
# ---------------------------------------------------------------------------

variable "swap_size" {
  description = <<-EOT
    스왑 파일 크기. 1GB 인스턴스에서 pip install(pandas 빌드) 중 OOM이
    가장 흔한 실패 지점이라 패키지 설치 전에 먼저 잡는다.
  EOT
  type        = string
  default     = "2G"
}

variable "timezone" {
  description = "인스턴스 타임존. 앱의 '지금'은 core/clock.py가 JST로 고정하므로 하루 경계는 이 값과 무관하지만, 로그·journalctl 시각과 cron 백업 시각은 이 값을 따른다."
  type        = string
  default     = "Asia/Tokyo"
}

variable "backup_retention_days" {
  description = "~/backup-*.tgz 로컬 백업 보관 일수. 이보다 오래된 파일은 cron이 지운다."
  type        = number
  default     = 14
}

# ---------------------------------------------------------------------------
# 스냅샷
# ---------------------------------------------------------------------------

variable "enable_auto_snapshot" {
  description = "일 1회 자동 스냅샷. data/가 함께 들어가므로 사실상 백업이다."
  type        = bool
  default     = true
}

variable "auto_snapshot_time" {
  description = <<-EOT
    자동 스냅샷 시각. **UTC 기준이며 정시(HH:00)만 허용된다.**
    기본 19:00 UTC = 04:00 JST — 03:00 JST의 tar 백업이 끝난 뒤에 찍힌다.
  EOT
  type        = string
  default     = "19:00"

  validation {
    condition     = can(regex("^([01][0-9]|2[0-3]):00$", var.auto_snapshot_time))
    error_message = "auto_snapshot_time은 UTC 정시 HH:00 형식이어야 한다 (예: 19:00)."
  }
}

# ---------------------------------------------------------------------------
# 태그
# ---------------------------------------------------------------------------

variable "tags" {
  description = "모든 리소스에 붙일 태그."
  type        = map(string)
  default = {
    Project   = "stock-chatbot"
    ManagedBy = "terraform"
  }
}
