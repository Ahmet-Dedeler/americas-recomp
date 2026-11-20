import subprocess,csv,sys,collections,concurrent.futures as cf,json,os
sys.path.insert(0,r"E:/Programming/rpf9shaders/out_disasm")
import rdna2
M=r"E:/Programming/rpf9shaders/out_disasm/anyps5/"
VAL=r"C:/VulkanSDK/1.4.363.1/Bin/spirv-val.exe"
EXE=sys.argv[1];KEY=sys.argv[2]
man={r['idx']:r for r in csv.DictReader(open(M+"manifest_all.csv"))}
def attrs(idx):
    d=open(M+f"bins_all/{idx}.bin",'rb').read()
    ins,_=rdna2.sweep(d,0,len(d));a=[];f=[]
    for i in ins:
        if i.fmt.startswith('VINTRP'): a.append((i.words[0]>>10)&0x3f)
        elif i.name in ('v_interp_p1ll_f16','v_interp_p1lv_f16','v_interp_p2_f16'):
            x=i.words[1]&0x3f; a.append(x); f.append(x)
    return (max(a)+1 if a else 0, sum(1<<x for x in set(f)))
jobs=[]
for x in json.load(open('results_full.json')):
    if KEY in x['detail']: jobs.append((x['idx'],'cs',False))
for x in json.load(open('results_gfx3.json')):
    if KEY in x['detail']: jobs.append((x['idx'],{'PS':'ps','VS':'vs'}[x['stage']],x['relaxed']))
os.makedirs('spv10',exist_ok=True)
def one(j):
    idx,st,rel=j;spv=f"spv10/{idx}.{int(rel)}.spv";env=dict(os.environ)
    if os.environ.get('XBOX_FRAGMENT_BARY'): env['XBOX_FRAGMENT_BARY']='1'
    if rel: env['XBOX_ALLOW_UNMEASURED_F16_INTERP']='1'
    cmd=[EXE,M+f"bins_all/{idx}.bin","32",spv,M+f"binds_all/{idx}.bind","1",st]
    if st=='ps': t=attrs(idx);cmd+=["2",str(t[0]),"%x"%t[1]]
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=120,env=env);o=(p.stdout.strip().splitlines() or [''])[-1]
    except subprocess.TimeoutExpired:o='TIMEOUT'
    if o.startswith('OK'):
        v=subprocess.run([VAL,"--target-env","vulkan1.3",spv],capture_output=True,text=True);s='VALID' if v.returncode==0 else 'INVALID'
    else:s=o[:150]
    try:os.remove(spv)
    except OSError:pass
    return (idx,st,rel,s)
with cf.ThreadPoolExecutor(12) as ex: out=list(ex.map(one,jobs))
json.dump(out,open('rerun_'+KEY.split()[0]+'.json','w'))
print(len(out),collections.Counter((o[1],o[2],o[3][:90]) for o in out))
