// Offline local user for the xgameruntime stand-in: one always-signed-in adult local profile, no network and no UI.
// Written from the interface signatures (IXUserImpl* in the Xodus IDLs); the XAsync parts use the vendored GTX task layer (MIT, see GTX_LICENSE.txt).
// Environment: XGR_GAMERTAG, XGR_USER_ID (decimal) override the identity.
#define GTX_SHIM_DLL "xgameruntime.dll"
#include <windows.h>

#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#include "gtx/log.h"
#include "xgr_internal.h"
#include "xtask.h"

namespace gtx::xgr::user {
namespace {

struct User {
  std::atomic<long> refs{1};
  uint64_t id;
  uint64_t localId;
};
struct LocalId { uint64_t value; };

User* g_user = nullptr;
SRWLOCK g_lock = SRWLOCK_INIT;

User* the_user() {
  AcquireSRWLockExclusive(&g_lock);
  if (!g_user) {
    g_user = new User;
    const char* e = std::getenv("XGR_USER_ID");
    g_user->id = e ? std::strtoull(e, nullptr, 10) : 2535400000000001ull;   // looks like an XUID, obviously synthetic
    g_user->localId = 1;
  }
  User* u = g_user;
  ReleaseSRWLockExclusive(&g_lock);
  return u;
}

User* from(void* h) { return reinterpret_cast<User*>(h); }

HRESULT XUserGetMaxUsers_impl(void*, uint32_t* n) {
  if (!n) return E_INVALIDARG;
  *n = 4;
  return S_OK;
}

// add: begin + schedule; the provider produces the handle when the result is read (GetResult), so each caller gets its own reference
HRESULT CALLBACK add_provider(task::AsyncOp op, const task::AsyncProviderData* d) {
  switch (op) {
    case task::AsyncOp::DoWork:
      task::AsyncComplete(d->async, S_OK, sizeof(void*));
      break;
    case task::AsyncOp::GetResult: {
      User* u = the_user();
      u->refs++;
      void* h = u;
      std::memcpy(d->buffer, &h, sizeof(h));
      break;
    }
    default:
      break;
  }
  return S_OK;
}
HRESULT XUserAddAsync_impl(void*, uint32_t options, task::AsyncBlock* a) {
  (void)options;
  HRESULT hr = task::AsyncBegin(a, nullptr, reinterpret_cast<const void*>(&XUserAddAsync_impl), "XUserAddAsync", add_provider);
  if (FAILED(hr)) return hr;
  return task::AsyncSchedule(a, 0);
}
HRESULT XUserAddResult_impl(void*, task::AsyncBlock* a, void** out) {
  return task::AsyncGetResult(a, reinterpret_cast<const void*>(&XUserAddAsync_impl), sizeof(void*), out, nullptr);
}

HRESULT XUserDuplicateHandle_impl(void*, void* h, void** out) {
  if (!h || !out) return E_INVALIDARG;
  from(h)->refs++;
  *out = h;
  return S_OK;
}
void XUserCloseHandle_impl(void*, void* h) {
  if (h) from(h)->refs--;      // the single process-wide user is never freed
}
int32_t XUserCompare_impl(void*, void* a, void* b) { return a == b ? 0 : (a < b ? -1 : 1); }
HRESULT XUserGetLocalId_impl(void*, void* h, LocalId* out) {
  if (!h || !out) return E_INVALIDARG;
  out->value = from(h)->localId;
  return S_OK;
}
HRESULT XUserFindUserByLocalId_impl(void*, LocalId id, void** out) {
  if (!out) return E_INVALIDARG;
  User* u = the_user();
  if (id.value != u->localId) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
  u->refs++;
  *out = u;
  return S_OK;
}
HRESULT XUserGetId_impl(void*, void* h, uint64_t* out) {
  if (!h || !out) return E_INVALIDARG;
  *out = from(h)->id;
  return S_OK;
}
HRESULT XUserFindUserById_impl(void*, uint64_t id, void** out) {
  if (!out) return E_INVALIDARG;
  User* u = the_user();
  if (id != u->id) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
  u->refs++;
  *out = u;
  return S_OK;
}
HRESULT XUserGetIsGuest_impl(void*, void* h, uint8_t* out) {
  if (!h || !out) return E_INVALIDARG;
  *out = 0;
  return S_OK;
}
HRESULT XUserGetState_impl(void*, void* h, uint32_t* out) {
  if (!h || !out) return E_INVALIDARG;
  *out = 0;   // XUserState_SignedIn
  return S_OK;
}
HRESULT XUserGetAgeGroup_impl(void*, void* h, uint32_t* out) {
  if (!h || !out) return E_INVALIDARG;
  *out = 3;   // XUserAgeGroup_Adult
  return S_OK;
}
HRESULT XUserCheckPrivilege_impl(void*, void* h, uint32_t, uint32_t, uint8_t* has) {
  if (!h || !has) return E_INVALIDARG;
  *has = 1;   // offline profile: every privilege granted, so no title is blocked on a missing one
  return S_OK;
}
HRESULT XUserGetGamertag_impl(void*, void* h, uint32_t component, size_t size, char* out, size_t* used) {
  if (!h || !out || component > 3) return E_INVALIDARG;
  const char* e = std::getenv("XGR_GAMERTAG");
  const char* tag = e && *e ? e : "Player";
  size_t n = component == 2 ? 0 : std::strlen(tag);
  if (component == 0 && n > 15) n = 15;
  if (size < n + 1) return HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER);
  std::memcpy(out, tag, n);
  out[n] = 0;
  if (used) *used = n + 1;
  return S_OK;
}
uint8_t XUserIsStoreUser_impl(void*, void*) { return 0; }
uint8_t XUserIsSignOutPresent_impl(void*) { return 0; }

// change events never fire (the one user never signs out); registration succeeds so titles that wait for sign-in events proceed
HRESULT XUserRegisterForChangeEvent_impl(void*, void*, void*, void*, task::RegistrationToken* tok) {
  static std::atomic<uint64_t> next{1};
  if (tok) tok->token = next++;
  return S_OK;
}
uint8_t XUserUnregisterForChangeEvent_impl(void*, task::RegistrationToken, uint8_t) { return 1; }

const Override kOverrides[] = {
    {"XUserGetMaxUsers", reinterpret_cast<void*>(&XUserGetMaxUsers_impl)},
    {"XUserAddAsync", reinterpret_cast<void*>(&XUserAddAsync_impl)},
    {"XUserAddResult", reinterpret_cast<void*>(&XUserAddResult_impl)},
    {"XUserDuplicateHandle", reinterpret_cast<void*>(&XUserDuplicateHandle_impl)},
    {"XUserCloseHandle", reinterpret_cast<void*>(&XUserCloseHandle_impl)},
    {"XUserCompare", reinterpret_cast<void*>(&XUserCompare_impl)},
    {"XUserGetLocalId", reinterpret_cast<void*>(&XUserGetLocalId_impl)},
    {"XUserFindUserByLocalId", reinterpret_cast<void*>(&XUserFindUserByLocalId_impl)},
    {"XUserGetId", reinterpret_cast<void*>(&XUserGetId_impl)},
    {"XUserFindUserById", reinterpret_cast<void*>(&XUserFindUserById_impl)},
    {"XUserGetIsGuest", reinterpret_cast<void*>(&XUserGetIsGuest_impl)},
    {"XUserGetState", reinterpret_cast<void*>(&XUserGetState_impl)},
    {"XUserGetAgeGroup", reinterpret_cast<void*>(&XUserGetAgeGroup_impl)},
    {"XUserCheckPrivilege", reinterpret_cast<void*>(&XUserCheckPrivilege_impl)},
    {"XUserGetGamertag", reinterpret_cast<void*>(&XUserGetGamertag_impl)},
    {"XUserIsStoreUser", reinterpret_cast<void*>(&XUserIsStoreUser_impl)},
    {"XUserIsSignOutPresent", reinterpret_cast<void*>(&XUserIsSignOutPresent_impl)},
    {"XUserRegisterForChangeEvent", reinterpret_cast<void*>(&XUserRegisterForChangeEvent_impl)},
    {"XUserUnregisterForChangeEvent", reinterpret_cast<void*>(&XUserUnregisterForChangeEvent_impl)},
};

}  // namespace

uint64_t id_of(void* h) { return h ? from(h)->id : 0; }

const Override* overrides(size_t* count) {
  *count = sizeof(kOverrides) / sizeof(kOverrides[0]);
  return kOverrides;
}

}  // namespace gtx::xgr::user
