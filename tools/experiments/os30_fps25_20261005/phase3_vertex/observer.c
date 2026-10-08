/* Host-only complete vertex-call snapshots, preserving guest/model state. */
typedef struct {
 FILE *out,*csv;const size_t *frame;uint32_t entry,active,recording,return_pc;
 uint32_t seen[5],saved[5],all_calls,all_vertices,count,dest,address;
 uint64_t instret,cyc,dep,div,ic,dc,bp;
} vertex_observer;
static const uint32_t vertex_ranges[][2]={
 {0x1115cca0u,6564u}, /* rdp104 + lighting_memo3072 + rsp3388, contiguous */
 {0x10fdb740u,20u},   /* fog enable, stale slots, lighting epoch (plus padding) */
 {0x10fdbb0cu,8u},    /* deferred fog coefficients and pending mask */
 {0x11327ca8u,12u},   /* current framebuffer dimensions/aspect */
};
static void vertex_write(FILE*f,const void*p,size_t n){if(fwrite(p,1,n,f)!=n)abort();}
static void vertex_dump(qsim_cpu*cpu,FILE*f){
 for(unsigned i=0;i<sizeof(vertex_ranges)/sizeof(*vertex_ranges);i++){
  const void*p=qsim_mem_ptr(cpu,vertex_ranges[i][0],vertex_ranges[i][1]);if(!p)abort();
  vertex_write(f,p,vertex_ranges[i][1]);
 }
}
static uint32_t vertex_word(qsim_cpu*cpu,uint32_t a){uint32_t v;const void*p=qsim_mem_ptr(cpu,a,4);if(!p)abort();memcpy(&v,p,4);return v;}
static void observe_vertex(qsim_cpu*cpu){
 vertex_observer*o=cpu->observe_context;
 if(!o->active){
  o->count=cpu->x[10];o->dest=cpu->x[11];o->address=cpu->x[12];o->return_pc=cpu->x[1];o->recording=0;
  if(*o->frame>=360){o->all_calls++;o->all_vertices+=o->count;}
  const unsigned wanted[]={360,400,500,600,720};unsigned index=5;
  for(unsigned i=0;i<5;i++)if(*o->frame==wanted[i])index=i;
  if(index==5)return;
  unsigned ordinal=o->seen[index]++;
  if(ordinal%7 || o->saved[index]>=40)return;
  if(o->count>68 || o->dest>68 || o->count>68-o->dest || cpu->frm)abort();
  uint32_t h[10]={0x33585456u,(uint32_t)*o->frame,ordinal,o->count,o->dest,o->address,cpu->x[3],cpu->x[2],cpu->frm,qsim_fflags(cpu)};
  if(h[6]!=0x10fdbc70u)abort();
  vertex_write(o->out,h,sizeof h);vertex_dump(cpu,o->out);
  const void*v=qsim_mem_ptr(cpu,o->address,o->count*24u);if(!v)abort();vertex_write(o->out,v,o->count*24u);
  o->instret=cpu->instret;o->cyc=cpu->model->cyc;o->dep=cpu->model->pen_dep;o->div=cpu->model->pen_fdiv;
  o->ic=cpu->model->pen_icache;o->dc=cpu->model->pen_dcache;o->bp=cpu->model->pen_mispredict+cpu->model->pen_btb_taken;
  fprintf(o->csv,"%zu,%u,%u,%u,%u,%u,%u,%u,%u,%u,",*o->frame,ordinal,o->count,o->dest,o->address,
      vertex_word(cpu,0x1115d908u+928),vertex_word(cpu,0x1115d908u+768),
      vertex_word(cpu,0x10fdb750u),vertex_word(cpu,0x10fdbb10u),vertex_word(cpu,0x1115cca0u));
  o->active=o->recording=1;o->saved[index]++;cpu->observe_pc=o->return_pc;return;
 }
 vertex_dump(cpu,o->out);
 uint64_t m[7]={cpu->instret-o->instret,cpu->model->cyc-o->cyc,cpu->model->pen_dep-o->dep,cpu->model->pen_fdiv-o->div,
   cpu->model->pen_icache-o->ic,cpu->model->pen_dcache-o->dc,cpu->model->pen_mispredict+cpu->model->pen_btb_taken-o->bp};
 vertex_write(o->out,m,sizeof m);
 fprintf(o->csv,"%"PRIu64",%"PRIu64",%"PRIu64",%"PRIu64",%"PRIu64",%"PRIu64",%"PRIu64"\n",m[0],m[1],m[2],m[3],m[4],m[5],m[6]);
 o->active=o->recording=0;cpu->observe_pc=o->entry;
}
