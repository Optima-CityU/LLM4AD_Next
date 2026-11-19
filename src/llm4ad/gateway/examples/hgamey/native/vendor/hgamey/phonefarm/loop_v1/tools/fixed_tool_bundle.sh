# Frozen host-side template. Executed through supervised stdin, never from the remote directory.
set -eu
guard() {
  [ ! -e "$D/session.closed" ] && [ ! -L "$D/session.closed" ] || exit 86
  [ -f "$D/owner" ] && [ ! -L "$D/owner" ] || exit 87
  owner_size=$(wc -c < "$D/owner") || exit 87
  set -- $owner_size
  [ "$#" -eq 1 ] && [ "$1" -eq "$((${#TOKEN}+${#BOOT}+2))" ] || exit 87
  actual_owner=$(cat "$D/owner") || exit 87
  [ "$actual_owner" = "$TOKEN $BOOT" ] || exit 87
  actual_boot=$(cat /proc/sys/kernel/random/boot_id) || exit 88
  [ "$actual_boot" = "$BOOT" ] || exit 88
}
prepare_install() {
  command -v base64 >/dev/null && command -v wc >/dev/null && command -v sha256sum >/dev/null || exit 89
  [ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT" ] || exit 88
  umask 077
  mkdir "$D" || exit 90
  (set -C; printf '%s\n' "$TOKEN $BOOT" > "$D/owner") || exit 90
  sync
  guard
  n=0
}
write_fixed() {
  name=$1; expected_size=$2
  guard
  [ ! -e "$D/$name" ] && [ ! -L "$D/$name" ] || exit 90
  temporary="$D/.bundle-$name"
  [ ! -e "$temporary" ] && [ ! -L "$temporary" ] || exit 90
  (set -C; base64 -d > "$temporary") || exit 91
  guard
  [ -f "$temporary" ] && [ ! -L "$temporary" ] || exit 91
  size=$(wc -c < "$temporary") || exit 91
  set -- $size
  [ "$#" -eq 1 ] && [ "$1" = "$expected_size" ] || exit 91
  sync
  guard
  [ ! -e "$D/$name" ] && [ ! -L "$D/$name" ] || exit 90
  mv "$temporary" "$D/$name" || exit 91
  n=$((n+1))
}
finish_install() {
  guard
  sync
  printf 'INSTALL_COMPLETE\t%s\t%s\t%s\t%s\n' "$TOKEN" "$actual_boot" "$BUNDLE" "$n"
}
enable_reader() {
  guard
  [ -f "$D/render_reader.elf" ] && [ ! -L "$D/render_reader.elf" ] || exit 93
  chmod 700 "$D/render_reader.elf" || exit 93
  sync
  guard
  verify_reader
}
verify_reader() {
  guard
  [ -f "$D/render_reader.elf" ] && [ ! -L "$D/render_reader.elf" ] && [ -x "$D/render_reader.elf" ] || exit 93
}
prepare_verify() {
  command -v wc >/dev/null && command -v sha256sum >/dev/null || exit 89
  guard
  printf '#phonefarm_fixed_tools_verification v1\nOWNER\t%s\nBOOT\t%s\nBUNDLE\t%s\n' "$actual_owner" "$actual_boot" "$BUNDLE"
  n=0
}
verify_fixed() {
  name=$1; expected_size=$2
  guard
  [ -f "$D/$name" ] && [ ! -L "$D/$name" ] || exit 92
  size=$(wc -c < "$D/$name") || exit 92
  set -- $size
  [ "$#" -eq 1 ] || exit 92
  size=$1
  [ "$size" = "$expected_size" ] || exit 92
  digest=$(sha256sum "$D/$name") || exit 92
  digest=${digest%% *}
  printf 'FILE\t%s\t%s\t%s\n' "$name" "$size" "$digest"
  n=$((n+1))
}
finish_verify() {
  guard
  printf 'COUNT\t%s\n#end\n' "$n"
}
