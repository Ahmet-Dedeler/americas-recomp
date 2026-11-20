import subprocess,csv,sys,re,collections,concurrent.futures as cf,json
M=r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
VAL=r"C:/VulkanSDK/1.4.363.1/Bin/spirv-val.exe"
rows=list(csv.DictReader(open(M+"manifest.csv")))
def one(a):
    r,w=a;spv=f"spv4/{r['idx']}.{w}.spv"
    try:
        p=subprocess.run(["./driver_heap.exe",M+f"bins/{r['idx']}.bin",str(w),spv,M+f"binds/{r['idx']}.bind"],capture_output=True,text=True,timeout=30)
        o=p.stdout.strip().splitlines();o=o[-1] if o else ""
        rc=p.returncode
    except subprocess.TimeoutExpired: o,rc="TIMEOUT",-1
    res=dict(idx=r['idx'],name=r['name'],rt=r['isrt'],wave=w,rc=rc)
    if o.startswith("OK"):
        v=subprocess.run([VAL,"--target-env","vulkan1.3",spv],capture_output=True,text=True)
        res.update(status="VALID" if v.returncode==0 else "INVALID",detail=(v.stderr.strip().splitlines() or [""])[0][:200])
    elif o.startswith("THROW"):
        res.update(status="THROW",detail=o[6:].split(" RecompileRequest:")[0][:200])
    else: res.update(status="CRASH" if rc not in(0,1) else "OTHER",detail=o[:200] or f"rc={rc}")
    return res
with cf.ThreadPoolExecutor(8) as ex: out=list(ex.map(one,[(r,w) for r in rows for w in (32,)]))
json.dump(out,open("results_heap.json","w"))
for w in (32,):
    c=collections.Counter(x['status'] for x in out if x['wave']==w);print("wave",w,dict(c))
