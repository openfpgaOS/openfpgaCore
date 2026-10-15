from pathlib import Path
import struct,json,hashlib
pak=Path('/tmp/quake2-gpu-20261011/bench/inputs/main/data/pak0.pak')
rows=[]
roundup=lambda x,n:(x+n-1)&~(n-1)
with pak.open('rb') as f:
    magic,offset,length=struct.unpack('<4sII',f.read(12));assert magic==b'PACK' and length%64==0
    f.seek(offset);directory=f.read(length)
    for off in range(0,length,64):
        raw,start,size=struct.unpack_from('<56sII',directory,off);name=raw.split(b'\0')[0].decode()
        if not name.endswith('.bsp'):continue
        f.seek(start);data=f.read(size);magic,version=struct.unpack_from('<4sI',data);assert magic==b'IBSP' and version==38
        lumps=[struct.unpack_from('<II',data,8+8*i) for i in range(19)]
        fo,fl=lumps[6];to,tl=lumps[5];assert fl%20==0 and tl%76==0
        eligible=0;oldcache=newcache=oldhunk=newhunk=0;counts={}
        for i in range(fl//20):
            n,ti=struct.unpack_from('<hh',data,fo+i*20+8);assert 0<=ti<tl//76
            flags=struct.unpack_from('<I',data,to+ti*76+32)[0]
            if flags&8 or not 3<=n<=32:continue
            eligible+=1;counts[n]=counts.get(n,0)+1
            poly=roundup(132+(n-4)*28,8)
            old=120+20*n;new=128+4*(7*n-4)
            oldcache+=old;newcache+=new;oldhunk+=roundup(poly+old,32);newhunk+=roundup(poly+new,32)
        rows.append({'map':name,'bsp_sha256':hashlib.sha256(data).hexdigest(),'cacheable_polygons':eligible,'counts':counts,'old_cache_bytes':oldcache,'new_cache_bytes':newcache,'extra_cache_bytes':newcache-oldcache,'old_polygon_hunk_bytes':oldhunk,'new_polygon_hunk_bytes':newhunk,'extra_rounded_hunk_bytes':newhunk-oldhunk})
out={'input_pak':str(pak),'scope':'All BSPs present in public demo fixture; extra BSP polygon hunk bytes only, not total model/heap. Warps and n outside3..32 unchanged. RV32 old/new world structs120/128; glpoly132; GL poly alignment8 then Hunk32. No guarantee arbitrary larger map fits remaining heap.','maps':rows}
Path(__file__).with_suffix('.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(rows,indent=2))
