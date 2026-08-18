#!/usr/bin/env bash
# Rebrand a termux (com.termux) .deb -> vheditor (vn.vhn.vsc), faithfully, via ar/tar.
# Relies on com.termux and vn.vhn.vsc being EQUAL LENGTH (10) so binary byte-replacement
# preserves every offset (no rebuild/patchelf). Avoids dpkg-deb -b validation which rejects
# termux's Architecture: x86_64 and conffiles.
set -euo pipefail
IN="$(readlink -f "$1")"; OUT="$(readlink -f "$2")"
OLD="com.termux"; NEW="vn.vhn.vsc"
[ "${#OLD}" -eq "${#NEW}" ] || { echo "length guard failed"; exit 3; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
cd "$W"; ar x "$IN"
CTRL="$(ls control.tar.* )"; DATA="$(ls data.tar.*)"
dec() { case "$1" in *.xz) xz -dc "$1";; *.gz) gzip -dc "$1";; *.zst) zstd -dc "$1";; *) cat "$1";; esac; }
enc() { case "$1" in *.xz) xz -9;; *.gz) gzip -9;; *.zst) zstd -19 -q;; *) cat;; esac; }
byterepl() { OLD="$OLD" NEW="$NEW" python3 - "$1" <<'PY'
import os,sys,io,gzip,lzma,bz2
root=sys.argv[1]; old=os.environ['OLD'].encode(); new=os.environ['NEW'].encode()
assert len(old)==len(new)  # equal length keeps every offset (binaries) safe
for dp,_,fs in os.walk(root):
  for fn in fs:
    p=os.path.join(dp,fn)
    if os.path.islink(p): continue
    d=open(p,'rb').read()
    # 1) raw byte-swap (binaries, text, configs): equal length -> layout preserved
    if old in d:
      d2=d.replace(old,new); assert len(d2)==len(d); open(p,'wb').write(d2); continue
    # 2) reach into compressed members (e.g. gzipped man/info pages) the raw swap can't see
    try:
      if fn.endswith('.gz'):
        u=gzip.decompress(d)
        if old in u:
          buf=io.BytesIO()
          with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as g: g.write(u.replace(old,new))
          open(p,'wb').write(buf.getvalue())
      elif fn.endswith('.xz'):
        u=lzma.decompress(d)
        if old in u: open(p,'wb').write(lzma.compress(u.replace(old,new)))
      elif fn.endswith('.bz2'):
        u=bz2.decompress(d)
        if old in u: open(p,'wb').write(bz2.compress(u.replace(old,new)))
    except Exception:
      pass  # not really compressed / unreadable -> leave as-is
PY
}

# ---- data.tar ----
mkdir data; dec "$DATA" | tar -C data -xf -
[ -d "data/data/data/$OLD" ] && { mkdir -p "data/data/data/$NEW"; cp -a "data/data/data/$OLD/." "data/data/data/$NEW/"; rm -rf "data/data/data/$OLD"; }
byterepl data
( cd data && tar --owner=0 --group=0 --numeric-owner --sort=name -cf - . | enc "$DATA" > "$W/$DATA.new" )

# ---- control.tar ---- (regen md5sums from rebranded data, then byte-replace metadata)
mkdir ctrl; dec "$CTRL" | tar -C ctrl -xf -
byterepl ctrl
if [ -e ctrl/md5sums ] || [ -e ctrl/./md5sums ]; then
  ( cd data && find . -type f | sed 's|^\./||' | LC_ALL=C sort | while read -r f; do md5sum "$f"; done ) > ctrl/md5sums
fi
( cd ctrl && tar --owner=0 --group=0 --numeric-owner --sort=name -cf - . | enc "$CTRL" > "$W/$CTRL.new" )

# ---- assemble .deb (order: debian-binary, control, data) ----
mv "$W/$CTRL.new" "$CTRL"; mv "$W/$DATA.new" "$DATA"
rm -f "$OUT"; ar qc "$OUT" debian-binary "$CTRL" "$DATA"
echo "  wrote $(basename "$OUT")"
