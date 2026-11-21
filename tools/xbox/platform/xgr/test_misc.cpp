#include <windows.h>

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

typedef HRESULT (*QueryApi)(const GUID&, const GUID&, void**);
struct AsyncBlock { void* queue; void* context; void (*callback)(AsyncBlock*); unsigned char internal[32]; };
struct Token { uint64_t value; };

static int g_failures = 0;
static void Check(bool ok, const char* what) {
  std::printf("%s %s\n", ok ? "PASS" : "FAIL", what);
  if (!ok) g_failures++;
}

static QueryApi g_query;
static std::string g_table;
static int TableSlot(const char* method) {
  std::string key = std::string("\"name\": \"") + method + "\",";
  size_t at = g_table.find(key);
  if (at == std::string::npos || g_table.find(key, at + 1) != std::string::npos) return -1;
  size_t slot = g_table.find("\"slot\": ", at);
  size_t next = g_table.find("\"name\": ", at + key.size());
  if (slot == std::string::npos || (next != std::string::npos && slot > next)) return -1;
  return std::atoi(g_table.c_str() + slot + 8);
}
static std::string TableUuid(const char* iface) {
  std::string key = std::string("\"name\": \"") + iface + "\",";
  size_t at = g_table.find(key);
  if (at == std::string::npos) return "";
  size_t uuid = g_table.find("\"uuid\": \"", at);
  size_t next = g_table.find("\"name\": ", at + key.size());
  if (uuid == std::string::npos || (next != std::string::npos && uuid > next)) return "";
  return g_table.substr(uuid + 9, 36);
}
static bool LoadTable() {
  std::ifstream file("table.json", std::ios::binary);
  if (!file) return false;
  std::ostringstream text;
  text << file.rdbuf();
  g_table = text.str();
  return !g_table.empty();
}
static std::string GuidText(const GUID& g) {
  char text[40];
  std::snprintf(text, sizeof(text), "%08lx-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x", static_cast<unsigned long>(g.Data1), g.Data2, g.Data3,
                g.Data4[0], g.Data4[1], g.Data4[2], g.Data4[3], g.Data4[4], g.Data4[5], g.Data4[6], g.Data4[7]);
  return text;
}

struct Api {
  void* self;
  void** slots;
  template <typename Fn> Fn At(int slot, const char* method) const {
    int expected = TableSlot(method);
    if (expected != slot) {
      std::printf("FAIL slot pin: %s is slot %d in table.json, the test uses %d\n", method, expected, slot);
      std::exit(1);
    }
    return reinterpret_cast<Fn>(slots[slot]);
  }
};
static Api Get(const char* iface, const GUID& iid) {
  if (TableUuid(iface) != GuidText(iid)) {
    std::printf("FAIL guid pin: %s is %s in table.json, the test uses %s\n", iface, TableUuid(iface).c_str(), GuidText(iid).c_str());
    std::exit(1);
  }
  void* object = nullptr;
  HRESULT hr = g_query(iid, iid, &object);
  if (FAILED(hr) || !object) {
    std::printf("FAIL QueryApiImpl %s %08lx\n", iface, static_cast<unsigned long>(hr));
    std::exit(1);
  }
  return {object, *static_cast<void***>(object)};
}

static const GUID kThreading = {0x073b7dcb, 0x1fcf, 0x4030, {0x94, 0xbe, 0xe3, 0xc9, 0xeb, 0x62, 0x34, 0x28}};
static const GUID kSystem3 = {0x67ce4bfc, 0xb1d1, 0x4ac7, {0xbc, 0x3a, 0xcb, 0x92, 0x19, 0xa9, 0x7a, 0x85}};
static const GUID kActivation = {0x2e4f76fe, 0x0fc7, 0x461e, {0xab, 0x4d, 0xa4, 0x49, 0x94, 0x34, 0xc3, 0xcf}};
static const GUID kInvite2 = {0x014d1cc3, 0xbcfe, 0x41ff, {0xb2, 0xf0, 0xe1, 0xef, 0x07, 0x15, 0x58, 0x28}};

constexpr uint32_t kManual = 0;
constexpr uint32_t kThreadPool = 1;
constexpr uint32_t kWork = 0;
constexpr uint32_t kCompletion = 1;

typedef void(CALLBACK* TaskCallback)(void*, bool);
struct Threading {
  Api api;
  HRESULT Create(uint32_t work, uint32_t completion, void** queue) const {
    return api.At<HRESULT (*)(void*, uint32_t, uint32_t, void**)>(12, "XTaskQueueCreate")(api.self, work, completion, queue);
  }
  HRESULT CreateComposite(void* work, void* completion, void** queue) const {
    return api.At<HRESULT (*)(void*, void*, void*, void**)>(13, "XTaskQueueCreateComposite")(api.self, work, completion, queue);
  }
  HRESULT GetPort(void* queue, uint32_t port, void** handle) const {
    return api.At<HRESULT (*)(void*, void*, uint32_t, void**)>(14, "XTaskQueueGetPort")(api.self, queue, port, handle);
  }
  bool Dispatch(void* queue, uint32_t port, uint32_t timeout) const {
    return api.At<bool (*)(void*, void*, uint32_t, uint32_t)>(16, "XTaskQueueDispatch")(api.self, queue, port, timeout);
  }
  void Close(void* queue) const { api.At<void (*)(void*, void*)>(17, "XTaskQueueCloseHandle")(api.self, queue); }
  HRESULT Submit(void* queue, uint32_t port, void* context, TaskCallback callback) const {
    return api.At<HRESULT (*)(void*, void*, uint32_t, void*, TaskCallback)>(18, "XTaskQueueSubmitCallback")(api.self, queue, port, context, callback);
  }
  HRESULT SubmitDelayed(void* queue, uint32_t port, uint32_t delay, void* context, TaskCallback callback) const {
    return api.At<HRESULT (*)(void*, void*, uint32_t, uint32_t, void*, TaskCallback)>(19, "XTaskQueueSubmitDelayedCallback")(
        api.self, queue, port, delay, context, callback);
  }
  HRESULT RegisterWaiter(void* queue, uint32_t port, HANDLE handle, void* context, TaskCallback callback, Token* token) const {
    return api.At<HRESULT (*)(void*, void*, uint32_t, HANDLE, void*, TaskCallback, Token*)>(20, "XTaskQueueRegisterWaiter")(
        api.self, queue, port, handle, context, callback, token);
  }
  void UnregisterWaiter(void* queue, Token token) const {
    api.At<void (*)(void*, void*, Token)>(21, "XTaskQueueUnregisterWaiter")(api.self, queue, token);
  }
  HRESULT Terminate(void* queue, bool wait) const {
    return api.At<HRESULT (*)(void*, void*, bool, void*, void*)>(22, "XTaskQueueTerminate")(api.self, queue, wait, nullptr, nullptr);
  }
  bool IsEmpty(void* queue, uint32_t port) const {
    return api.At<bool (*)(void*, void*, uint32_t)>(28, "XTaskQueueIsEmpty")(api.self, queue, port);
  }
  HRESULT WaitAsync(AsyncBlock* block) const {
    return api.At<HRESULT (*)(void*, AsyncBlock*, BOOLEAN)>(3, "XAsyncGetStatus")(api.self, block, TRUE);
  }
};
static Threading g_tq;

struct Counter {
  std::atomic<int> calls{0};
  std::atomic<int> canceled{0};
  void* queue = nullptr;
  uint32_t port = 0;
  std::atomic<int> sawNonEmpty{0};
  HANDLE gate = nullptr;
};
static void CALLBACK Count(void* context, bool canceled) {
  auto* c = static_cast<Counter*>(context);
  if (canceled) c->canceled++;
  c->calls++;
}
static void CALLBACK CountAndProbe(void* context, bool canceled) {
  auto* c = static_cast<Counter*>(context);
  if (!g_tq.IsEmpty(c->queue, c->port)) c->sawNonEmpty++;
  Count(context, canceled);
}
static void CALLBACK WaitForGate(void* context, bool canceled) {
  auto* c = static_cast<Counter*>(context);
  WaitForSingleObject(c->gate, 5000);
  Count(context, canceled);
}

static bool WaitEmpty(void* queue, uint32_t port, DWORD timeoutMs) {
  ULONGLONG until = GetTickCount64() + timeoutMs;
  while (!g_tq.IsEmpty(queue, port)) {
    if (GetTickCount64() > until) return false;
    Sleep(5);
  }
  return true;
}

static void CheckManualQueue() {
  void* q = nullptr;
  Check(g_tq.Create(kManual, kManual, &q) == S_OK && q, "XTaskQueueCreate makes a manual queue");
  Check(g_tq.IsEmpty(q, kWork) && g_tq.IsEmpty(q, kCompletion), "XTaskQueueIsEmpty is true on both ports of a new queue");

  Counter c;
  c.queue = q;
  c.port = kWork;
  g_tq.Submit(q, kWork, &c, CountAndProbe);
  Check(!g_tq.IsEmpty(q, kWork), "XTaskQueueIsEmpty is false for the work port while a callback waits for dispatch");
  Check(g_tq.IsEmpty(q, kCompletion), "XTaskQueueIsEmpty keeps the completion port empty when only work was queued");
  Check(g_tq.Dispatch(q, kWork, 0) && c.calls == 1, "XTaskQueueDispatch runs the queued work callback");
  Check(c.sawNonEmpty == 1, "XTaskQueueIsEmpty is false from inside the running callback");
  Check(g_tq.IsEmpty(q, kWork), "XTaskQueueIsEmpty is true again after the callback was dispatched");

  Counter d;
  d.queue = q;
  d.port = kCompletion;
  g_tq.Submit(q, kCompletion, &d, CountAndProbe);
  g_tq.Submit(q, kCompletion, &d, CountAndProbe);
  Check(!g_tq.IsEmpty(q, kCompletion) && g_tq.IsEmpty(q, kWork), "XTaskQueueIsEmpty tracks the completion port separately");
  g_tq.Dispatch(q, kCompletion, 0);
  Check(!g_tq.IsEmpty(q, kCompletion), "XTaskQueueIsEmpty stays false while one of two callbacks is left");
  g_tq.Dispatch(q, kCompletion, 0);
  Check(g_tq.IsEmpty(q, kCompletion) && d.calls == 2, "XTaskQueueIsEmpty is true once every completion callback ran");

  Counter delayed;
  g_tq.SubmitDelayed(q, kWork, 150, &delayed, Count);
  Check(!g_tq.IsEmpty(q, kWork), "XTaskQueueIsEmpty counts a delayed callback that is not due yet");
  Check(!g_tq.Dispatch(q, kWork, 0), "XTaskQueueDispatch has nothing to run before the delay passes");
  Check(g_tq.Dispatch(q, kWork, 5000) && delayed.calls == 1 && g_tq.IsEmpty(q, kWork),
        "XTaskQueueIsEmpty is true after the delayed callback was dispatched");

  Check(g_tq.IsEmpty(q, 2), "XTaskQueueIsEmpty reports an unknown port as empty");

  HANDLE never = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  Counter waited;
  Token token{};
  Check(g_tq.RegisterWaiter(q, kWork, never, &waited, Count, &token) == S_OK && g_tq.IsEmpty(q, kWork),
        "XTaskQueueIsEmpty does not count a registered waiter whose handle is unsignaled");
  g_tq.UnregisterWaiter(q, token);
  CloseHandle(never);
  g_tq.Close(q);
}

static void CheckTermination() {
  void* q = nullptr;
  g_tq.Create(kManual, kManual, &q);
  Counter c;
  g_tq.SubmitDelayed(q, kWork, 60000, &c, Count);
  Check(!g_tq.IsEmpty(q, kWork), "XTaskQueueIsEmpty counts a long-delayed callback");
  g_tq.Terminate(q, false);
  Check(!g_tq.IsEmpty(q, kWork), "XTaskQueueIsEmpty still counts the canceled callback until it is dispatched");
  while (g_tq.Dispatch(q, kWork, 1000)) {
  }
  Check(c.calls == 1 && c.canceled == 1 && g_tq.IsEmpty(q, kWork), "XTaskQueueIsEmpty is true once the canceled callback was delivered");
  g_tq.Close(q);
}

static void CheckComposite() {
  void* q = nullptr;
  g_tq.Create(kManual, kManual, &q);
  void* work = nullptr;
  void* completion = nullptr;
  g_tq.GetPort(q, kWork, &work);
  g_tq.GetPort(q, kCompletion, &completion);
  void* composite = nullptr;
  Check(g_tq.CreateComposite(work, completion, &composite) == S_OK, "XTaskQueueCreateComposite shares the ports");
  Counter c;
  g_tq.Submit(composite, kWork, &c, Count);
  Check(!g_tq.IsEmpty(q, kWork) && !g_tq.IsEmpty(composite, kWork), "XTaskQueueIsEmpty sees work on a shared port from both queues");
  g_tq.Dispatch(q, kWork, 0);
  Check(c.calls == 1 && g_tq.IsEmpty(q, kWork) && g_tq.IsEmpty(composite, kWork), "XTaskQueueIsEmpty is true on both queues after dispatch");
  g_tq.Close(composite);
  g_tq.Close(q);
}

static void CheckThreadPool() {
  void* q = nullptr;
  g_tq.Create(kThreadPool, kThreadPool, &q);
  Counter c;
  c.gate = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  g_tq.Submit(q, kWork, &c, WaitForGate);
  Sleep(20);
  Check(!g_tq.IsEmpty(q, kWork), "XTaskQueueIsEmpty is false while a thread-pool callback is running");
  SetEvent(c.gate);
  Check(WaitEmpty(q, kWork, 5000) && c.calls == 1, "XTaskQueueIsEmpty turns true only after the thread-pool callback returned");
  CloseHandle(c.gate);

  Counter many;
  std::vector<std::thread> submitters;
  for (int t = 0; t < 4; t++)
    submitters.emplace_back([&] {
      for (int i = 0; i < 250; i++) g_tq.Submit(q, kCompletion, &many, Count);
    });
  for (auto& t : submitters) t.join();
  Check(WaitEmpty(q, kCompletion, 10000) && many.calls == 1000, "XTaskQueueIsEmpty stays consistent with 1000 callbacks submitted from four threads");
  g_tq.Close(q);
}

static void CheckStillFailing() {
  Api system3 = Get("IXSystemImpl3", kSystem3);
  Check(system3.At<HRESULT (*)(void*, void*, void*)>(6, "XSystemHandleTrack")(system3.self, nullptr, nullptr) == E_NOTIMPL, "XSystemHandleTrack stays failing");
  void* q = nullptr;
  g_tq.Create(kManual, kManual, &q);
  Check(!system3.At<BOOLEAN (*)(void*, void*)>(7, "XSystemIsHandleValid")(system3.self, q), "XSystemIsHandleValid stays unimplemented (stub answers false)");
  g_tq.Close(q);
  Api activation = Get("IXGameActivationImpl", kActivation);
  Check(activation.At<HRESULT (*)(void*, const char*)>(5, "XGameActivationAcceptPendingInvite")(activation.self, "ms-xbl-0://invite") == E_NOTIMPL,
        "XGameActivationAcceptPendingInvite stays failing");
  Api invite = Get("IXGameInviteImpl2", kInvite2);
  Check(invite.At<HRESULT (*)(void*, const char*)>(7, "XGameInviteAcceptPendingInvite")(invite.self, "ms-xbl-0://invite") == E_NOTIMPL,
        "XGameInviteAcceptPendingInvite stays failing");
}

int main() {
  HMODULE module = LoadLibraryA("xgameruntime.dll");
  if (!module) {
    std::printf("FAIL LoadLibrary\n");
    return 1;
  }
  g_query = reinterpret_cast<QueryApi>(GetProcAddress(module, "QueryApiImpl"));
  if (!LoadTable()) {
    std::printf("FAIL table.json not found next to the test (run it from the build directory)\n");
    return 1;
  }
  g_tq.api = Get("IXThreadingImpl", kThreading);
  CheckManualQueue();
  CheckTermination();
  CheckComposite();
  CheckThreadPool();
  CheckStillFailing();
  std::printf("%s: %d failure(s)\n", g_failures ? "FAILED" : "ALL PASSED", g_failures);
  return g_failures ? 1 : 0;
}
