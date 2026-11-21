// dxasm: assemble DXIL text (LLVM assembly as printed by `dxc -dumpbin`) into a DXIL container and sign it with dxil.dll, so a
// hand-edited or machine-rewritten shader can be loaded again by a PC D3D12 runtime.
// build: cl /nologo /EHsc /O2 /std:c++17 dxasm.cpp /Fe:dxasm.exe          usage: dxasm in.ll out.dxil
// needs dxcompiler.dll and dxil.dll next to the exe (or on PATH). Prints the validator's message if signing fails.
#include <windows.h>
#include <dxcapi.h>
#include <wrl/client.h>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
using Microsoft::WRL::ComPtr;

int main(int argc, char** argv) {
    if (argc < 3) { printf("usage: dxasm in.ll out.dxil\n"); return 2; }
    std::ifstream f(argv[1], std::ios::binary);
    std::stringstream ss; ss << f.rdbuf();
    std::string text = ss.str();
    HMODULE comp = LoadLibraryA("dxcompiler.dll"), val = LoadLibraryA("dxil.dll");
    if (!comp || !val) { printf("cannot load dxcompiler.dll / dxil.dll\n"); return 2; }
    auto cc = (DxcCreateInstanceProc)GetProcAddress(comp, "DxcCreateInstance");
    auto cv = (DxcCreateInstanceProc)GetProcAddress(val, "DxcCreateInstance");
    ComPtr<IDxcLibrary> lib; ComPtr<IDxcAssembler> as; ComPtr<IDxcValidator> v;
    if (FAILED(cc(CLSID_DxcLibrary, IID_PPV_ARGS(&lib))) || FAILED(cc(CLSID_DxcAssembler, IID_PPV_ARGS(&as))) || FAILED(cv(CLSID_DxcValidator, IID_PPV_ARGS(&v)))) { printf("cannot create DXC objects\n"); return 2; }
    ComPtr<IDxcBlobEncoding> src;
    lib->CreateBlobWithEncodingFromPinned(text.data(), (UINT32)text.size(), CP_UTF8, &src);
    ComPtr<IDxcOperationResult> r;
    as->AssembleToContainer(src.Get(), &r);
    HRESULT st = S_OK; r->GetStatus(&st);
    ComPtr<IDxcBlob> blob; r->GetResult(&blob);
    if (FAILED(st) || !blob) {
        ComPtr<IDxcBlobEncoding> err, err8; r->GetErrorBuffer(&err);
        if (err) { lib->GetBlobAsUtf8(err.Get(), &err8); if (err8) printf("assemble failed: %.*s\n", (int)err8->GetBufferSize(), (const char*)err8->GetBufferPointer()); }
        return 1;
    }
    ComPtr<IDxcOperationResult> vr;
    v->Validate(blob.Get(), DxcValidatorFlags_InPlaceEdit, &vr);       // signs the container in place
    HRESULT vs = S_OK; vr->GetStatus(&vs);
    if (FAILED(vs)) {
        ComPtr<IDxcBlobEncoding> err, err8; vr->GetErrorBuffer(&err);
        if (err) { lib->GetBlobAsUtf8(err.Get(), &err8); if (err8) printf("validation failed: %.*s\n", (int)err8->GetBufferSize(), (const char*)err8->GetBufferPointer()); }
        return 1;
    }
    ComPtr<IDxcBlob> signedBlob;
    if (FAILED(vr->GetResult(&signedBlob)) || !signedBlob) signedBlob = blob;   // in-place edit: the input blob now holds the signed container
    std::ofstream o(argv[2], std::ios::binary);
    o.write((const char*)signedBlob->GetBufferPointer(), signedBlob->GetBufferSize());
    printf("wrote %s (%zu bytes)\n", argv[2], (size_t)signedBlob->GetBufferSize());
    return 0;
}
