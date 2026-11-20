import subprocess,csv,json,re,sys,random,os,concurrent.futures as cf
B="E:/Programming/rpf9shaders/out_disasm/anyps5/bins_all/"
exe,outf=sys.argv[1],sys.argv[2];tmo=int(sys.argv[3]) if len(sys.argv)>3 else 900;env=dict(os.environ,XBOX_SPLIT_PCT='60')
old=[x for x in json.load(open('corpus_v2.json')) if x.get('ms')]
old.sort(key=lambda x:-x['ms'])
ids=[x['idx'] for x in old[:25]]+['22616','22617','22618','23206','23207','23208']
rows=list(csv.DictReader(open("E:/Programming/rpf9shaders/out_disasm/anyps5/manifest_all.csv")))
random.seed(5);rest=[r['idx'] for r in rows if r['idx'] not in ids and r['stage'] in ('CS','PS','VS')]
ids+=random.sample(rest,250)
def one(i):
    try:
        p=subprocess.run([exe,B+i+".bin"],capture_output=True,text=True,timeout=tmo,env=env);o=p.stdout
    except subprocess.TimeoutExpired:o=''
    m=re.search(r'structurize (\d+)ms blocks=(\d+) hash=([0-9a-f]+)',o)
    return (i,int(m.group(1)) if m else None,int(m.group(2)) if m else None,m.group(3) if m else None)
with cf.ThreadPoolExecutor(4) as ex: out=list(ex.map(one,ids))
json.dump(out,open(outf,'w'));print(len(out),sum(1 for o in out if o[1] is None),'fail/timeouts',sum(o[1] or 0 for o in out)/1000,'s total')
