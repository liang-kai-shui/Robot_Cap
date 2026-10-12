#pragma once
#include <stdint.h>
#include <stddef.h>
// Minimal transport compatibility for the vendored Pololu driver. One task owns it.
class TwoWire {
public:
    void beginTransmission(uint8_t address) { addr=address; used=0; }
    size_t write(uint8_t byte) {
        if (used>=sizeof(tx)) { error=true; return 0; }
        tx[used++]=byte; return 1;
    }
    uint8_t endTransmission();
    uint8_t requestFrom(uint8_t address,uint8_t count);
    int read() { if(pos>=received) { error=true; return 0; } return rx[pos++]; }
    bool failed() const { return error; }
    void clearError() { error=false; }
private:
    uint8_t tx[32]={},rx[32]={},addr=0,used=0,pos=0,received=0;
    bool error=false;
};
extern TwoWire Wire;
