"""Private wrapper adaptation for generator-elided zero-width AXI ID ports."""
import re
def adapt(source):
    ports=['FetchL1Axi4Plugin_logic_axi_ar_payload_id','FetchL1Axi4Plugin_logic_axi_r_payload_id',
           'LsuL1Axi4Plugin_logic_axi_aw_payload_id','LsuL1Axi4Plugin_logic_axi_b_payload_id',
           'LsuL1Axi4Plugin_logic_axi_ar_payload_id','LsuL1Axi4Plugin_logic_axi_r_payload_id']
    for name in ports:
        source,n=re.subn(r'^    \.'+re.escape(name)+r'\s*\([^\n]+\),\n','',source,flags=re.M)
        assert n==1,(name,n)
    marker='VexiiRiscv cpu (';assert source.count(marker)==1
    return source.replace(marker,'''// Private one-refill/one-writeback CPU has no AXI ID bits. The unchanged
// external slices/fabric retain their wider interface with constant ID zero;
// only one transaction per CPU read/write channel can be outstanding.
assign i_arid_cpu = 0;
assign mem_arid_cpu = 0;
assign mem_awid_cpu = 0;
VexiiRiscv cpu (''')
