// d3d12_xs.dll stand-in. The package's DLLs import exactly two functions from it (XboxBvhBuilder: D3D12SerializeVersionedRootSignature,
// amd_fidelityfx_upscaler_xbox: D3D12CreateRootSignatureDeserializer) and the PC d3d12.dll exports both with the same signature, so each
// call is forwarded. (A .def forwarder would be simpler, but link.exe read it as an unresolved symbol, so this resolves d3d12.dll at run time.)
// The game executable will import far more from d3d12_xs (Xbox device creation and the XBOX command-list extensions): not known until it exists.
#include <windows.h>
static FARPROC Pc(const char* name) {
    static HMODULE m = LoadLibraryW(L"d3d12.dll");
    return m ? GetProcAddress(m, name) : nullptr;
}
extern "C" {
__declspec(dllexport) HRESULT WINAPI D3D12SerializeVersionedRootSignature(const void* desc, void** blob, void** error) {
    using Fn = HRESULT(WINAPI*)(const void*, void**, void**);
    auto f = reinterpret_cast<Fn>(Pc("D3D12SerializeVersionedRootSignature"));
    return f ? f(desc, blob, error) : E_NOTIMPL;
}
__declspec(dllexport) HRESULT WINAPI D3D12CreateRootSignatureDeserializer(const void* data, SIZE_T size, const GUID& iid, void** out) {
    using Fn = HRESULT(WINAPI*)(const void*, SIZE_T, const GUID&, void**);
    auto f = reinterpret_cast<Fn>(Pc("D3D12CreateRootSignatureDeserializer"));
    return f ? f(data, size, iid, out) : E_NOTIMPL;
}
}
