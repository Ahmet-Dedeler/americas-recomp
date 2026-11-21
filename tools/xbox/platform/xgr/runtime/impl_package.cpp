#define GTX_SHIM_DLL "xgameruntime.dll"
#include <windows.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "xgr_internal.h"

namespace gtx::xgr::package {
namespace {

std::string config_text() {
  char p[MAX_PATH * 4];
  DWORD n = GetModuleFileNameA(nullptr, p, sizeof(p));
  if (!n || n >= sizeof(p)) return {};
  std::string path(p, n);
  size_t cut = path.find_last_of("\\/");
  path = (cut == std::string::npos ? std::string() : path.substr(0, cut + 1)) + "MicrosoftGame.Config";
  HANDLE f = CreateFileA(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
  if (f == INVALID_HANDLE_VALUE) return {};
  std::string text;
  char buf[4096];
  DWORD got = 0;
  while (ReadFile(f, buf, sizeof(buf), &got, nullptr) && got) text.append(buf, got);
  CloseHandle(f);
  return text;
}

std::vector<std::string> title_languages() {
  std::vector<std::string> out;
  std::string t = config_text();
  size_t sec = t.find("<Resources>");
  size_t end = sec == std::string::npos ? sec : t.find("</Resources>", sec);
  for (size_t i = sec; i != std::string::npos && i < end;) {
    i = t.find("Language=\"", i);
    if (i == std::string::npos || i > end) break;
    i += 10;
    size_t q = t.find('"', i);
    if (q == std::string::npos) break;
    out.push_back(t.substr(i, q - i));
    i = q + 1;
  }
  return out;
}

std::vector<std::string> user_languages() {
  std::vector<std::string> out;
  ULONG count = 0, len = 0;
  if (!GetUserPreferredUILanguages(MUI_LANGUAGE_NAME, &count, nullptr, &len) || !len) return out;
  std::vector<wchar_t> buf(len);
  if (!GetUserPreferredUILanguages(MUI_LANGUAGE_NAME, &count, buf.data(), &len)) return out;
  for (const wchar_t* s = buf.data(); *s; s += wcslen(s) + 1) {
    char a[LOCALE_NAME_MAX_LENGTH * 2] = {};
    WideCharToMultiByte(CP_UTF8, 0, s, -1, a, sizeof(a), nullptr, nullptr);
    out.push_back(a);
  }
  return out;
}

std::string primary(const std::string& s) { return s.substr(0, s.find('-')); }

std::string choose() {
  const char* e = std::getenv("XGR_LOCALE");
  if (e && *e) return e;
  std::vector<std::string> title = title_languages(), user = user_languages();
  if (title.empty()) {
    wchar_t w[LOCALE_NAME_MAX_LENGTH] = {};
    char a[LOCALE_NAME_MAX_LENGTH * 2] = {};
    if (GetUserDefaultLocaleName(w, LOCALE_NAME_MAX_LENGTH)) WideCharToMultiByte(CP_UTF8, 0, w, -1, a, sizeof(a), nullptr, nullptr);
    return a;
  }
  for (const auto& u : user)
    for (const auto& t : title)
      if (!_stricmp(u.c_str(), t.c_str())) return t;
  for (const auto& u : user)
    for (const auto& t : title)
      if (!_stricmp(primary(u).c_str(), primary(t).c_str())) return t;
  return title.front();
}

uint8_t XPackageIsPackagedProcess_impl(void*) { return 1; }

HRESULT XPackageGetUserLocale_impl(void*, size_t size, char* locale) {
  if (!locale) return E_INVALIDARG;
  std::string l = choose();
  if (l.empty()) return E_FAIL;
  if (size < l.size() + 1) return HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER);
  std::memcpy(locale, l.c_str(), l.size() + 1);
  return S_OK;
}

const Override kOverrides[] = {
    {"XPackageIsPackagedProcess", reinterpret_cast<void*>(&XPackageIsPackagedProcess_impl)},
    {"XPackageGetUserLocale", reinterpret_cast<void*>(&XPackageGetUserLocale_impl)},
};

}  // namespace

const Override* overrides(size_t* count) {
  *count = sizeof(kOverrides) / sizeof(kOverrides[0]);
  return kOverrides;
}

}  // namespace gtx::xgr::package
