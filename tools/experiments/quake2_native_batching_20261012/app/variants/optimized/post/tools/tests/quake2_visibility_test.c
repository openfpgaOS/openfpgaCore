/* Exact live-stamp and parent-walk comparison, including cache-disabled fallback. */
#define Mod_ClusterPVS OriginalModClusterPVS
#include "../../src/quake2/src/client/refresh/gl1/gl1_model.c"
#undef Mod_ClusterPVS
const byte *Mod_ClusterPVS(int cluster, const model_t *model);
#include "../../src/quake2/src/client/refresh/gl1/gl1_surf.c"
#include <assert.h>
#include <stdlib.h>

model_t *r_worldmodel;
int r_framecount, r_visframecount, r_viewcluster, r_viewcluster2;
int r_oldviewcluster, r_oldviewcluster2;
static cvar_t novis, lockpvs;
cvar_t *r_novis=&novis, *r_lockpvs=&lockpvs;
static unsigned random_state=72391;
static unsigned Random(void) {random_state=random_state*1664525u+1013904223u;return random_state;}
static byte pvs[8][MAX_MAP_LEAFS/8];
const byte *Mod_ClusterPVS(int cluster, const model_t *model)
{
    (void)model;assert(cluster>=0&&cluster<8);return pvs[cluster];
}
static void *allocations[8];static int allocation_count;
void *Hunk_Alloc(int size)
{
    void *p=calloc(1,size);assert(p);assert(allocation_count<8);allocations[allocation_count++]=p;return p;
}
int main(void)
{
    enum {NN=63, NL=64};
    mnode_t nodes[NN]={0};mleaf_t leafs[NL]={0};model_t model={0};dvis_t vis={0};
    model.nodes=nodes;model.numnodes=NN;model.leafs=leafs;model.numleafs=NL;model.vis=&vis;
    r_worldmodel=&model;
    for(int n=0;n<NN;n++)nodes[n].parent=n?nodes+(n-1)/2:NULL;
    for(int l=0;l<NL;l++) {leafs[l].parent=nodes+31+l/2;leafs[l].cluster=l%9?l%8:-1;}
    Mod_BuildVisibilityLinks(&model);
    for(int trial=0;trial<12000;trial++)
    {
        int initial_nodes[NN],initial_leafs[NL],expected_nodes[NN],expected_leafs[NL];
        for(int n=0;n<NN;n++)initial_nodes[n]=nodes[n].visframe=(Random()%9==0)?402:Random()%402;
        for(int l=0;l<NL;l++)initial_leafs[l]=leafs[l].visframe=(Random()%9==0)?402:Random()%402;
        for(int c=0;c<8;c++)for(int b=0;b<(NL+31)/32*4;b++)pvs[c][b]=Random();
        int vc=trial%13?Random()%8:-1,vc2=vc<0?-1:Random()%8;
        int oldc=trial%7? -1:vc,oldc2=trial%7?-1:vc2;
        novis.value=trial%19==0;lockpvs.value=trial%23==0;
        model.vis=trial%29?&vis:NULL;
        of_vislink_t *links=model.of_vislinks;model.of_vislinks=NULL;
        r_visframecount=401;r_oldviewcluster=oldc;r_oldviewcluster2=oldc2;r_viewcluster=vc;r_viewcluster2=vc2;
        R_MarkLeaves();
        int expected_frame=r_visframecount,expected_old1=r_oldviewcluster,expected_old2=r_oldviewcluster2;
        for(int n=0;n<NN;n++){expected_nodes[n]=nodes[n].visframe;nodes[n].visframe=initial_nodes[n];}
        for(int l=0;l<NL;l++){expected_leafs[l]=leafs[l].visframe;leafs[l].visframe=initial_leafs[l];}
        model.of_vislinks=links;
        r_visframecount=401;r_oldviewcluster=oldc;r_oldviewcluster2=oldc2;
        R_MarkLeaves();
        assert(expected_frame==r_visframecount&&expected_old1==r_oldviewcluster&&expected_old2==r_oldviewcluster2);
        for(int n=0;n<NN;n++)assert(expected_nodes[n]==nodes[n].visframe);
        for(int l=0;l<NL;l++)assert(expected_leafs[l]==leafs[l].visframe);
    }
    for(int i=0;i<allocation_count;i++)free(allocations[i]);
    puts("PASS: 12000 compact topology vs original PVS marking, all stamps and state exact");
    return 0;
}
