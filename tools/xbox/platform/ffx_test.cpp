// Runs the package's own FidelityFX loader + upscaler DLLs (Xbox builds, unmodified x64 code) on a PC D3D12 device.
// No recompilation: both are ordinary x64 PE files, they load next to the stand-in xmem.dll / d3d12_xs.dll.
// Stages: 1 version query (no device)   2 create context on a PC device   3 destroy it.
// Struct layouts follow AMD's public ffx_api.h; the Xbox backend extension type (0x01000002) and the effect id (0x10000) come from
// reading the loader and upscaler code (see Xbox2Windows docs/experiments.md for an independent account of the same paths).
// build: cl /nologo /EHsc /O2 /std:c++17 ffx_test.cpp d3d12.lib dxgi.lib /Fe:ffx_test.exe    usage: ffx_test [warp] [nodebug] [flags=0x200]
#include <windows.h>
#include <d3d12.h>
#include <dxgi1_6.h>
#include <wrl/client.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
using Microsoft::WRL::ComPtr;
#include "xbox_device_proxy.h"
#include "xbox_device_overrides.h"

// Xbox device vtable slot 76 (+0x260) is the resource creation call: heap properties, heap flags, resource description, initial state,
// optimized clear value, iid, output. Same argument order as the PC CreateCommittedResource (slot 27), but the description carries an
// Xbox texture layout (0x101) and extension words, which the PC runtime would reject.
static bool g_log = false;
static XboxObjectProxy* g_heaps[16]; static int g_nheaps = 0;

// Descriptor heaps: on Xbox GetCPU/GPUDescriptorHandleForHeapStart (slots 9, 10) return the 8-byte handle in RAX; the PC runtime wants a
// hidden return pointer in RDX, so called the Xbox way it writes through a garbage pointer.
static SIZE_T __fastcall HeapCpuStart(XboxObjectProxy* self) { return ((ID3D12DescriptorHeap*)self->real)->GetCPUDescriptorHandleForHeapStart().ptr; }
static UINT64 __fastcall HeapGpuStart(XboxObjectProxy* self) { return ((ID3D12DescriptorHeap*)self->real)->GetGPUDescriptorHandleForHeapStart().ptr; }
// Device slot 14 CreateDescriptorHeap: same arguments as PC, but the heap handed back is wrapped in the Xbox-ABI stand-in.
static HRESULT __fastcall Slot14(XboxObjectProxy* self, const D3D12_DESCRIPTOR_HEAP_DESC* desc, REFIID riid, void** out) {
    HRESULT hr = ((ID3D12Device*)self->real)->CreateDescriptorHeap(desc, riid, out);
    if (SUCCEEDED(hr) && out && *out && riid == __uuidof(ID3D12DescriptorHeap)) {
        auto* h = XboxObjectProxy::Create((IUnknown*)*out);
        h->Override(9, (void*)HeapCpuStart);
        h->Override(10, (void*)HeapGpuStart);
        if (g_nheaps < 16) g_heaps[g_nheaps++] = h;
        *out = h;
    }
    return hr;
}
static HRESULT __fastcall Slot76(XboxDeviceProxy* self, const D3D12_HEAP_PROPERTIES* hp, UINT heapFlags, const unsigned char* desc, UINT state, const D3D12_CLEAR_VALUE* cv, REFIID riid, void** out) {
    D3D12_RESOURCE_DESC d;
    memcpy(&d, desc, sizeof(d));
    if (g_log) {
        printf("  [slot 76] heap type %d flags 0x%x state 0x%x, desc dim %d %llux%u fmt %d layout 0x%x flags 0x%x;", (int)hp->Type, heapFlags, state, (int)d.Dimension, d.Width, d.Height, (int)d.Format, (unsigned)d.Layout, (unsigned)d.Flags);
        printf(" words after the 56-byte PC struct:");
        for (int i = 56; i < 80; i += 4) printf(" %08x", *(const unsigned*)(desc + i));
        printf("\n");
    }
    return xbox_overrides::CreateResource((ID3D12Device*)self->real, hp, heapFlags, desc, state, cv, riid, out);
}

struct Header { uint64_t type; Header* next; };
struct QueryVersions { Header h; uint64_t createDescType; void* device; uint64_t* outputCount; uint64_t* versionIds; const char** versionNames; };
struct CreateUpscale { Header h; uint32_t flags; uint32_t maxRenderW, maxRenderH, maxUpscaleW, maxUpscaleH; void* fpMessage; };
struct BackendDx12 { Header h; void* device; };
typedef uint32_t(__cdecl* QueryFn)(void** ctx, Header* desc);
typedef uint32_t(__cdecl* CreateFn)(void** ctx, Header* desc, const void* alloc);
typedef uint32_t(__cdecl* DestroyFn)(void** ctx, const void* alloc);

static void PrintStack(const char* why) {
    void* frames[32];
    USHORT n = CaptureStackBackTrace(0, 32, frames, nullptr);
    printf("  %s, call stack:\n", why);
    for (USHORT i = 0; i < n; ++i) {
        HMODULE mod = nullptr;
        GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCSTR)frames[i], &mod);
        char name[MAX_PATH] = "?";
        if (mod) GetModuleFileNameA(mod, name, MAX_PATH);
        const char* base = strrchr(name, 0x5c);
        printf("    %s+0x%llx\n", base ? base + 1 : name, (unsigned long long)((char*)frames[i] - (char*)mod));
    }
    fflush(stdout);
}
static LONG CALLBACK Trace(EXCEPTION_POINTERS* p) {
    DWORD c = p->ExceptionRecord->ExceptionCode;
    if (c == 0xe06d7363 || c == 0xc0000005 || c == 0xc000001d || c == 0xc0000094) { char w[64]; sprintf_s(w, "exception 0x%lx at %p", c, p->ExceptionRecord->ExceptionAddress); PrintStack(w); }
    return EXCEPTION_CONTINUE_SEARCH;
}
static unsigned long SehQuery(QueryFn f, Header* d, uint32_t* rc) { __try { *rc = f(nullptr, d); return 0; } __except (EXCEPTION_EXECUTE_HANDLER) { return GetExceptionCode(); } }
static unsigned long SehCreate(CreateFn f, void** ctx, Header* d, uint32_t* rc) { __try { *rc = f(ctx, d, nullptr); return 0; } __except (EXCEPTION_EXECUTE_HANDLER) { return GetExceptionCode(); } }
static unsigned long SehDestroy(DestroyFn f, void** ctx, uint32_t* rc) { __try { *rc = f(ctx, nullptr); return 0; } __except (EXCEPTION_EXECUTE_HANDLER) { return GetExceptionCode(); } }

int main(int argc, char** argv) {
    bool warp = false, nodebug = false;
    uint32_t flags = 0;
    for (int i = 1; i < argc; ++i) {
        warp |= !strcmp(argv[i], "warp"); nodebug |= !strcmp(argv[i], "nodebug");
        if (!strncmp(argv[i], "flags=", 6)) flags = strtoul(argv[i] + 6, nullptr, 0);
    }
    AddVectoredExceptionHandler(1, Trace);
    HMODULE m = LoadLibraryA(getenv("FFX_DLL") ? getenv("FFX_DLL") : "amd_fidelityfx_loader_xbox.dll");
    if (!m) { printf("LoadLibrary loader failed %lu\n", GetLastError()); return 1; }
    auto query = (QueryFn)GetProcAddress(m, "ffxQuery");
    auto create = (CreateFn)GetProcAddress(m, "ffxCreateContext");
    auto destroy = (DestroyFn)GetProcAddress(m, "ffxDestroyContext");
    if (!query || !create || !destroy) { printf("loader exports missing\n"); return 1; }

    // stage 1: version enumeration (null context, query type 4, create-desc type = upscale)
    uint64_t count = 0, id = 0;
    const char* name = nullptr;
    QueryVersions q = {};
    q.h.type = 4; q.createDescType = 0x10000; q.outputCount = &count;
    uint32_t rc = 0;
    if (unsigned long ex = SehQuery(query, &q.h, &rc)) { printf("stage 1: query raised 0x%lx\n", ex); }
    else {
        printf("stage 1: ffxQuery(count) -> %u, count %llu\n", rc, count);
        if (rc == 0 && count) {
            uint64_t cap = 1;
            q.outputCount = &cap; q.versionIds = &id; q.versionNames = &name;
            rc = 0; SehQuery(query, &q.h, &rc);
            printf("         ffxQuery(ids) -> %u, id 0x%llx, name %s\n", rc, id, name ? name : "(null)");
        }
    }

    // stage 2: create a context on a PC device
    ComPtr<ID3D12Debug> dbg;
    if (!nodebug && SUCCEEDED(D3D12GetDebugInterface(IID_PPV_ARGS(&dbg)))) dbg->EnableDebugLayer();
    ComPtr<IDXGIFactory4> fac;
    CreateDXGIFactory1(IID_PPV_ARGS(&fac));
    ComPtr<IDXGIAdapter> ad;
    if (warp) fac->EnumWarpAdapter(IID_PPV_ARGS(&ad)); else fac->EnumAdapters(0, &ad);
    DXGI_ADAPTER_DESC d; ad->GetDesc(&d);
    printf("adapter: %ls\n", d.Description);
    ComPtr<ID3D12Device> dev;
    if (FAILED(D3D12CreateDevice(ad.Get(), D3D_FEATURE_LEVEL_12_1, IID_PPV_ARGS(&dev)))) { printf("no D3D12 device\n"); return 1; }
    BackendDx12 be = {};
    g_log = getenv("FFX_LOG") != nullptr;
    XboxDeviceProxy* proxy = XboxDeviceProxy::Create(dev.Get());
    proxy->Override(76, (void*)Slot76);
    proxy->Override(14, (void*)Slot14);
    proxy->Override(18, (void*)xbox_overrides::Slot18);
    proxy->Override(19, (void*)xbox_overrides::Slot19);
    be.h.type = 0x01000002; be.device = proxy;      // the DLLs get the Xbox-layout stand-in, not the PC device
    CreateUpscale cu = {};
    cu.h.type = 0x10000; cu.h.next = &be.h;
    cu.flags = flags; cu.maxRenderW = 1280; cu.maxRenderH = 720; cu.maxUpscaleW = 1920; cu.maxUpscaleH = 1080;
    void* ctx = nullptr;
    printf("stage 2: ffxCreateContext(upscale 1280x720 -> 1920x1080, flags 0x%x)...\n", flags); fflush(stdout);
    rc = 0;
    if (unsigned long ex = SehCreate(create, &ctx, &cu.h, &rc)) { printf("  create raised exception 0x%lx\n", ex); return 2; }
    printf("  returned %u, context %p\n", rc, ctx);
    proxy->PrintHits("device");
    for (int i = 0; i < g_nheaps; ++i) { char w[32]; sprintf_s(w, "heap %d", i); g_heaps[i]->PrintHits(w); }
    if (ctx) {
        rc = 0;
        if (unsigned long ex = SehDestroy(destroy, &ctx, &rc)) printf("stage 3: destroy raised 0x%lx\n", ex);
        else printf("stage 3: ffxDestroyContext -> %u, context now %p\n", rc, ctx);
    }
    // debug layer: everything the PC runtime had to say about the calls the package made
    ComPtr<ID3D12InfoQueue> iq;
    if (SUCCEEDED(dev.As(&iq))) {
        UINT64 n = iq->GetNumStoredMessages();
        int err = 0, warn = 0;
        for (UINT64 i = 0; i < n; ++i) {
            SIZE_T len = 0; iq->GetMessage(i, nullptr, &len);
            auto* mm = (D3D12_MESSAGE*)malloc(len); iq->GetMessage(i, mm, &len);
            if (mm->Severity <= D3D12_MESSAGE_SEVERITY_ERROR) ++err; else if (mm->Severity == D3D12_MESSAGE_SEVERITY_WARNING) ++warn;
            if (i < 12) printf("  [d3d12 %d] %.220s\n", (int)mm->Severity, mm->pDescription);
            free(mm);
        }
        printf("debug layer: %llu messages, %d errors, %d warnings\n", n, err, warn);
    } else printf("debug layer: not active\n");
    return 0;
}
