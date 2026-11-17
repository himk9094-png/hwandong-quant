#!/usr/bin/env bash
# hwandong-quant server helper (hq)
#  - GitHub's scheduled runs start 30-60 min late. This server presses "Run workflow" on time instead:
#      daily : Mon-Fri 08:37 KST      coin : every 4h at :11 (01,05,09,13,17,21 KST)
#    GitHub's own schedule stays as a backup and skips itself when the server already ran it.
#  - The GitHub token is typed here (hidden) and stored only on this server: /opt/hq/token (root only).
# Install / update:  curl -fsSL https://raw.githubusercontent.com/himk9094-png/hwandong-quant/main/server/install.sh -o hq.sh && sudo bash hq.sh
# Messages are in English because the Lightsail browser terminal shows Korean as boxes.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then exec sudo bash "$0" "$@"; fi
D=/opt/hq
mkdir -p "$D"; chmod 700 "$D"
command -v curl >/dev/null || { apt-get update -y && apt-get install -y curl; }
command -v python3 >/dev/null || { apt-get update -y && apt-get install -y python3; }

cat > /usr/local/bin/hq <<'__HQ__'
#!/usr/bin/env bash
# hq - hwandong-quant server helper
set -uo pipefail
D=/opt/hq
REPO=himk9094-png/hwandong-quant
API=https://api.github.com/repos/$REPO
UA="hq-server"
declare -A WF=([daily]=daily.yml [coin]=coin.yml [collect]=collect.yml)
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"     # token file is root-only

tok() { cat "$D/token" 2>/dev/null; }
gh() {  # gh METHOD PATH [JSON] -> prints body, last line = http code
  local m=$1 p=$2 b=${3:-}
  if [ -n "$b" ]; then
    curl -sS -m 30 -X "$m" -A "$UA" -H "Authorization: Bearer $(tok)" -H "Accept: application/vnd.github+json" \
         -H "X-GitHub-Api-Version: 2022-11-28" -d "$b" -w '\n%{http_code}' "$p"
  else
    curl -sS -m 30 -X "$m" -A "$UA" -H "Authorization: Bearer $(tok)" -H "Accept: application/vnd.github+json" \
         -H "X-GitHub-Api-Version: 2022-11-28" -w '\n%{http_code}' "$p"
  fi
}
topic() { curl -fsS -m 15 "https://raw.githubusercontent.com/$REPO/main/config.json" 2>/dev/null \
          | python3 -c 'import json,sys;print(json.load(sys.stdin).get("ntfy_topic",""))' 2>/dev/null; }
notify() { local t; t=$(topic); [ -n "$t" ] && curl -sS -m 15 -H "Title: $1" -H "Tags: warning" -d "$2" "https://ntfy.sh/$t" >/dev/null; }

check_token() {
  local T=$1 code
  code=$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -A "$UA" -H "Authorization: Bearer $T" "$API/actions/workflows")
  case $code in
    200) echo "OK - token works (repo + Actions)"; return 0;;
    401) echo "FAIL 401 - token is wrong or expired. Copy it again.";;
    403|404) echo "FAIL $code - token cannot see $REPO. Check: Repository access = Only select -> hwandong-quant, Actions = Read and write.";;
    *) echo "FAIL $code - network problem? try again";;
  esac
  return 1
}

cmd_token() {
  local T
  echo "Paste the GitHub token (starts with github_pat_). It will NOT show on screen. Then press Enter."
  read -rs T </dev/tty || T=""; echo
  T=$(printf '%s' "$T" | tr -d '[:space:]')
  [ -z "$T" ] && { echo "Nothing pasted. Run: hq token"; return 1; }
  check_token "$T" || return 1
  umask 077; printf '%s' "$T" > "$D/token"; chmod 600 "$D/token"
  echo "Saved to $D/token (root only)."
}

cmd_run() {  # hq run daily|coin|collect
  local k=${1:-} w out code i
  w=${WF[$k]:-}; [ -z "$w" ] && { echo "usage: hq run daily|coin|collect"; return 2; }
  [ -s "$D/token" ] || { echo "No token. Run: hq token"; return 1; }
  for i in 1 2 3; do
    out=$(gh POST "$API/actions/workflows/$w/dispatches" '{"ref":"main"}' 2>&1); code=${out##*$'\n'}
    if [ "$code" = 204 ]; then echo "$(date '+%F %T') $k started"; return 0; fi
    echo "$(date '+%F %T') $k try $i failed: HTTP $code $(echo "$out" | head -c 200)"
    sleep 20
  done
  notify "hq: $k start failed" "server could not start $w (HTTP $code). GitHub backup schedule will still run late. Check: hq check"
  return 1
}

cmd_check() {
  [ -s "$D/token" ] && check_token "$(tok)" || echo "No token yet. Run: hq token"
  echo "server time: $(date '+%F %T %Z')"
  systemctl list-timers 'hq-*' --no-pager | head -5
}

cmd_status() { cmd_check; echo; journalctl -u 'hq@*' -n 12 --no-pager -o cat 2>/dev/null | tail -12; }
cmd_log() { journalctl -u 'hq@*' -n "${1:-40}" --no-pager; }

# DART bulk download test (overseas IPs get an error page; this server is in Seoul).
# Result is saved to the 'server' branch so it can be read without screenshots.
cmd_darttest() {
  local W; W=$(mktemp -d); cd "$W" || return 1
  local U=https://opendart.fss.or.kr L=https://opendart.fss.or.kr/disclosureinfo/fnltt/dwld/list.do
  local C="curl -sS -m 120 -A Mozilla/5.0 -b jar -c jar -e $L"
  {
    echo "darttest v2 $(date '+%F %T %Z') ip=$(curl -s -m 10 https://checkip.amazonaws.com)"
    code=$($C -o list.html -w '%{http_code}' "$L")
    echo "list page: HTTP $code, $(wc -c < list.html) bytes, zips: $(grep -oE "[A-Za-z0-9_.-]+\.zip" list.html | sort -u | wc -l)"
    f=$(grep -oE "[0-9]{4}_[0-9]Q_BS_[0-9]+\.zip" list.html | sort -u | tail -1)
    echo "test file: $f"
    echo "--- page lines around first zip:"
    grep -n -m3 -B2 -A2 "\.zip" list.html | cut -c1-300
    echo "--- forms / scripts mentioning download:"
    grep -n -iE "<form|download|fl_nm|\.do" list.html | cut -c1-250 | head -25
    try() {  # try NAME curl-args...
      local n=$1; shift
      code=$($C -o "$n.bin" -w '%{http_code} %{content_type}' "$@")
      sz=$(wc -c < "$n.bin")
      if head -c 2 "$n.bin" | grep -q PK; then echo "[$n] OK ZIP $code $sz bytes"; return 0; fi
      echo "[$n] no: $code $sz bytes: $(tr -d '\r' < "$n.bin" | sed 's/<[^>]*>//g' | tr -s ' \n' ' ' | cut -c1-200)"
      return 1
    }
    try get   "$U/cmm/downloadFnlttZip.do?fl_nm=$f" || \
    try post  -X POST --data "fl_nm=$f" "$U/cmm/downloadFnlttZip.do" || \
    try post2 -X POST --data "fl_nm=$f&mode=" "$U/disclosureinfo/fnltt/dwld/download.do" || true
    for n in get post post2; do
      if [ -f "$n.bin" ] && head -c 2 "$n.bin" | grep -q PK; then
        python3 - "$n.bin" <<'__PY__' 2>&1 | head -12
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
for i in z.infolist()[:4]: print("  in zip:", i.filename, i.file_size)
head = z.open(z.infolist()[0]).read(700)
for enc in ("cp949", "utf-8"):
    try: print("  first lines:", head.decode(enc)[:500].replace("\t", " | ")); break
    except Exception: pass
__PY__
        break
      fi
    done
  } 2>&1 | tee report.txt
  report "darttest.txt" report.txt
  report "dart_list.html" list.html >/dev/null
  cd /; rm -rf "$W"
}

report() {  # report NAME FILE -> branch 'server' (needs Contents: Read and write)
  local name=$1 file=$2 out code sha b64 body
  [ -s "$D/token" ] || { echo "(no token: report not uploaded)"; return 0; }
  out=$(gh GET "$API/git/ref/heads/server"); code=${out##*$'\n'}
  if [ "$code" != 200 ]; then
    sha=$(gh GET "$API/git/ref/heads/main" | head -n -1 | python3 -c 'import json,sys;print(json.load(sys.stdin)["object"]["sha"])')
    gh POST "$API/git/refs" "{\"ref\":\"refs/heads/server\",\"sha\":\"$sha\"}" >/dev/null
  fi
  sha=$(gh GET "$API/contents/server/$name?ref=server" | head -n -1 | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("sha",""))
except Exception: print("")')
  b64=$(base64 -w0 "$file")
  body=$(python3 -c 'import json,sys;d={"message":"server report "+sys.argv[1],"branch":"server","content":sys.argv[2]}
if sys.argv[3]: d["sha"]=sys.argv[3]
print(json.dumps(d))' "$name" "$b64" "$sha")
  out=$(gh PUT "$API/contents/server/$name" "$body"); code=${out##*$'\n'}
  case $code in 200|201) echo "(report uploaded - Claude can read it now)";; *) echo "(report upload failed: HTTP $code - token needs Contents: Read and write)";; esac
}

case ${1:-status} in
  token) cmd_token;;
  run) shift; cmd_run "$@";;
  check) cmd_check;;
  status) cmd_status;;
  log) shift; cmd_log "$@";;
  darttest) cmd_darttest;;
  update) curl -fsSL "https://raw.githubusercontent.com/$REPO/main/server/install.sh?t=$(date +%s)" -o /tmp/hq.sh && bash /tmp/hq.sh;;
  *) echo "hq status | check | token | run daily|coin|collect | log | darttest | update";;
esac
__HQ__
chmod 755 /usr/local/bin/hq

cat > /etc/systemd/system/hq@.service <<'__U__'
[Unit]
Description=hwandong-quant: start GitHub workflow %i
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/bin/hq run %i
TimeoutStartSec=180
__U__
cat > /etc/systemd/system/hq-daily.timer <<'__U__'
[Unit]
Description=hwandong-quant daily (Mon-Fri 08:37 KST)
[Timer]
OnCalendar=Mon..Fri *-*-* 08:37:00 Asia/Seoul
AccuracySec=5s
Unit=hq@daily.service
[Install]
WantedBy=timers.target
__U__
cat > /etc/systemd/system/hq-coin.timer <<'__U__'
[Unit]
Description=hwandong-quant coin (every 4h at :11, 01/05/09/13/17/21 KST)
[Timer]
OnCalendar=*-*-* 00/4:11:00 UTC
AccuracySec=5s
Unit=hq@coin.service
[Install]
WantedBy=timers.target
__U__
systemctl daemon-reload
systemctl enable --now hq-daily.timer hq-coin.timer >/dev/null

if [ ! -s "$D/token" ]; then hq token || true; else echo "Keeping saved token (change: hq token)"; fi
echo
hq check || true
cat <<'__MSG__'

Done. Commands:
  hq status      timers + recent runs       hq check     token + timers
  hq token       paste a new token          hq run daily start daily now (test)
  hq darttest    test DART bulk download from this Seoul server
__MSG__
