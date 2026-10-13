#pragma once
#include <stdbool.h>
#include <stdint.h>

// Two independent PCNT units cannot be read atomically. Bracket both reads
// and accept only a narrow window; task/ISR delays cause a bounded resample,
// never a wider encoder consistency tolerance or a movement retry.
#define ENCODER_SNAPSHOT_ATTEMPTS 3
typedef struct { int quadrature, edges; } encoder_sample_t;
typedef int (*encoder_counter_reader_t)(void *ctx, bool edge_counter, int *value);
static inline bool encoder_snapshot_read(encoder_counter_reader_t reader, void *ctx,
                                         encoder_sample_t *sample, int *error)
{
    for (unsigned attempt=0; attempt<ENCODER_SNAPSHOT_ATTEMPTS; ++attempt) {
        int q0, e0, q1, e1;
        *error=reader(ctx,false,&q0); if(*error)return false;
        *error=reader(ctx,true,&e0); if(*error)return false;
        *error=reader(ctx,false,&q1); if(*error)return false;
        *error=reader(ctx,true,&e1); if(*error)return false;
        int64_t dq=(int64_t)q1-q0, de=(int64_t)e1-e0;
        if(dq>=-1 && dq<=1 && de>=0 && de<=1) {
            *sample=(encoder_sample_t){.quadrature=q1,.edges=e0};
            return true;
        }
    }
    // No output is published if all read windows span too many edges.
    return false;
}
