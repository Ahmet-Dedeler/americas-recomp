import subprocess,csv,sys,collections,concurrent.futures as cf,json,os
sys.path.insert(0,r"E:/Programming/rpf9shaders/out_disasm")
import rdna2
M=r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
VAL=r"C:/VulkanSDK/1.4.363.1/Bin/spirv-val.exe"
stages={'?':'ms'}
rows=[r for r in csv.DictReader(open(M+"manifest_all.csv")) if r['stage'] in stages]
os.makedirs("spv10",exist_ok=True)
def attrs(idx):
    d=open(M+f"bins_all/{idx}.bin",'rb').read()
    ins,_=rdna2.sweep(d,0,len(d))
    a=[];f=[]
    for i in ins:
        if i.fmt.startswith('VINTRP'): a.append((i.words[0]>>10)&0x3f)
        elif i.name in ('v_interp_p1ll_f16','v_interp_p1lv_f16','v_interp_p2_f16'):
            x=i.words[1]&0x3f; a.append(x); f.append(x)
    return (max(a)+1 if a else 0, sum(1<<x for x in set(f)))
def one(args):
    r,relaxed=args;idx=r['idx'];st=stages[r['stage']];spv=f"spv10/{idx}.{int(relaxed)}.spv"
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
json.dump(out,open("results_ms.json","w"))
for st in stages:
    for x in (False,): print(st,'relaxed' if x else 'strict',dict(collections.Counter(e['status'] for e in out if e['stage']==st and e['relaxed']==x)))
