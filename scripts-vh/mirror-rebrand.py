#!/usr/bin/env python3
"""Mirror termux's glibc apt repo into the vheditor repo by rebranding each .deb.

For one architecture: diff upstream (com.termux) against what we've already published
(vn.vhn.vsc), rebrand only the new/changed .debs (equal-length com.termux->vn.vhn.vsc byte
swap, see rebrand-deb.sh), and stage them under <outdir>/debs/ plus a built-packages list.
The caller tars <outdir>/debs and hands it to the existing vsc.vhn.vn upload path.

Usage: mirror-rebrand.py <arch> <outdir> [max_packages]
  max_packages > 0 bounds a run so the upload stays small; repeated runs drain the backlog.
"""
import gzip, os, shutil, subprocess, sys, tempfile, urllib.request, urllib.error

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

DOC_DIRS = ("/share/man/", "/share/info/", "/share/doc/")

def audit_deb(deb):
    """Functional com.termux residue in a rebranded .deb (docs excluded). [] == clean.

    Catches what a blind byte-swap could miss and that WOULD break at runtime: an absolute
    com.termux symlink target, or com.termux left in a non-doc file (raw or gzipped)."""
    d = tempfile.mkdtemp()
    bad = []
    try:
        subprocess.run(["dpkg-deb", "-x", deb, d], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for dp, dirs, files in os.walk(d):
            for name in dirs + files:
                p = os.path.join(dp, name)
                rel = os.path.relpath(p, d)
                if os.path.islink(p):
                    if b"com.termux" in os.readlink(p).encode():
                        bad.append(rel + " (symlink)")
                    continue
                if name in dirs:
                    continue
                if any(s in "/" + rel for s in DOC_DIRS):
                    continue  # man/info/doc residue is cosmetic
                raw = open(p, "rb").read()
                if b"com.termux" in raw:
                    bad.append(rel)
                elif name.endswith(".gz"):
                    try:
                        if b"com.termux" in gzip.decompress(raw):
                            bad.append(rel + " (gz)")
                    except Exception:
                        pass
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return bad

def main():
    debs = os.path.join(OUT, "debs")
    os.makedirs(debs, exist_ok=True)
    upstream = get_index(UP, UP_COMP, ARCH)
    mine = get_index(MINE, MINE_COMP, ARCH)
    # REFRESH forces re-rebrand + re-publish of named packages (or "all") even when the version
    # is unchanged — used to overwrite already-published debs after a rebrand-logic fix, or to
    # re-do a package the audit flagged. Set via the workflow's `refresh` dispatch input.
    refresh = set(os.environ.get("REFRESH", "").split())
    force_all = "all" in refresh
    delta = [(n, v, fn) for n, (v, fn) in sorted(upstream.items())
             if force_all or n in refresh or mine.get(n, (None,))[0] != v]
    if refresh:
        print(f"[{ARCH}] refresh: {'ALL' if force_all else sorted(refresh)}")
    print(f"[{ARCH}] upstream={len(upstream)} mine={len(mine)} delta={len(delta)}"
          + (f" (capping to {MAX})" if MAX and len(delta) > MAX else ""))
    if MAX:
        delta = delta[:MAX]
    built, audit_fails = [], []
    for i, (name, ver, fn) in enumerate(delta, 1):
        src = os.path.join(OUT, "src.deb")
        base = os.path.basename(fn)
        dst = os.path.join(debs, base)
        try:
            with open(src, "wb") as f:
                f.write(fetch(f"{UP}/{fn}", binary=True))
            subprocess.run(["bash", REBRAND, src, dst], check=True)
            residue = audit_deb(dst)
            if residue:
                print(f"  [{i}/{len(delta)}] AUDIT FAIL {name} {ver}: functional com.termux in "
                      f"{residue[:5]} - NOT publishing", file=sys.stderr)
                os.remove(dst)
                audit_fails.append(name)
            else:
                built.append(name)
                print(f"  [{i}/{len(delta)}] {name} {ver} -> {base}")
        except Exception as e:
            print(f"  [{i}/{len(delta)}] FAILED {name} {ver}: {e}", file=sys.stderr)
        finally:
            if os.path.exists(src):
                os.remove(src)
    with open(os.path.join(debs, "built_termux-glibc_packages.txt"), "w") as f:
        f.write("\n".join(built) + ("\n" if built else ""))

    # Deletion sync: packages we've published for this arch that upstream no longer has.
    # The server removes anything listed in deleted_<repo>_packages.txt. Guard hard against a
    # failed/partial upstream fetch (which would make every package look "removed" and wipe the
    # repo): require a non-empty upstream index and cap deletions per run to a small fraction.
    removed = sorted(n for n in mine if n not in upstream)
    limit = max(15, len(mine) // 20)
    if upstream and 0 < len(removed) <= limit:
        with open(os.path.join(debs, "deleted_termux-glibc_packages.txt"), "w") as f:
            f.write("\n".join(removed) + "\n")
        print(f"[{ARCH}] deletion sync: {len(removed)} gone upstream -> {removed[:10]}")
    elif len(removed) > limit:
        print(f"[{ARCH}] WARNING: {len(removed)} would-be deletions exceed safety limit {limit} "
              f"(upstream={len(upstream)}, mine={len(mine)}) - skipping deletion this run",
              file=sys.stderr)

    print(f"[{ARCH}] rebranded {len(built)} packages, {len(removed)} to delete; remaining backlog "
          f"~{max(0, len(upstream) - len(mine) - len(built))}")
    if audit_fails:
        # GitHub Actions annotation: loud, visible, but does not fail the run (good packages
        # still publish). These packages were withheld until the rebrand gap is fixed + refreshed.
        print(f"::error ::[{ARCH}] {len(audit_fails)} package(s) withheld with functional "
              f"com.termux residue: {audit_fails}")

if __name__ == "__main__":
    main()
