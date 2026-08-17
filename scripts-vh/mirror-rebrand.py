#!/usr/bin/env python3
"""Mirror termux's glibc apt repo into the vheditor repo by rebranding each .deb.

For one architecture: diff upstream (com.termux) against what we've already published
(vn.vhn.vsc), rebrand only the new/changed .debs (equal-length com.termux->vn.vhn.vsc byte
swap, see rebrand-deb.sh), and stage them under <outdir>/debs/ plus a built-packages list.
The caller tars <outdir>/debs and hands it to the existing vsc.vhn.vn upload path.

Usage: mirror-rebrand.py <arch> <outdir> [max_packages]
  max_packages > 0 bounds a run so the upload stays small; repeated runs drain the backlog.
"""
import gzip, os, subprocess, sys, urllib.request, urllib.error

ARCH = sys.argv[1]
OUT = os.path.abspath(sys.argv[2])
MAX = int(sys.argv[3]) if len(sys.argv) > 3 else 0
SELF = os.path.dirname(os.path.abspath(__file__))
REBRAND = os.path.join(SELF, "rebrand-deb.sh")

UP = "https://packages-cf.termux.dev/apt/termux-glibc"        # upstream (com.termux)
UP_COMP = "stable"
MINE = "https://vsc.vhn.vn/termux-packages-24"                # ours (vn.vhn.vsc)
MINE_COMP = "main"

def fetch(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "vh-mirror"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()

def get_index(base, comp, arch):
    """Return dict Package-name -> (version, filename) from a Packages(.gz) index."""
    raw = None
    for ext in (".gz", ""):
        url = f"{base}/dists/glibc/{comp}/binary-{arch}/Packages{ext}"
        try:
            data = fetch(url, binary=True)
            raw = gzip.decompress(data) if ext == ".gz" else data
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
    if raw is None:
        return {}
    out, name, ver, fn = {}, None, None, None
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("Package:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("Version:"):
            ver = line.split(":", 1)[1].strip()
        elif line.startswith("Filename:"):
            fn = line.split(":", 1)[1].strip()
        elif not line.strip():
            if name and ver:
                out[name] = (ver, fn)
            name = ver = fn = None
    if name and ver:
        out[name] = (ver, fn)
    return out

def main():
    debs = os.path.join(OUT, "debs")
    os.makedirs(debs, exist_ok=True)
    upstream = get_index(UP, UP_COMP, ARCH)
    mine = get_index(MINE, MINE_COMP, ARCH)
    delta = [(n, v, fn) for n, (v, fn) in sorted(upstream.items())
             if mine.get(n, (None,))[0] != v]
    print(f"[{ARCH}] upstream={len(upstream)} mine={len(mine)} delta={len(delta)}"
          + (f" (capping to {MAX})" if MAX and len(delta) > MAX else ""))
    if MAX:
        delta = delta[:MAX]
    built = []
    for i, (name, ver, fn) in enumerate(delta, 1):
        src = os.path.join(OUT, "src.deb")
        base = os.path.basename(fn)
        dst = os.path.join(debs, base)
        try:
            with open(src, "wb") as f:
                f.write(fetch(f"{UP}/{fn}", binary=True))
            subprocess.run(["bash", REBRAND, src, dst], check=True)
            built.append(name)
            print(f"  [{i}/{len(delta)}] {name} {ver} -> {base}")
        except Exception as e:
            print(f"  [{i}/{len(delta)}] FAILED {name} {ver}: {e}", file=sys.stderr)
        finally:
            if os.path.exists(src):
                os.remove(src)
    with open(os.path.join(debs, f"built_termux-glibc_packages.txt"), "w") as f:
        f.write("\n".join(built) + ("\n" if built else ""))
    print(f"[{ARCH}] rebranded {len(built)} packages; remaining backlog "
          f"~{max(0, len(upstream) - len(mine) - len(built))}")

if __name__ == "__main__":
    main()
