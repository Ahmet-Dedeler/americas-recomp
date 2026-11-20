import subprocess,csv,collections,concurrent.futures as cf,json,os,sys
M=r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
VAL=r"C:/VulkanSDK/1.4.363.1/Bin/spirv-val.exe"
a={e['idx']:e for e in json.load(open('results_full_v1.json'))}
b={e['idx']:e for e in json.load(open('results_full.json'))}
todo=[i for i in a if a[i]['status']!='VALID' and b[i]['status']!='VALID']
variants=[int(x) for x in sys.argv[1:]] or [2,3]
os.makedirs("spv6",exist_ok=True)
def one(args):
    i,v=args;spv=f"spv6/{i}.{v}.spv"
    try:
        p=subprocess.run(["./driver_heap.exe",M+f"bins_all/{i}.bin","32",spv,M+f"binds_all/{i}.bind",str(v)],capture_output=True,text=True,timeout=30)
        o=p.stdout.strip().splitlines();o=o[-1] if o else "";rc=p.returncode
    except subprocess.TimeoutExpired: o,rc="TIMEOUT",-1
    res=dict(idx=i,variant=v)
    if o.startswith("OK"):
        r=subprocess.run([VAL,"--target-env","vulkan1.3",spv],capture_output=True,text=True)
        res.update(status="VALID" if r.returncode==0 else "INVALID",detail=(r.stderr.strip().splitlines() or [""])[0][:200])
    elif o.startswith("THROW"): res.update(status="THROW",detail=o[6:].split(" RecompileRequest:")[0][:300])
    else: res.update(status="TIMEOUT" if o=="TIMEOUT" else "CRASH",detail=o[:200])
    try: os.remove(spv)
    except OSError: pass
    return res
with cf.ThreadPoolExecutor(12) as ex: out=list(ex.map(one,[(i,v) for i in todo for v in variants]))
json.dump(out,open("results_variants.json","w"))
for v in variants: print('variant',v,dict(collections.Counter(x['status'] for x in out if x['variant']==v)))
ok={x['idx'] for x in out if x['status']=='VALID'}
print('todo',len(todo),'newly valid under some variant',len(ok))
