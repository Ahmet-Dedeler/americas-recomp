"""Build the shader recompiler + Xbox test tools with MSVC (no CMake, no MinGW).

Run from a normal Windows shell (PowerShell/cmd), NOT Git Bash: vcvars64.bat needs the native environment.

    python tools/xbox/build_msvc.py [--build-dir build-xbox] [--jobs 8] [--force]

Outputs <build-dir>/driver.exe and <build-dir>/stages.exe.
Needs: Visual Studio (any recent), and 3rdparty/SPIRV-Headers checked out
(git submodule update --init 3rdparty/SPIRV-Headers).
"""
import argparse, concurrent.futures as cf, glob, os, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
REC = ROOT / "core/shader/recompiler"
HERE = pathlib.Path(__file__).resolve().parent


def vc_env():
    vswhere = pathlib.Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    install = subprocess.check_output([str(vswhere), "-latest", "-products", "*", "-property", "installationPath"], text=True).strip()
    bat = pathlib.Path(install) / "VC/Auxiliary/Build/vcvars64.bat"
    out = subprocess.check_output(f'cmd /c ""{bat}" >nul 2>&1 && set"', text=True, shell=True)
    return dict(l.split("=", 1) for l in out.splitlines() if "=" in l)


def includes():
    d = [ROOT / "core/libs", ROOT / "3rdparty/SPIRV-Headers/include", REC, HERE / "msvc"]
    d += list(REC.glob("*/include")) + list(REC.glob("*/*/include"))
    return " ".join(f'/I"{p}"' for p in d)


FLAGS = "/nologo /std:c++20 /EHsc /O1 /w /DNOMINMAX /DWIN32_LEAN_AND_MEAN /permissive-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", default=str(ROOT / "build-xbox"))
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    B = pathlib.Path(a.build_dir)
    (B / "obj").mkdir(parents=True, exist_ok=True)
    (B / "tool_obj").mkdir(exist_ok=True)
    env = vc_env()
    cl = shutil.which("cl", path=env.get("Path") or env.get("PATH"))
    inc = includes()
    cm = (REC / "CMakeLists.txt").read_text()
    srcs = [s for s in dict.fromkeys(re.findall(r"\$\{CMAKE_CURRENT_FUNCTION_LIST_DIR\}/(\S+\.cpp)", cm))
            if "DummyShaders" not in s and "SpirvOptimizer" not in s and not s.startswith("tests/") and "/tests/" not in s]

    def one(s):
        out = B / "obj" / (s.replace("/", "_") + ".obj")
        if out.exists() and not a.force:
            return s, 0, ""
        r = subprocess.run(f'"{cl}" {FLAGS} /bigobj /c {inc} /Fo"{out}" "{REC / s}"', env=env, capture_output=True, text=True)
        return s, r.returncode, r.stdout + r.stderr

    with cf.ThreadPoolExecutor(a.jobs) as ex:
        results = list(ex.map(one, srcs))
    bad = [(s, o) for s, c, o in results if c]
    for s, o in bad:
        print("FAILED", s, "\n", o[-1500:])
    if bad:
        sys.exit(1)
    objs = " ".join(f'"{o}"' for o in glob.glob(str(B / "obj" / "*.obj")))
    for src, exe in (("driver.cpp", "driver.exe"), ("stages.cpp", "stages.exe")):
        r = subprocess.run(f'"{cl}" {FLAGS} {inc} "{HERE / src}" "{HERE / "stub_version.cpp"}" {objs} /Fe:"{B / exe}" /Fo"{(B / "tool_obj").as_posix()}/" advapi32.lib',
                           env=env, capture_output=True, text=True)
        if r.returncode:
            print(r.stdout + r.stderr)
            sys.exit(1)
        print("built", B / exe)


if __name__ == "__main__":
    main()
