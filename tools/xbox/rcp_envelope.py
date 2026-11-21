"""Experiment: does a +-1 ulp error in v_rcp_f32 explain a CPU/GPU difference? usage: rcp_envelope.py <idx> <trials>
Runs the CPU reference <trials> times with random +-1 ulp jitter on every v_rcp_f32 and reports whether the GPU image values stay inside the min/max envelope of the CPU runs (images only)."""
import sys,tempfile,subprocess,struct,random
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cpu_compare as X, run_real as R, cpu_ref as C
idx=sys.argv[1];N=int(sys.argv[2])
d=tempfile.mkdtemp();lines,info=R.build(idx,64,d,64,'float')
lines=[("groups 1 1 1" if l.startswith("groups") else l) for l in lines]
sc=d+'/scenario.txt';open(sc,'w').write('\n'.join(lines)+'\n')
subprocess.run([R.EXEC,sc],capture_output=True,text=True)
gpu={}
for iid in info['images']: gpu[1000+iid]=struct.unpack('<%dI'%(len(open(f'{d}/out/image{iid}.bin','rb').read())//4),open(f'{d}/out/image{iid}.bin','rb').read())
code=open(R.ROOT+f'bins_all/{idx}.bin','rb').read()
lo={};hi={}
for t in range(N):
    regions,sgpr,images=X.scenario_memory(lines)
    mem=C.Memory()
    for k,(rid,dd) in enumerate(sorted(regions.items())): mem.add(0x10000000+0x1000000*k,dd)
    C.RCP_JITTER=(lambda: 0) if t==0 else (lambda: random.choice((-1,0,1)))
    w=C.Wave(code,mem,sgpr);w.images=images;w.run()
    for iid,im in images.items():
        for k,v in enumerate(im['data']):
            key=(1000+iid,k);lo[key]=min(lo.get(key,v),v);hi[key]=max(hi.get(key,v),v)
bad=0;diff=0;maxw=0
for (iid,k),l in lo.items():
    if k>=len(gpu[iid]): continue
    g=gpu[iid][k]
    if g!=l or g!=hi[(iid,k)] or True:
        if g<l or g>hi[(iid,k)]: bad+=1
        if l!=hi[(iid,k)]: diff+=1;maxw=max(maxw,hi[(iid,k)]-l)
print(idx,'texels where the CPU result depends on rcp rounding:',diff,'(max spread %d ulp)'%maxw,'| GPU values outside the CPU envelope:',bad)
