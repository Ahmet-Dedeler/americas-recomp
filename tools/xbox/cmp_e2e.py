import subprocess,csv,sys,json,os,concurrent.futures as cf
sys.path.insert(0,r"E:/Programming/rpf9shaders/out_disasm")
import rdna2
M=r"E:/Programming/rpf9shaders/out_disasm/anyps5/";VAL=r"C:/VulkanSDK/1.4.363.1/Bin/spirv-val.exe"
b={x[0]:x for x in json.load(open('cmp_base.json'))};n={x[0]:x for x in json.load(open('cmp_new8.json'))}
ids=[i for i in b if b[i][3]!=n[i][3]]
man={r['idx']:r for r in csv.DictReader(open(M+"manifest_all.csv"))}
stg={'CS':'cs','PS':'ps','VS':'vs','?':'ms'}
def attrs(idx):
    d=open(M+f"bins_all/{idx}.bin",'rb').read();ins,_=rdna2.sweep(d,0,len(d));a=[];f=[]
    for i in ins:
        if i.fmt.startswith('VINTRP'): a.append((i.words[0]>>10)&0x3f)
        elif i.name in ('v_interp_p1ll_f16','v_interp_p1lv_f16','v_interp_p2_f16'):
            x=i.words[1]&0x3f; a.append(x); f.append(x)
    return (max(a)+1 if a else 0, sum(1<<x for x in set(f)))
def run(exe,idx,tag):
    st=stg.get(man[idx]['stage'],'cs');spv=f"spv13/{idx}.{tag}.spv"
    cmd=[exe,M+f"bins_all/{idx}.bin","32",spv,M+f"binds_all/{idx}.bind","1",st]
    if st=='ps': t=attrs(idx);cmd+=["2",str(t[0]),"%x"%t[1]]
    env=dict(os.environ,XBOX_ALLOW_UNMEASURED_F16_INTERP='1',XBOX_FRAGMENT_BARY='1')
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=900,env=env);o=(p.stdout.strip().splitlines() or [''])[-1]
    except subprocess.TimeoutExpired:o='TIMEOUT'
    if o.startswith('OK'):
        v=subprocess.run([VAL,"--target-env","vulkan1.3",spv],capture_output=True,text=True);r='VALID' if v.returncode==0 else 'INVALID'
    else:r=o[:70]
    try:os.remove(spv)
    except OSError:pass
    return r
os.makedirs('spv13',exist_ok=True)
def one(i): return (i,man[i]['stage'],run('./driver_fb.exe',i,'a'),run('./driver_new.exe',i,'b'))
with cf.ThreadPoolExecutor(8) as ex: out=list(ex.map(one,ids))
diff=[o for o in out if o[2]!=o[3]]
print(len(out),'compared; differing status:',len(diff));[print(d) for d in diff[:10]]
import collections;print(collections.Counter((o[2][:5],o[3][:5]) for o in out))
