// Loads Bink2Scarlett.dll (next to this exe, with our xmem.dll) and decodes every frame of a carved video.
#include <windows.h>
#include <cstdio>
#include <cstdint>
typedef void* (__cdecl* OpenFn)(const char*, uint32_t);
typedef int (__cdecl* DoFrameFn)(void*);
typedef void (__cdecl* NextFn)(void*);
typedef void (__cdecl* CloseFn)(void*);
typedef int (__cdecl* CopyFn)(void*, void*, int, uint32_t, uint32_t, uint32_t, uint32_t);
static unsigned char buf[2560 * 1440 * 4];
int main(int argc, char** argv) {
    if (argc < 2) return 2;
    HMODULE m = LoadLibraryA("Bink2Scarlett.dll");
    if (!m) { printf("LoadLibrary failed %lu\n", GetLastError()); return 1; }
    auto open = (OpenFn)GetProcAddress(m, "BinkOpen");
    auto doFrame = (DoFrameFn)GetProcAddress(m, "BinkDoFrame");
    auto next = (NextFn)GetProcAddress(m, "BinkNextFrame");
    auto close = (CloseFn)GetProcAddress(m, "BinkClose");
    auto copy = (CopyFn)GetProcAddress(m, "BinkCopyToBuffer");
    uint32_t flags = argc > 2 ? strtoul(argv[2], nullptr, 0) : 0;
    void* b = open(argv[1], flags);
    if (!b) { printf("BinkOpen failed\n"); return 1; }
    const uint32_t* h = (const uint32_t*)b;
    printf("opened: %ux%u, %u frames\n", h[0], h[1], h[2]);
    unsigned frames = h[2], bad = 0;
    for (unsigned i = 0; i < frames; ++i) {
        int r = doFrame(b);
        if (r) ++bad;
        if (i == 0 || i == frames / 2) {
            int c = copy(b, buf, 2560 * 4, 1440, 0, 0, 3);
            unsigned long long sum = 0;
            for (size_t k = 0; k < sizeof(buf); ++k) sum += buf[k];
            printf("frame %u: DoFrame %d, CopyToBuffer %d, byte sum %llu\n", i, r, c, sum);
            char name[64];
            sprintf_s(name, "frame%u.raw", i);
            FILE* f;
            if (!fopen_s(&f, name, "wb")) { fwrite(buf, 1, sizeof(buf), f); fclose(f); }
        }
        next(b);
    }
    printf("decoded %u frames, DoFrame errors: %u\n", frames, bad);
    close(b);
    return 0;
}
