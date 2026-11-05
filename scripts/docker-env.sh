# 왜: docker CLI가 사용자 전역 ~/.docker 설정(자격 증명 도우미, 컨텍스트)에 기대면 사람마다 결과가 달라진다. Docker 공식 DOCKER_CONFIG로 설정 디렉터리를 리포 안으로 돌린다.
# 규칙(한 곳): DOCKER_CONFIG가 이미 주어지면 그대로. 없으면 리포 build/docker-config의 빈 설정({}). 사용자 ~/.docker는 이 스크립트가 읽지도 고치지도 않는다.
# 컨텍스트 엔드포인트: DOCKER_CONFIG를 바꾸기 전에 docker CLI에게 현재 컨텍스트의 엔드포인트만 물어(CLI가 읽는다), 기본 소켓이 아니면 DOCKER_HOST로 넘긴다
# (설정을 바꾸면 컨텍스트 선택도 사라지므로 같은 서버를 계속 쓰게 하려는 것이다). 사용: source scripts/docker-env.sh  (여러 번 source해도 같다)
_mr_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${DOCKER_CONFIG:-}" ]; then
  if [ -z "${DOCKER_HOST:-}" ]; then
    _mr_host="$(${DOCKER:-docker} context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)"
    case "$_mr_host" in unix:///var/run/docker.sock|"") ;; unix://*|tcp://*|ssh://*) export DOCKER_HOST="$_mr_host" ;; esac
  fi
  mkdir -p "$_mr_root/build/docker-config"
  [ -f "$_mr_root/build/docker-config/config.json" ] || echo '{}' > "$_mr_root/build/docker-config/config.json"
  export DOCKER_CONFIG="$_mr_root/build/docker-config"
fi
unset _mr_root _mr_host
