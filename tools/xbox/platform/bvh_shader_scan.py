"""Classify the XboxBvhBuilder compute shaders by what the Xbox DXIL compiler put in them.

The builder DLL carries 53 DXIL shaders. 44 of them call non-standard `@xbox.*` functions (the dxcxbox compiler's one-to-one wrappers of
RDNA instructions: 64-bit global loads/stores/atomics, Med3/Min3/Max3, BFI, lane operations ...), which no PC shader compiler knows.
Input: a directory with shader_NN.ll (`bvh_test` with BVH_DUMP_DIR=<dir> writes shader_NN.dxil, `dxc -dumpbin shader_NN.dxil > shader_NN.ll`
disassembles) and optionally hw_result.json ({"NN": "OK"|"CRASH"|"FAIL"}, from running bvh_test with BVH_ONLY=NN on a PC GPU).
usage: python -I bvh_shader_scan.py <dir> [hw_result.json] [out.json]      Only names, counts and outcomes are written, no shader code."""
import json, re, collections, os, sys
d = sys.argv[1]
res = {int(k): v for k, v in json.load(open(sys.argv[2])).items()} if len(sys.argv) > 2 else collections.defaultdict(lambda: "?")
OUT = sys.argv[3] if len(sys.argv) > 3 else "bvh_shader_scan.json"
os.chdir(d)
POINTER = ('global_load', 'global_store', 'global_atomic', 's_load')     # read/write memory by 64-bit address
WAVE = ('Ballot64', 'MBCNT64', 'S_BCNT1_U64', 'LaneSwizzle', 'ReadLane', 'GetLaneID', 'WaveMin_U32', 'MakeUniform')
rows = {}
for n in sorted(int(f[7:9]) for f in os.listdir('.') if re.fullmatch(r'shader_\d\d\.ll', f)):
    t = open(f'shader_{n:02d}.ll', errors='ignore').read()
    names = set(re.findall(r'call [^@\n]*@xbox\.([A-Za-z0-9_.]+)\(', t))
    m = re.search(r'([A-Za-z0-9_]+)\.pdb', t)
    rows[n] = (m.group(1) if m else '?', names)
cls = collections.defaultdict(list)
for n, (nm, names) in rows.items():
    if not names:
        c = 'standard DXIL (compiles)'
    elif any(x.startswith(POINTER) for x in names):
        c = 'needs raw 64-bit address access'
    elif any(x.startswith(WAVE) for x in names):
        c = 'wave/lane intrinsics, no pointers'
    else:
        c = 'arithmetic intrinsics only'
    cls[c].append(n)
for c, l in cls.items():
    print(f'{c}: {len(l)}')
    if c != 'standard DXIL (compiles)':
        print('   ', [(n, rows[n][0]) for n in l][:60] if len(l) < 12 else [n for n in l])
only_arith = cls['arithmetic intrinsics only'] + cls['wave/lane intrinsics, no pointers']
print('\nintrinsics needed by the no-pointer group:', sorted(set().union(*[rows[n][1] for n in only_arith])))
json.dump({str(n): {"name": rows[n][0], "result_on_amd_pc_compiler": res[n], "xbox_intrinsics": sorted(rows[n][1])} for n in rows}, open(OUT, 'w'), indent=1)
