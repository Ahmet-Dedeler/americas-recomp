// Stand-in for an Xbox COM object (device, descriptor heap, ...): an object whose vtable mirrors the PC device's, except for slots where the Xbox layout is known
// to differ. Every slot starts as a generated thunk (mov rcx,[rcx+8]; mov rax,[rcx]; jmp [rax+slot*8]) that forwards to the same slot of
// the real PC device with `this` replaced; override(slot, fn) installs a C++ function that receives the proxy as `this` (the real device
// is proxy->real). This is a first step of the "D3D12.X runtime" row: which slots differ is learned from the DLLs' own device calls
// (docs/xbox/PLATFORM_SURFACE.md), so the table of overrides grows with what the game executable is seen to use.
#pragma once
#include <windows.h>
#include <d3d12.h>
#include <cstdio>
#include <cstring>

struct XboxObjectProxy {
    void** vtbl;                 // must stay the first member: the callee dereferences `this`
    IUnknown* real;              // second member: the thunks load it from this+8
    void* slots[160];
    void* thunks;
    unsigned long long hits[160];  // how often each forwarded slot was called (overridden slots are not counted)
    static XboxObjectProxy* Create(IUnknown* real) {
        auto* p = (XboxObjectProxy*)VirtualAlloc(nullptr, sizeof(XboxObjectProxy), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        p->real = real;
        p->vtbl = p->slots;
        auto* code = (unsigned char*)VirtualAlloc(nullptr, 160 * 32, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        p->thunks = code;
        for (int i = 0; i < 160; ++i) {
            unsigned char* t = code + i * 32;
            *t++ = 0x48; *t++ = 0xb8;                                   // mov rax, &hits[i]
            unsigned long long a = (unsigned long long)&p->hits[i];
            memcpy(t, &a, 8); t += 8;
            *t++ = 0xf0; *t++ = 0x48; *t++ = 0xff; *t++ = 0x00;        // lock inc qword ptr [rax]
            const unsigned char head[] = { 0x48, 0x8b, 0x49, 0x08,        // mov rcx, [rcx+8]   (real object)
                                           0x48, 0x8b, 0x01,              // mov rax, [rcx]     (its vtable)
                                           0xff, 0xa0 };                  // jmp [rax+disp32]
            memcpy(t, head, sizeof(head));
            *(int*)(t + sizeof(head)) = i * 8;
            p->slots[i] = code + i * 32;
        }
        return p;
    }
    void PrintHits(const char* what) {
        printf("  %s: forwarded slots called:", what);
        for (int i = 0; i < 160; ++i) if (hits[i]) printf(" %d(x%llu)", i, hits[i]);
        printf("\n");
    }
    void Override(int slot, void* fn) { slots[slot] = fn; }
};

typedef XboxObjectProxy XboxDeviceProxy;
