import sys,csv,struct,random,re,collections
sys.path.insert(0,'.')
from run_decode import rows,blob,code_range
T={0:'PS',1:'VS',2:'GS',3:'HS',4:'DS',5:'CS',7:'MS',8:'AS'}
typ={}
for r in rows:
    d=open('../out/'+r['dxil_file'],'rb').read(4);typ[r['idx']]=T.get(struct.unpack('<I',d)[0]>>16,'?')
print(collections.Counter(typ.values()))
random.seed(7)
cs=[r for r in rows if typ[r['idx']]=='CS']
rt=[r for r in cs if re.search(r'TraceRays_BVH|RTShadowTrace|RaytraceVBuffer|ShadowTrace|DeferredGather',r['name'])]
rest=[r for r in cs if r not in rt]
sel=rt+random.sample(rest,300-len(rt))
import rdna2
out=open('anyps5/manifest.csv','w')
out.write('idx,name,bytes,isrt,prologue\n')
for r in sel:
    b=blob(r);s,e=code_range(b)
    w=struct.unpack_from('<10I',b,s)
    pro=all((w[i]>>26)==0x35 and ((w[i]>>16)&0x3ff)==0x180 for i in (4,7))
    s0=s+(40 if pro else 0)
    ins,_=rdna2.sweep(b,s0,e)
    ends=[i for i in ins if i.name=='s_endpgm']
    e0=ends[-1].off+4 if ends else e
    open(f'anyps5/bins/{r["idx"]}.bin','wb').write(b[s0:e0])
    out.write(f'{r["idx"]},{r["name"]},{e0-s0},{int(r in rt)},{int(pro)}\n')
print(len(cs),len(rt),len(sel))
