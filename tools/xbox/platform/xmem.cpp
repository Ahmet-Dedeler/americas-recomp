// Stand-in for the Xbox GDK xmem.dll, covering every export the package's DLLs import:
//   Bink2Scarlett.dll            XMemAlloc, XMemFree
//   amd_fidelityfx_upscaler_xbox XMemAlloc, XMemFree, XMemGetWorkingSetStatistics
//   XboxBvhBuilder.dll           XMemVirtualQuery
// Signatures were taken from the callers' machine code (dumpbin /disasm), not from headers:
//   XMemAlloc(size, attributes)          rcx = size rounded up to 256, rdx = one global attribute word (0x5e50000 in Bink)
//   XMemFree(pointer, attributes)        same attribute word
//   XMemGetWorkingSetStatistics(kind, out)   kind = 0x20000001 in both call sites, out = a >= 16-byte struct whose qword at +8 the caller
//                                        uses as a size; non-zero return = success (the caller maps 0 to "no data")
//   XMemVirtualQuery(process, address, out)  out is a zeroed 64-byte struct that starts like MEMORY_BASIC_INFORMATION (State at +0x20 is tested
//                                        against MEM_COMMIT) plus a field at +0x30 whose low two bits the caller tests; non-zero = success
// What is NOT known: the meaning of the attribute bits, of the working-set struct beyond +8, and of the +0x30 field. The values below are
// guesses chosen to let the callers proceed; XMEM_LOG=<file> logs every call so a real caller's use can be checked.
#include <windows.h>
#include <cstdio>
static void Log(const char* what, void* p, ULONGLONG a, ULONGLONG b) {
    char path[MAX_PATH];
    if (!GetEnvironmentVariableA("XMEM_LOG", path, sizeof(path))) return;
    FILE* f = nullptr;
    if (fopen_s(&f, path, "a") == 0 && f) { fprintf(f, "%s ptr=%p a=0x%llx b=0x%llx\n", what, p, a, b); fclose(f); }
}
extern "C" {
__declspec(dllexport) void* XMemAlloc(SIZE_T size, ULONGLONG attrs) {
    // VirtualAlloc returns 64 KiB aligned zeroed memory, which satisfies any alignment the caller can ask for
    void* p = VirtualAlloc(nullptr, size ? size : 1, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    Log("alloc", p, size, attrs);
    return p;
}
__declspec(dllexport) void XMemFree(void* p, ULONGLONG attrs) {
    Log("free ", p, 0, attrs);
    if (p) VirtualFree(p, 0, MEM_RELEASE);
}
__declspec(dllexport) BOOL XMemGetWorkingSetStatistics(DWORD kind, ULONGLONG* out) {
    MEMORYSTATUSEX m{sizeof(m)};
    if (!out || !GlobalMemoryStatusEx(&m)) return FALSE;
    out[0] = m.ullTotalPhys;     // guess: total
    out[1] = m.ullAvailPhys;     // the qword the upscaler reads (+8): treated here as the memory still available
    Log("wsstat", out, kind, out[1]);
    return TRUE;
}
__declspec(dllexport) BOOL XMemVirtualQuery(HANDLE process, const void* address, void* out) {
    MEMORY_BASIC_INFORMATION mbi{};
    if (!out || VirtualQueryEx(process ? process : GetCurrentProcess(), address, &mbi, sizeof(mbi)) == 0) return FALSE;
    memset(out, 0, 64);
    memcpy(out, &mbi, sizeof(mbi));                                   // BaseAddress, AllocationBase, protections, RegionSize, State at +0x20
    *reinterpret_cast<DWORD*>(static_cast<char*>(out) + 0x30) = (mbi.State == MEM_COMMIT) ? 3u : 0u;   // guess: bits 0-1 = readable / writable
    Log("vquery", const_cast<void*>(address), mbi.RegionSize, mbi.State);
    return TRUE;
}
}
