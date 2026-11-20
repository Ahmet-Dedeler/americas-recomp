import subprocess,csv,sys,collections,concurrent.futures as cf,json,os
sys.path.insert(0,r"E:/Programming/rpf9shaders/out_disasm")
import rdna2
M=r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
VAL=r"C:/VulkanSDK/1.4.363.1/Bin/spirv-val.exe"
stages={'PS':'ps'}
rows=[r for r in csv.DictReader(open(M+"manifest_all.csv")) if r['stage'] in stages]
os.makedirs("spv11",exist_ok=True)
sys.path.insert(0,r"E:/Programming/rpf9shaders/out_disasm")
_cwd=os.getcwd();os.chdir(r'E:/Programming/rpf9shaders/out_disasm')
from run_decode import rows as _rows,blob as _blob
_TBL={}
for _r in _rows:
    if _r['idx'] in {x['idx'] for x in rows}:
        _b=_blob(_r);_ent=[];_p=0x138
        while _p+8<=0x1d0 and _b[_p]==6:
            _ent.append(_b[_p+7]);_p+=8
        _TBL[_r['idx']]=(len(_ent), sum(1<<k for k,fl in enumerate(_ent) if fl&0x10))
os.chdir(_cwd)
def attrs(idx): return _TBL[idx]
def one(args):
    r,relaxed=args;idx=r['idx'];st=stages[r['stage']];spv=f"spv11/{idx}.{int(relaxed)}.spv"
    env=dict(os.environ)
    if relaxed: env['XBOX_ALLOW_UNMEASURED_F16_INTERP']='1'
    cmd=["./driver_gfx.exe",M+f"bins_all/{idx}.bin","32",spv,M+f"binds_all/{idx}.bind","1",st]+((lambda t:["2",str(t[0]),"%x"%t[1]])(attrs(idx)) if st=='ps' else [])
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=30,env=env)
        o=p.stdout.strip().splitlines();o=o[-1] if o else "";rc=p.returncode
    except subprocess.TimeoutExpired: o,rc="TIMEOUT",-1
    res=dict(idx=idx,stage=r['stage'],name=r['name'],relaxed=relaxed)
    if o.startswith("OK"):
        v=subprocess.run([VAL,"--target-env","vulkan1.3",spv],capture_output=True,text=True)
        res.update(status="VALID" if v.returncode==0 else "INVALID",detail=(v.stderr.strip().splitlines() or [""])[0][:200])
    elif o.startswith("THROW"): res.update(status="THROW",detail=o[6:].split(" RecompileRequest:")[0][:300])
    else: res.update(status="TIMEOUT" if o=="TIMEOUT" else "CRASH",detail=o[:200])
    try: os.remove(spv)
    except OSError: pass
    return res
with cf.ThreadPoolExecutor(12) as ex: out=list(ex.map(one,[(r,x) for r in rows for x in (False,)]))
json.dump(out,open("results_gfx_table.json","w"))
for st in stages:
    for x in (False,): print(st,'relaxed' if x else 'strict',dict(collections.Counter(e['status'] for e in out if e['stage']==st and e['relaxed']==x)))
