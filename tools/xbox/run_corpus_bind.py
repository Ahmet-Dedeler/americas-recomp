import subprocess,csv,sys,re,collections,concurrent.futures as cf,json,os
M=r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
VAL=r"C:/VulkanSDK/1.4.363.1/Bin/spirv-val.exe"
rows=[r for r in csv.DictReader(open(M+"manifest_all.csv")) if r['stage']=='CS']
os.makedirs("spv5",exist_ok=True)
def one(r):
    spv=f"spv5/{r['idx']}.spv"
    try:
        p=subprocess.run(["./driver_heap.exe",M+f"bins_all/{r['idx']}.bin","32",spv,M+f"binds_all/{r['idx']}.bind"],capture_output=True,text=True,timeout=30)
        o=p.stdout.strip().splitlines();o=o[-1] if o else "";rc=p.returncode
    except subprocess.TimeoutExpired: o,rc="TIMEOUT",-1
    res=dict(idx=r['idx'],name=r['name'],rc=rc)
    if o.startswith("OK"):
        v=subprocess.run([VAL,"--target-env","vulkan1.3",spv],capture_output=True,text=True)
        res.update(status="VALID" if v.returncode==0 else "INVALID",detail=(v.stderr.strip().splitlines() or [""])[0][:200])
    elif o.startswith("THROW"): res.update(status="THROW",detail=o[6:].split(" RecompileRequest:")[0][:300])
    else: res.update(status="TIMEOUT" if o=="TIMEOUT" else ("CRASH" if rc not in(0,1) else "OTHER"),detail=o[:200] or f"rc={rc}")
    try: os.remove(spv)
    except OSError: pass
    return res
with cf.ThreadPoolExecutor(12) as ex: out=list(ex.map(one,rows))
json.dump(out,open("results_full.json","w"))
print(len(out),dict(collections.Counter(x['status'] for x in out)))
