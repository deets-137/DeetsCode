"""blog_doctor — one-shot diagnose-and-fix script for `blog` mode.

Run from the harness root with the same Python that runs server.py:

    python blog_doctor.py            # check + auto-install missing deps + smoke test
    python blog_doctor.py --check    # check only, no installs

What it does, in order:
    1. Reports which Python interpreter and which venv (if any) is active.
    2. Verifies BLOG_DIR points at a real blog repo with app/repo.py + app/itunes.py.
    3. pip-installs harness/requirements.txt and blog/requirements.txt (unless --check).
    4. Imports tools.blog_service from the harness — the same path the WS handler
       in server.py uses when you click "save draft".
    5. Runs a CRUD smoke test against the blog DB: create a junk draft, list it,
       publish, unpublish, delete. The same calls the harness blog panel makes
       when you click + song / save / publish / delete in the UI.
    6. Prints a green ✓ summary or the first traceback that broke the chain.

If step 4 or 5 fails, the printed error is the same one the WS handler would
report as `blog_error` — so the cause of the "save draft does nothing" symptom
will be visible here without having to read browser devtools.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import traceback
from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parent
HARNESS_REQS = HARNESS_ROOT / "requirements.txt"


def step(label: str) -> None:
    print(f"\n── {label} " + "─" * max(2, 70 - len(label)))


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def warn(msg: str) -> None:
    print(f"  ! {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


def show_python() -> None:
    step("interpreter")
    print(f"  python   : {sys.executable}")
    print(f"  version  : {sys.version.split()[0]}")
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        ok(f"venv active: {venv}")
    else:
        warn("no VIRTUAL_ENV set — using system / harness's own interpreter."
             " That's fine if you launch server.py with the same `python` you ran here.")


def resolve_blog_dir() -> Path:
    step("blog repo location")
    sys.path.insert(0, str(HARNESS_ROOT))  # so `import paths` resolves
    import paths  # type: ignore
    blog_dir = Path(paths.BLOG_DIR)
    print(f"  paths.BLOG_DIR -> {blog_dir}")
    if not blog_dir.is_dir():
        fail(f"BLOG_DIR does not exist: {blog_dir}")
        raise SystemExit(2)
    must_exist = [blog_dir / "app" / "repo.py",
                  blog_dir / "app" / "itunes.py",
                  blog_dir / "app" / "settings.py",
                  blog_dir / "requirements.txt"]
    missing = [p for p in must_exist if not p.exists()]
    if missing:
        for p in missing:
            fail(f"missing: {p}")
        raise SystemExit(2)
    ok("blog repo skeleton present")
    return blog_dir


def pip_install(req: Path) -> None:
    if not req.exists():
        warn(f"skip: {req} not found")
        return
    print(f"  installing {req} ...")
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        fail(f"pip exited {proc.returncode}")
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(3)
    ok(f"installed deps from {req.name}")


def install_deps(blog_dir: Path) -> None:
    step("pip install")
    pip_install(HARNESS_REQS)
    pip_install(blog_dir / "requirements.txt")


def import_service():
    step("import tools.blog_service (same path server.py uses)")
    try:
        from tools import blog_service  # type: ignore
    except Exception:
        fail("import failed — this is the same error 'save draft' produces:")
        traceback.print_exc()
        raise SystemExit(4)
    ok("tools.blog_service imported")
    # Force the lazy blog-repo import too — that's the usual failure point.
    try:
        blog_service._ensure_blog_on_path()
        from app import repo as _r       # type: ignore  # noqa: F401
        from app import itunes as _i     # type: ignore  # noqa: F401
    except Exception:
        fail("loading blog repo modules failed:")
        traceback.print_exc()
        raise SystemExit(4)
    ok("blog repo (app.repo, app.itunes) imported")
    return blog_service


def smoke_test(svc) -> None:
    step("CRUD smoke test (mirrors the WS save/publish/delete flow)")
    junk_title = "blog_doctor smoke test — safe to delete"
    p = svc.create_post(
        kind="journal", title=junk_title,
        meta={}, body_md="ignore me", locked=False,
    )
    slug = p["slug"]
    ok(f"create_post   -> slug={slug}")
    posts = svc.list_posts(status="draft", limit=5)
    if not any(x["slug"] == slug for x in posts):
        fail("list_posts did not return the new draft")
        raise SystemExit(5)
    ok(f"list_posts    -> found {len(posts)} draft(s) including ours")
    p = svc.publish(slug)
    ok(f"publish       -> status={p['status']}")
    p = svc.unpublish(slug)
    ok(f"unpublish     -> status={p['status']}")
    svc.delete_post(slug)
    ok("delete_post   -> ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="don't run pip install, just verify imports + smoke test")
    args = ap.parse_args()

    show_python()
    blog_dir = resolve_blog_dir()
    if not args.check:
        install_deps(blog_dir)
    svc = import_service()
    smoke_test(svc)
    print("\nall green — blog mode is wired correctly. if 'save draft' still")
    print("fails in the browser, the harness server.py was started with a")
    print("DIFFERENT python interpreter than the one you just ran this with.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
