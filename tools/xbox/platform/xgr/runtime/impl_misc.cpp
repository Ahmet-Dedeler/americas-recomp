#define GTX_SHIM_DLL "xgameruntime.dll"
#include <windows.h>

#include "xgr_internal.h"

namespace gtx::xgr::misc {
namespace {

HRESULT XGameEventWrite_impl(void*, void* user, const char* serviceConfigId, const char* playSessionId, const char* eventName,
                             const char* dimensionsJson, const char* measurementsJson) {
  (void)dimensionsJson;
  (void)measurementsJson;
  if (!user || !serviceConfigId || !playSessionId || !eventName) return E_INVALIDARG;
  return S_OK;
}

const Override kOverrides[] = {
    {"XGameEventWrite", reinterpret_cast<void*>(&XGameEventWrite_impl)},
};

}  // namespace

const Override* overrides(size_t* count) {
  *count = sizeof(kOverrides) / sizeof(kOverrides[0]);
  return kOverrides;
}

}  // namespace gtx::xgr::misc
