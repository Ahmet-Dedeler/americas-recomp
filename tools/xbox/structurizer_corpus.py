import subprocess,csv,json,re,sys,os,concurrent.futures as cf
B="E:/Programming/rpf9shaders/out_disasm/anyps5/bins_all/"
exe=sys.argv[1];outf=sys.argv[2];tmo=int(sys.argv[3]) if len(sys.argv)>3 else 60
rows=list(csv.DictReader(open("E:/Programming/rpf9shaders/out_disasm/anyps5/manifest_all.csv")))
env=dict(os.environ,XBOX_STRUCT_PROF="1",XBOX_SPLIT_PCT=sys.argv[4] if len(sys.argv)>4 else '150')
def one(r):
    try:
        p=subprocess.run([exe,B+r['idx']+".bin"],capture_output=True,text=True,timeout=tmo,env=env)
        err=p.stderr;out=p.stdout;to=False
    except subprocess.TimeoutExpired as e:
        err=(e.stderr or b'').decode(errors='ignore') if isinstance(e.stderr,bytes) else (e.stderr or '');out='';to=True
    m=re.search(r'build \d+ms blocks=(\d+)',out or '');ms=re.search(r'structurize (\d+)ms blocks=(\d+) hash=([0-9a-f]+)',out or '')
    att=err.count('[struct] attempt privatize')
    fails=re.findall(r'attempt failed: (.{0,60})',err)
    spl=[(int(a),int(b)) for a,b in re.findall(r'splits=([0-9]+) orig=([0-9]+)',err)]
    oks=[(int(a),int(b)) for a,b in re.findall(r'\[struct\] ok start=(\d+) end=(\d+)',err)]
    return dict(idx=r['idx'],stage=r['stage'],blocks=int(m.group(1)) if m else None,timeout=to,ms=int(ms.group(1)) if ms else None,out_blocks=int(ms.group(2)) if ms else None,hash=ms.group(3) if ms else None,attempts=att,fails=fails,oks=oks,spl=spl,crash=(p.returncode if not to else None) if False else None)
with cf.ThreadPoolExecutor(6) as ex: res=list(ex.map(one,rows))
json.dump(res,open(outf,'w'))
print('done',len(res))
