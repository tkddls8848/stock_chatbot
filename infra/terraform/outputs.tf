output "instance_name" {
  description = "Lightsail 인스턴스 이름."
  value       = aws_lightsail_instance.this.name
}

output "public_ip" {
  description = "고정 IP. 재부팅해도 유지된다."
  value       = aws_lightsail_static_ip.this.ip_address
}

output "ssh_command" {
  description = "SSH 접속 명령."
  value       = "ssh ${var.app_user}@${aws_lightsail_static_ip.this.ip_address}"
}

output "web_admin_tunnel_command" {
  description = <<-EOT
    관리 웹 접근. 8787은 방화벽에서 열지 않으므로 반드시 이 터널을 쓴다.
    연결한 뒤 브라우저에서 http://127.0.0.1:8787 로 접속한다.
  EOT
  value       = "ssh -L 8787:127.0.0.1:8787 ${var.app_user}@${aws_lightsail_static_ip.this.ip_address}"
}

output "bootstrap_status_command" {
  description = "부트스트랩 진행 상황 확인. 최초 부팅 후 완료까지 5~10분 걸린다."
  value       = "ssh ${var.app_user}@${aws_lightsail_static_ip.this.ip_address} 'cat /var/lib/stock-chatbot/bootstrap-ok 2>/dev/null || sudo tail -30 /var/log/stock-chatbot-bootstrap.log'"
}

output "cutover_commands" {
  description = <<-EOT
    전환 절차. 부트스트랩은 봇을 기동하지 않으므로 이 단계는 사람이 순서대로 한다.
    순서를 바꾸면 같은 토큰으로 두 프로세스가 폴링해 양쪽이 번갈아 죽는다.
  EOT
  value       = <<-EOT
    # 0. 부트스트랩 완료 확인
    ssh ${var.app_user}@${aws_lightsail_static_ip.this.ip_address} 'cat /var/lib/stock-chatbot/bootstrap-ok'

    # 1. 서버 .env 작성 — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / ALLOWED_CHAT_IDS /
    #    CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN 을 채운다.
    #    WEB_ADMIN_PASSWORD는 부트스트랩이 무작위로 채워 두었다.
    ssh ${var.app_user}@${aws_lightsail_static_ip.this.ip_address}
    nano ${local.app_dir}/.env

    # 2. 설정 검증 — ConfigurationError 없이 끝나야 한다
    cd ${local.app_dir} && ./venv/bin/python -c "import sys; sys.path.insert(0,'app'); import core.config"

    # 3. 테스트가 로컬과 같은 결과인지 확인 (Python 3.12 비호환 조기 발견)
    #    부트스트랩은 실행 의존성만 설치하므로 pytest를 여기서 함께 받는다.
    cd ${local.app_dir} && ./venv/bin/pip install -r requirements-dev.txt
    cd ${local.app_dir} && ./venv/bin/python -m pytest -q

    # 4. 재생성되는 것만 빼고 data/ 전부를 이관 (로컬 PowerShell에서 실행)
    #    옮길 것을 나열하지 않고 뺄 것만 나열한다 — 기능이 늘 때마다 이 줄을
    #    고치는 걸 잊으면 그 기능의 이력이 조용히 사라진다.
    #    instruments는 /stockdb build로 재생성되고 runtime은 bot.lock뿐이다.
    #    나머지(signal_scoring 예측 로그, market_sentiment 다이제스트·폴리마켓
    #    스냅숏 포함)는 다시 만들 수 없거나 LLM 호출을 다시 태워야 한다.
    scp -r (Get-ChildItem data -Directory -Exclude instruments,runtime | ForEach-Object FullName) ${var.app_user}@${aws_lightsail_static_ip.this.ip_address}:${local.app_dir}/data/

    # 5. >>> 로컬 봇을 먼저 정지한다 <<<

    # 6. 서버 기동
    sudo systemctl enable --now ${var.service_name}
    journalctl -u ${var.service_name} -f
  EOT
}

output "verify_commands" {
  description = "Phase 5 검증. 기동 직후와 24시간 뒤에 확인한다."
  value       = <<-EOT
    # 기동 로그에 '봇 시작됨. 활성 기능: ...'
    journalctl -u ${var.service_name} --no-pager | tail -40

    # LLM 연결 — result=success 와 neurons= 값이 찍혀야 한다
    journalctl -u ${var.service_name} --no-pager | grep '\[LLM\]' | tail

    # 회로 차단 — 아무것도 안 나와야 정상
    journalctl -u ${var.service_name} --no-pager | grep circuit_open

    # 텔레그램에서: /system, /briefing morning, /market (차트 이미지 = MPLBACKEND 정상)

    # 최근 7일 UTC 일자별 Neurons 소모 (무료 한도 10,000/일, 예상 약 2,600/일)
    journalctl -u ${var.service_name} --since "$(date -u -d '6 days ago' '+%Y-%m-%d 00:00:00 UTC')" --until "$(date -u -d 'tomorrow' '+%Y-%m-%d 00:00:00 UTC')" -o short-iso-precise --utc --no-pager | awk 'match($0, /neurons=[0-9]+([.][0-9]+)?/) { day = substr($1, 1, 10); total[day] += substr($0, RSTART + 8, RLENGTH - 8) } END { for (day in total) printf "%s %.2f\n", day, total[day] }' | sort
  EOT
}

output "rollback_command" {
  description = "기동 실패·오작동 시 즉시 복구. 서버를 멈추고 로컬 봇을 다시 켠다."
  value       = "ssh ${var.app_user}@${aws_lightsail_static_ip.this.ip_address} 'sudo systemctl stop ${var.service_name}'"
}
