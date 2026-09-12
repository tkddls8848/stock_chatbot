locals {
  app_dir = "/home/${var.app_user}/${var.app_dir_name}"

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    app_user              = var.app_user
    app_dir               = local.app_dir
    repo_url              = var.repo_url
    repo_ref              = var.repo_ref
    service_name          = var.service_name
    swap_size             = var.swap_size
    timezone              = var.timezone
    backup_retention_days = var.backup_retention_days
  })
}

# ---------------------------------------------------------------------------
# SSH 키페어
#
# Lightsail 키페어는 EC2의 aws_key_pair와 별개 리소스다. 개인키는 Terraform이
# 만들지 않고 기존 공개키를 등록만 하므로 tfstate에 비밀이 남지 않는다.
# ---------------------------------------------------------------------------

resource "aws_lightsail_key_pair" "this" {
  name       = "${var.instance_name}-key"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

# ---------------------------------------------------------------------------
# 인스턴스
#
# Lightsail Containers가 아니라 Instance를 쓴다. Containers에는 영속 디스크가
# 없어 재배포마다 data/(관심종목·뉴스·리서치 상태)가 사라진다.
# ---------------------------------------------------------------------------

resource "aws_lightsail_instance" "this" {
  name              = var.instance_name
  availability_zone = var.availability_zone
  blueprint_id      = var.blueprint_id
  bundle_id         = var.bundle_id
  key_pair_name     = aws_lightsail_key_pair.this.name
  ip_address_type   = "ipv4"
  user_data         = local.user_data

  dynamic "add_on" {
    for_each = var.enable_auto_snapshot ? [1] : []

    content {
      type          = "AutoSnapshot"
      snapshot_time = var.auto_snapshot_time
      status        = "Enabled"
    }
  }

  lifecycle {
    # user_data는 최초 부팅에만 실행되는데 Terraform은 값이 바뀌면 인스턴스를
    # 재생성한다. 이 인스턴스의 디스크에는 재구축 비용이 큰 data/가 들어 있으므로
    # 부트스트랩 스크립트 수정이 조용히 인스턴스를 날리지 않도록 막는다.
    # 스크립트를 실제로 다시 적용하려면 SSH로 직접 실행하거나, 의도적으로
    # `terraform taint aws_lightsail_instance.this` 후 apply 한다(데이터 유실 주의).
    ignore_changes = [user_data]
  }
}

# ---------------------------------------------------------------------------
# 고정 IP
#
# 인스턴스에 연결되어 있는 동안은 무료다. 인스턴스를 지우고 IP만 남기면
# 1시간 뒤부터 $0.005/시간(약 $3.6/월)이 붙으므로 중단할 때 함께 해제한다.
# ---------------------------------------------------------------------------

resource "aws_lightsail_static_ip" "this" {
  name = "${var.instance_name}-ip"
}

resource "aws_lightsail_static_ip_attachment" "this" {
  static_ip_name = aws_lightsail_static_ip.this.name
  instance_name  = aws_lightsail_instance.this.name
}

# ---------------------------------------------------------------------------
# 방화벽
#
# 이 리소스는 인스턴스의 공개 포트 규칙 전체를 대체한다. 여기 없는 포트는 닫히므로
# 공개 웹이 쓰는 80/443을 함께 선언한다. 80은 ACME HTTP-01 검증과 https 리다이렉트
# 전용이고 실제 열람은 443이다 — Caddy의 자동 HTTPS가 둘 다 처리한다.
# 관리 웹(8787)과 공개 웹 프로세스(8788)는 여기에 절대 넣지 않는다. 8787은 HTTP
# Basic뿐이라 평문 노출되고, 8788은 인증이 없다(TLS와 인증은 앞단 Caddy가 맡는다).
# 둘 다 127.0.0.1에만 바인딩되며 8787 접근은 SSH 터널로만 한다
# (outputs의 web_admin_tunnel_command 참조).
#
# 방화벽을 Lightsail 콘솔에서 직접 관리하려면 manage_firewall = false 로 둔다.
# 그러지 않으면 콘솔에서 좁힌 규칙을 다음 apply가 var.allowed_ssh_cidrs 로 되돌린다.
# ---------------------------------------------------------------------------

resource "aws_lightsail_instance_public_ports" "this" {
  count = var.manage_firewall ? 1 : 0

  instance_name = aws_lightsail_instance.this.name

  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
    cidrs     = var.allowed_ssh_cidrs
  }

  port_info {
    protocol  = "tcp"
    from_port = 80
    to_port   = 80
    cidrs     = ["0.0.0.0/0"]
  }

  port_info {
    protocol  = "tcp"
    from_port = 443
    to_port   = 443
    cidrs     = ["0.0.0.0/0"]
  }
}
