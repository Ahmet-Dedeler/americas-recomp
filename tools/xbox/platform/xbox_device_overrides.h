// The Xbox device deviations learned so far, applied to a proxy (xbox_device_proxy.h) around a PC D3D12 device:
//   slot 14 CreateDescriptorHeap: same arguments as PC; the heap returned is wrapped so GetCPU/GPUDescriptorHandleForHeapStart (slots 9, 10) return the
//           8-byte handle in RAX as on Xbox (the PC runtime wants a hidden return pointer in RDX and would write through garbage)
//   slot 76 resource creation: same argument order as the PC CreateCommittedResource (slot 27), but the description carries an Xbox texture layout (0x101)
//           and extension words; the layout becomes UNKNOWN (the PC driver chooses) and only the 56-byte PC struct is read
// Other slots are forwarded to the same PC slot (see PLATFORM_SURFACE.md for the ones confirmed to match: 1, 14, 15, 16, 18, 19, 27, 38; 62 is pipeline creation).
#pragma once
#include "xbox_device_proxy.h"

namespace xbox_overrides {
static SIZE_T __fastcall HeapCpuStart(XboxObjectProxy* self) { return ((ID3D12DescriptorHeap*)self->real)->GetCPUDescriptorHandleForHeapStart().ptr; }
static UINT64 __fastcall HeapGpuStart(XboxObjectProxy* self) { return ((ID3D12DescriptorHeap*)self->real)->GetGPUDescriptorHandleForHeapStart().ptr; }
static HRESULT __fastcall Slot14(XboxObjectProxy* self, const D3D12_DESCRIPTOR_HEAP_DESC* desc, REFIID riid, void** out) {
    HRESULT hr = ((ID3D12Device*)self->real)->CreateDescriptorHeap(desc, riid, out);
    if (SUCCEEDED(hr) && out && *out && riid == __uuidof(ID3D12DescriptorHeap)) {
        auto* h = XboxObjectProxy::Create((IUnknown*)*out, "descriptor heap");
        h->Override(9, (void*)HeapCpuStart);
        h->Override(10, (void*)HeapGpuStart);
        *out = h;
    }
    return hr;
}
static DXGI_FORMAT UavFallback(ID3D12Device* dev, const D3D12_RESOURCE_DESC& d) {
    if (d.Format != DXGI_FORMAT_R9G9B9E5_SHAREDEXP || !(d.Flags & D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS)) return DXGI_FORMAT_UNKNOWN;
    if (GetEnvironmentVariableA("XS_NO_FORMAT_FALLBACK", nullptr, 0)) return DXGI_FORMAT_UNKNOWN;
    D3D12_FEATURE_DATA_FORMAT_SUPPORT fs = { d.Format };
    if (SUCCEEDED(dev->CheckFeatureSupport(D3D12_FEATURE_FORMAT_SUPPORT, &fs, sizeof(fs))) && (fs.Support1 & D3D12_FORMAT_SUPPORT1_TYPED_UNORDERED_ACCESS_VIEW)) return DXGI_FORMAT_UNKNOWN;
    return DXGI_FORMAT_R16G16B16A16_FLOAT;
}
static HRESULT CreateResource(ID3D12Device* dev, const D3D12_HEAP_PROPERTIES* hp, UINT heapFlags, const unsigned char* desc, UINT state, const D3D12_CLEAR_VALUE* cv, REFIID riid, void** out) {
    D3D12_RESOURCE_DESC d;
    memcpy(&d, desc, sizeof(d));
    if ((unsigned)d.Layout == 0x101) d.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;
    D3D12_CLEAR_VALUE c;
    if (DXGI_FORMAT f = UavFallback(dev, d)) {
        d.Format = f;
        if (cv) { c = *cv; if (c.Format == DXGI_FORMAT_R9G9B9E5_SHAREDEXP) c.Format = f; cv = &c; }
    }
    return dev->CreateCommittedResource(hp, (D3D12_HEAP_FLAGS)heapFlags, &d, (D3D12_RESOURCE_STATES)state, cv, riid, out);
}
static HRESULT __fastcall Slot76(XboxObjectProxy* self, const D3D12_HEAP_PROPERTIES* hp, UINT heapFlags, const unsigned char* desc, UINT state, const D3D12_CLEAR_VALUE* cv, REFIID riid, void** out) {
    return CreateResource((ID3D12Device*)self->real, hp, heapFlags, desc, state, cv, riid, out);
}
static bool Substituted(ID3D12Resource* r, DXGI_FORMAT viewFormat) {
    return r && viewFormat == DXGI_FORMAT_R9G9B9E5_SHAREDEXP && r->GetDesc().Format == DXGI_FORMAT_R16G16B16A16_FLOAT;
}
static void __fastcall Slot18(XboxObjectProxy* self, ID3D12Resource* r, const D3D12_SHADER_RESOURCE_VIEW_DESC* desc, SIZE_T handle) {
    D3D12_SHADER_RESOURCE_VIEW_DESC v;
    if (desc && Substituted(r, desc->Format)) { v = *desc; v.Format = DXGI_FORMAT_R16G16B16A16_FLOAT; desc = &v; }
    ((ID3D12Device*)self->real)->CreateShaderResourceView(r, desc, D3D12_CPU_DESCRIPTOR_HANDLE{ handle });
}
static void __fastcall Slot19(XboxObjectProxy* self, ID3D12Resource* r, ID3D12Resource* counter, const D3D12_UNORDERED_ACCESS_VIEW_DESC* desc, SIZE_T handle) {
    D3D12_UNORDERED_ACCESS_VIEW_DESC v;
    if (desc && Substituted(r, desc->Format)) { v = *desc; v.Format = DXGI_FORMAT_R16G16B16A16_FLOAT; desc = &v; }
    ((ID3D12Device*)self->real)->CreateUnorderedAccessView(r, counter, desc, D3D12_CPU_DESCRIPTOR_HANDLE{ handle });
}
// Wrap a PC device as an Xbox device object.
static XboxObjectProxy* WrapDevice(ID3D12Device* dev) {
    auto* p = XboxObjectProxy::Create(dev, "device");
    p->Override(14, (void*)Slot14);
    p->Override(18, (void*)Slot18);
    p->Override(19, (void*)Slot19);
    p->Override(76, (void*)Slot76);
    return p;
}
}  // namespace xbox_overrides
