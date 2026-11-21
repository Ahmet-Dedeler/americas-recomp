"""Refuse to run on stale harness inputs.

require_fresh_binds(dir, ids=None): every <dir>/<idx>.bind (or the listed ids) must start with "version <bindings.VERSION>", the hash of
the bindings.py that is in this directory now. A file from an older or edited generator, or one without a stamp, stops the run with
a count and examples. Fix: regenerate with  python -I bindings.py <bins dir> <binds dir>.
Why: the first full-corpus run used bindings from an old copy of bindings.py without atomic-image marking and lost 953 shaders.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bindings


def stale_binds(d, ids=None):
    names = [f"{i}.bind" for i in ids] if ids is not None else [f for f in os.listdir(d) if f.endswith(".bind")]
    bad = []
    for f in names:
        try:
            with open(os.path.join(d, f)) as fh:
                first = fh.readline().split()
        except OSError:
            bad.append((f, "missing"))
            continue
        if first[:1] != ["version"]:
            bad.append((f, "no version stamp"))
        elif first[1:2] != [bindings.VERSION]:
            bad.append((f, "made by bindings.py " + (first[1] if len(first) > 1 else "?")))
    return bad, len(names)


def require_fresh_binds(d, ids=None):
    bad, n = stale_binds(d, ids)
    if bad:
        sys.exit(f"harness_guard: {len(bad)} of {n} bindings in {d} are not from the current bindings.py ({bindings.VERSION}), "
                 f"e.g. {bad[:3]}. Regenerate them: python -I bindings.py <bins> {d}")
    print(f"harness_guard: {n} bindings, all from bindings.py {bindings.VERSION}", flush=True)
