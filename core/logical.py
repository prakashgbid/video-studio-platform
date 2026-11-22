"""Resolve relative path arguments from the folder the user is *in* (the shell's logical $PWD), not from the
physical folder. Needed because ~/Movies/video-pipeline is a folder of shortcuts into Google Drive: after
`cd ~/Movies/video-pipeline/engine`, "../projects/x" must mean ~/Movies/video-pipeline/projects/x."""
import os, sys

def fix_argv():
    pwd = os.environ.get("PWD")
    if not pwd or not os.path.isdir(pwd): return
    try:
        if not os.path.samefile(pwd, os.getcwd()): return
    except OSError: return
    if os.path.realpath(pwd) == pwd: return            # not inside a shortcut: nothing to fix
    for i, a in enumerate(sys.argv[1:], 1):
        if not a or a.startswith("-") or os.path.isabs(a) or "://" in a: continue
        if "/" in a or a in (".", ".."):
            cand = os.path.normpath(os.path.join(pwd, a))
            if os.path.exists(cand) or os.path.exists(os.path.dirname(cand)): sys.argv[i] = cand

fix_argv()
