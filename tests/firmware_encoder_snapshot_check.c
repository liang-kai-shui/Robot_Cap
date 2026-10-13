#include <assert.h>
#include <stdio.h>
#include "encoder_snapshot.h"

typedef struct { const int *values; unsigned length, at; int fail_at; } fake_t;
static int read_counter(void *ctx,bool edge,int *value) {
    fake_t *f=ctx;
    assert(edge==(f->at%2==1));
    if((int)f->at==f->fail_at)return 17;
    assert(f->at<f->length);
    *value=f->values[f->at++]; return 0;
}
static bool sample(const int *v,unsigned n,encoder_sample_t *out,unsigned *reads) {
    fake_t f={v,n,0,-1};int error=0;
    bool ok=encoder_snapshot_read(read_counter,&f,out,&error);
    assert(error==0);*reads=f.at;return ok;
}
int main(void) {
    encoder_sample_t out={777,888};unsigned reads;
    // Healthy motion interrupted between separate reads: old q=100,e=120
    // would exceed the real mismatch tolerance. Reject that window, then
    // accept a new coherent pair without resetting either hardware counter.
    int delayed[]={100,120,120,120,121,121,121,121};
    assert(sample(delayed,8,&out,&reads)&&reads==8);
    assert(out.quadrature==121&&out.edges==121);
    int reverse[]={-121,121,-122,122};
    assert(sample(reverse,4,&out,&reads)&&reads==4);
    assert(out.quadrature==-122&&out.edges==121);
    // A real signal mismatch must remain visible, not be "corrected".
    int bad_signal[]={80,120,80,120};
    assert(sample(bad_signal,4,&out,&reads));
    assert(out.quadrature==80&&out.edges==120);
    // Accumulator catch-up/rollover crossing is not a coherent read window.
    int rollover[]={29999,29999,0,30000,30001,30001,30001,30001};
    assert(sample(rollover,8,&out,&reads)&&reads==8&&out.quadrature==30001);
    int unstable[]={0,5,5,5,5,10,10,10,10,15,15,15};
    out=(encoder_sample_t){777,888};
    assert(!sample(unstable,12,&out,&reads)&&reads==12);
    assert(out.quadrature==777&&out.edges==888);
    fake_t f={delayed,8,0,1};int error=0;
    assert(!encoder_snapshot_read(read_counter,&f,&out,&error)&&error==17);
    puts("PASS delayed reads, signed motion, real mismatch, rollover, bounded failure and driver errors");
    return 0;
}
