import subprocess,csv,sys,collections,concurrent.futures as cf,json,os
sys.path.insert(0,r"E:/Programming/rpf9shaders/out_disasm")
import rdna2
M=r"E:/Programming/rpf9shaders/out_disasm/anyps5/";VAL=r"C:/VulkanSDK/1.4.363.1/Bin/spirv-val.exe"
EXE=sys.argv[1] if len(sys.argv)>1 else "./driver_fb.exe";LIMIT=int(sys.argv[2]) if len(sys.argv)>2 else 600
stages={'PS':'ps','VS':'vs','?':'ms'}
rows=[r for r in csv.DictReader(open(M+"manifest_all.csv")) if r['stage'] in stages]
os.makedirs("spv12",exist_ok=True)
def attrs(idx):
    d=open(M+f"bins_all/{idx}.bin",'rb').read();ins,_=rdna2.sweep(d,0,len(d));a=[];f=[]
    for i in ins:
        if i.fmt.startswith('VINTRP'): a.append((i.words[0]>>10)&0x3f)
        elif i.name in ('v_interp_p1ll_f16','v_interp_p1lv_f16','v_interp_p2_f16'):
            x=i.words[1]&0x3f; a.append(x); f.append(x)
    return (max(a)+1 if a else 0, sum(1<<x for x in set(f)))
def run(idx,st,relaxed,bary):
    spv=f"spv12/{idx}.{int(relaxed)}{int(bary)}.spv";env=dict(os.environ)
    if relaxed: env['XBOX_ALLOW_UNMEASURED_F16_INTERP']='1'
    if bary: env['XBOX_FRAGMENT_BARY']='1'
    cmd=[EXE,M+f"bins_all/{idx}.bin","32",spv,M+f"binds_all/{idx}.bind","1",st]+((lambda t:["2",str(t[0]),"%x"%t[1]])(attrs(idx)) if st=='ps' else [])
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=LIMIT,env=env);o=(p.stdout.strip().splitlines() or [''])[-1]
    except subprocess.TimeoutExpired:o='TIMEOUT'
    if o.startswith('OK'):
        v=subprocess.run([VAL,"--target-env","vulkan1.3",spv],capture_output=True,text=True);res=('VALID' if v.returncode==0 else 'INVALID',(v.stderr.strip().splitlines() or [''])[0][:150])
    elif o.startswith('THROW'):res=('THROW',o[6:].split(' RecompileRequest:')[0][:250])
    else:res=('TIMEOUT' if o=='TIMEOUT' else 'CRASH',o[:150])
    try:os.remove(spv)
    except OSError:pass
    return res
def one(a):
    r,relaxed=a;idx=r['idx'];st=stages[r['stage']]
    s,d=run(idx,st,relaxed,False);bary=False
    if s=='THROW' and 'emulated barycentric' in d:
        s2,d2=run(idx,st,relaxed,True);bary=True
        if s2=='VALID': s,d='VALID_BARY_EXT',d2
    return dict(idx=idx,stage=r['stage'],name=r['name'],relaxed=relaxed,status=s,detail=d)
jobs=[(r,x) for r in rows for x in ((False,True) if r['stage']=='PS' else (False,))]
with cf.ThreadPoolExecutor(8) as ex: out=list(ex.map(one,jobs))
json.dump(out,open("results_final_gfx.json","w"))
for st in stages:
    for x in (False,True):
        c=collections.Counter(e['status'] for e in out if e['stage']==st and e['relaxed']==x)
        if c: print(st,'relaxed' if x else 'strict',dict(c))
