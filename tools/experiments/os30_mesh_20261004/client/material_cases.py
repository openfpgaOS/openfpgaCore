#!/usr/bin/env python3
"""Read real supported jobs without inventing material or texture state."""
from pathlib import Path
import struct
from analyze import OUT,read

def cases(folder=OUT/'material-textures-360-8'):
    for path in sorted(Path(folder).glob('mesh-*.bin')):
        frame=int(path.stem.split('-')[1]);_,jobs=read(path)
        data=(path.parent/f'material-{frame:05}.bin').read_bytes()
        words=struct.unpack('<%dI'%(len(data)//4),data)
        assert words[:2]==(0x31544d4d,len(jobs)) and len(words)==2+65*len(jobs)
        for ordinal,job in enumerate(jobs):
            status=words[2+65*ordinal];state=list(words[3+65*ordinal:67+65*ordinal])
            assert len(state)==64
            if status:yield frame,ordinal,status,job,None,None;continue
            bank=[0]*256;raw=job['vertices'];indices=job['indices']
            bank[:len(raw)]=raw;bank[128:192]=state
            for i in range(0,len(indices),2):
                bank[192+i//2]=indices[i]|((indices[i+1] if i+1<len(indices) else 0)<<16)
            assert state[38:40]==state[45:47]
            yield frame,ordinal,status,job,bank,state

def textures(path):
    data=Path(path).read_bytes();magic,count=struct.unpack_from('<2I',data)
    assert magic==0x31584d54;cursor=8;result=[]
    for _ in range(count):
        addr,size=struct.unpack_from('<2I',data,cursor);cursor+=8
        result.append((addr,data[cursor:cursor+size]));cursor+=size
    assert cursor==len(data)
    return result
