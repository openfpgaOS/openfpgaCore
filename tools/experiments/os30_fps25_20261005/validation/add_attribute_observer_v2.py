#!/usr/bin/env python3
"""Use the frozen ELF section boundary when the unused linker symbol is absent."""
from common import *
import struct

recipe = Path(__file__).with_name('add_attribute_observer.py')
source = recipe.read_text()
baseline = ROOT / 'docs/measurements/os30-simple-20261005/simple-v4/app.elf'
assert sha(baseline) == 'e11905966c8d5c3c8a8d086e3269e5c97a64042eb77c21c1e15d9964b68770a4'
b = baseline.read_bytes()
eh = struct.unpack_from('<16sHHIIIIIHHHHHH', b)
assert eh[0][:5] == b'\x7fELF\x01'
sections = [struct.unpack_from('<IIIIIIIIII', b, eh[6] + i * eh[11]) for i in range(eh[12])]
names_section = sections[eh[13]]
names = b[names_section[4]:names_section[4]+names_section[5]]
named = {names[s[0]:].split(b'\0',1)[0].decode():s for s in sections}
text_section = named['.text']; frame_section = named['.eh_frame']
assert not (text_section[2] & 1)
assert text_section[3] + text_section[5] == frame_section[3] == 0x10d39ab8
source = source.replace("dest = OUT / 'qsim-attributes-observed'", "dest = OUT / 'qsim-attributes-observed-v2'")
source = source.replace('''    const qsim_sym *attr_high=qsim_syms_lookup(&E.syms,"__EH_FRAME_BEGIN__");''', '''    /* PROVIDE(__EH_FRAME_BEGIN__) is omitted when no guest code references
     * it. This exact SHA-bound ELF has read-only .text ending at .eh_frame. */
    const qsim_sym attribute_high_boundary={.addr=0x10d39ab8u};
    const qsim_sym *attr_high=&attribute_high_boundary;''')
source = source.replace("'qsim-attributes-observed-build.log'", "'qsim-attributes-observed-v2-build.log'")
source = source.replace("'qsim-attributes-observed-manifest.json'", "'qsim-attributes-observed-v2-manifest.json'")
assert 'attribute_high_boundary' in source
exec(compile(source, str(__file__), 'exec'), globals())
record['parent_recipe_sha256'] = sha(recipe)
record['readonly_boundary_proof'] = {'text_addr': text_section[3], 'text_size': text_section[5],
    'text_flags': text_section[2], 'eh_frame_addr': frame_section[3],
    'reason_v1_rejected': 'Unused PROVIDE linker symbol absent from current simple-v4 ELF; initial guard aborted before running guest.'}
(OUT / 'qsim-attributes-observed-v2-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
